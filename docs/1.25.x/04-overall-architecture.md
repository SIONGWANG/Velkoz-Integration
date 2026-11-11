# 审视之眼 Pro 1.25.x 总体架构设计

> 适用版本：v1.25.0 起
> 设计目标：在**不破坏现有质检核心**的前提下，增量建立数据验收管理平台
> 代码基线：`b97e0df` / `v1.24.0`

---

## 1. 架构原则

1. **保护核心**：现有图片质检、查看器、快捷键、截图、标签、备注、查询、导出不重写。
2. **增量分层**：新能力放独立模块，旧 Mixin 只做“适配/调用”，不把新逻辑塞回 `app.py`。
3. **只读优先**：扫描、格式检查、导入、统计默认只读；复制/写入必须显式确认。
4. **CSV 仍是权威**：v1.25.x 期间验收记录以 CSV 为准，数据库是派生索引。
5. **本地优先**：默认单机 SQLite，不需要服务器/MySQL；未来再评估共享模式。
6. **失败可恢复**：每个批量操作有预览、校验、日志、断点/回滚。
7. **可测试**：新模块的纯逻辑与 UI 分离，能脱离 Streamlit 单测。
8. **兼容旧版**：不要求其他验收员升级，不修改旧 CSV 字段，不改变图片/TXT 命名。

---

## 2. 现状架构

```text
┌────────────────────────────────────────────────────────────┐
│ Streamlit 进程                                             │
│                                                            │
│  app.py: AcceptanceApp(6 Mixins)                           │
│   ├─ DataMixin       CSV/导航/扫描触发/上传消费            │
│   ├─ SettingsMixin   设置/数据源/扫描规则/查看器入口      │
│   ├─ ExportMixin     导出/归集/Excel                       │
│   ├─ ZoneAMixin      A 区 UI                               │
│   ├─ ZoneBMixin      B 区 UI                               │
│   └─ ZoneCMixin      C 区 UI                               │
│                                                            │
│  st.session_state（全局事实状态）                          │
│   ├─ data_groups / current_id / search_*                   │
│   ├─ scan_rules / categories_config / selected_tags        │
│   └─ 各类缓存                                              │
└───────────────┬──────────────────────────────┬─────────────┘
                │                              │
        CSV / 文件夹                    image_viewer 子进程
        config/*.json                    viewer/ 进程内 Qt
        scan_rules.json                  （两套查看器并存）
```

问题：

- 页面编排与业务混合；
- 状态无边界；
- 新功能没有落点；
- 扫描规则是全局单例；
- 配置写程序目录；
- 发布清单与代码脱节。

---

## 3. 目标架构（1.25.x）

```text
┌──────────────────────────────────────────────────────────────────┐
│ 表现层（Streamlit）                                              │
│  app.py（组合根，保持轻量）                                      │
│   └─ ui/router.py  页面路由                                      │
│      ├─ pages/workbench.py          工作台                        │
│      ├─ pages/supplier.py           供应商提交                    │
│      ├─ pages/scan.py               数据扫描                      │
│      ├─ pages/batches.py            批次管理                      │
│      ├─ pages/format_check.py       格式检查                      │
│      ├─ pages/precheck.py           翻译与预审                    │
│      ├─ pages/acceptance.py         正式质检（原三栏，不变）      │
│      ├─ pages/records.py            验收记录                      │
│      ├─ pages/archive.py            合格归档                      │
│      ├─ pages/statistics.py         数据统计                      │
│      └─ pages/settings.py           系统设置                      │
├──────────────────────────────────────────────────────────────────┤
│ 应用服务层（modules/*/service.py）                               │
│  scanning | batch_management | supplier_management               │
│  format_checker | precheck | record_import | archive             │
│  statistics | audit_log                                          │
├──────────────────────────────────────────────────────────────────┤
│ 领域/纯逻辑层                                                    │
│  rules（目录/命名/模板） | matcher | checker | csv_schema         │
│  report | fingerprint | path_guard | state_machine               │
├──────────────────────────────────────────────────────────────────┤
│ 基础设施层                                                       │
│  database（SQLite，本地数据目录）                                │
│  fs（只读扫描/复制/校验） | config | logging | secrets(DPAPI)     │
├──────────────────────────────────────────────────────────────────┤
│ 旧核心（保持兼容）                                               │
│  data_mixin / settings_mixin / export_mixin / zone_a/b/c         │
│  disk_io / utils / excel_importer / search_service               │
│  viewer/ | image_viewer/                                         │
└──────────────────────────────────────────────────────────────────┘
```

### 3.1 关键边界

| 层 | 可以做 | 不可以做 |
|---|---|---|
| 表现层 | 收集输入、展示结果、调用 service | 直接读写 CSV/文件；直接拼 SQL |
| 应用服务层 | 编排流程、事务、日志 | 依赖 Streamlit；直接画 UI |
| 领域层 | 纯函数/规则/状态机 | 文件 IO（除纯工具） |
| 基础设施层 | 文件系统、数据库、配置 | 业务判断 |
| 旧核心 | 保持原行为 | 被新模块反向依赖 |

### 3.2 依赖规则

```text
pages → modules/*/service → modules/*/domain → infra
pages → 旧核心（仅通过明确适配器）
旧核心 ✗→ modules（避免循环依赖）
modules ✗→ app.py / *_mixin.py / zone_*.py
```

例外：`format_checker/ui.py` 是 Streamlit 页面，可依赖 `format_checker/service.py`，但不得依赖旧 Mixin。

---

## 4. 页面路由与兼容

### 4.1 路由方案

保留 `app.py` 作为唯一入口，新增 `ui/router.py`：

```python
def render():
    page = st.session_state.get("nav_page", "acceptance")
    if page == "acceptance":
        legacy_app.run_acceptance()   # 现有三栏，原样
    elif page == "format_check":
        from modules.format_checker.ui import render
        render()
    ...
```

方式选择：

- 优先 `st.navigation`（Streamlit 新版）；
- 若内嵌版本不支持，用 `st.sidebar.radio`；
- 不强制迁移到 `pages/` 目录（避免 Streamlit 自动多页与现有状态冲突）。

### 4.2 兼容要求

1. 默认页仍是“正式质检”；
2. 旧 URL/启动方式不变；
3. 旧 `st.session_state` 键不变；
4. 新页面不修改 `data_groups`/`current_id`，除非用户明确“带到正式质检”；
5. 关闭/隐藏新模块时，旧功能完全可用；
6. 任何新模块 import 失败不能导致主程序起不来（延迟 import + 友好提示）。

### 4.3 主界面不挤压

- 正式质检页保持 A/B/C 三栏宽度逻辑（`layout_width`/`layout_height`）；
- 新页面使用全宽独立布局，不复用三栏；
- 导航放侧边栏/顶部，不占用 B 区。

---

## 5. 模块清单与落点

### 5.1 物理目录（逐步创建，不一次到位）

```text
modules/
├── common/
│   ├── path_guard.py       # 路径规范化/越界检查/长路径
│   ├── fingerprint.py      # 文件/目录快照与哈希
│   ├── fs_readonly.py      # 只读遍历封装
│   ├── timeutil.py
│   └── result.py           # 统一 Result/错误
├── database/
│   ├── connection.py       # SQLite 连接、WAL、busy_timeout
│   ├── migrations/         # 版本化迁移
│   ├── backup.py           # 备份/恢复/校验
│   └── repositories/       # 按聚合拆 repository
├── config/
│   ├── app_config.py       # 配置优先级/读写
│   ├── secrets.py          # DPAPI（仅本机）
│   └── defaults/
├── scanning/
│   ├── rules.py            # 逐层规则模型
│   ├── matcher.py          # 层级匹配 + 原因
│   ├── scanner.py          # 只读扫描
│   ├── diagnostics.py      # 诊断/解释
│   └── service.py
├── batch_management/
├── supplier_management/
├── format_checker/         # 见 03-format-checker-design.md
├── precheck/                # 原生仿照豆包 AI 预审工具（见 02 文档方案 A）
│   ├── models.py
│   ├── pairing.py          # _CH/_EN 配对、缺失/已翻译状态
│   ├── providers/          # Ark / DashScope / OpenAI 兼容
│   ├── prompts.py          # 翻译/语法/一致性 Prompt（可编辑、可恢复默认）
│   ├── service.py          # 批量、并发、重试、取消、进度
│   ├── safety.py           # 写前备份、原子写入、失败不覆盖
│   ├── cost.py             # Token/费用账本
│   ├── report.py           # 翻译报告 Excel/CSV
│   ├── secrets.py          # API Key DPAPI 加密
│   ├── report_import.py    # 原工具历史报告只读导入（兼容入口）
│   └── ui.py               # 翻译与预审页面
├── record_import/
│   ├── discover.py         # 只读发现 CSV/Excel
│   ├── classify.py         # CSV 类型识别
│   ├── dedup.py            # 文件/行指纹
│   └── service.py
├── archive/
├── stats/
├── workspace/              # v1.25.8 业务目录、双来源发现、批次工作台
│   ├── models.py
│   ├── project_roots.py    # 多项目待验/申请终验目录配置
│   ├── discovery.py        # 双来源增量发现
│   ├── repository.py       # 扫描记录与批次快照
│   └── service.py
├── audit_log/
└── ui/
    ├── router.py
    ├── components/
    └── pages/
```

**原则**：目录可以后建，职责先定；每个版本只落地当前需要的模块，不创建空壳。

### 5.2 逻辑模块职责

| 模块 | 职责 | 首个版本 |
|---|---|---|
| `scanning` | 多规则、逐层识别、预览、诊断、持久化 | v1.25.0–1.25.1 |
| `batch_management` | 批次/子批次、状态、合并拆分 | v1.25.1/1.25.5 |
| `supplier_management` | 供应商提交登记 | v1.25.5 |
| `format_checker` | 格式检测 | v1.25.2 |
| `precheck` | 原生翻译/语法/一致性检查/费用/报告 + 历史报告只读导入 | v1.25.3 |
| `record_import` | 其他验收员记录只读导入 | v1.25.4 |
| `archive` | 合格数据只复制归档 | v1.25.6 |
| `stats` | 统计/日报/可追溯明细/Excel | v1.25.7 |
| `workspace` | 多项目业务目录、待验/申请终验双来源发现、批次工作台与来源关联 | v1.25.8 |
| `database` | SQLite/迁移/备份 | v1.25.0 |
| `config` | 配置/密钥 | v1.25.0 |
| `audit_log` | 结构化操作日志 | v1.25.0 |

---

## 6. 数据流

### 6.1 主流程

```text
供应商提交登记
  → 数据下载到 root
  → 格式检查（只读，可跳过）→ 检查记录
  → 扫描（只读）→ 候选批次
  → 人工确认 → 批次入库
  → 翻译/预审（原生 precheck；可选只读导入外部历史报告）
  → 正式质检（原 CSV 流程）
  → 验收记录（CSV 权威 + DB 索引）
  → 合格归档（只复制 + 校验）
  → 统计/日报
```

### 6.2 每次写入的边界

| 操作 | 源目录 | CSV | 数据库 | 报告目录 |
|---|---|---|---|---|
| 扫描 | 只读 | 不写 | 写扫描记录 | 可选 |
| 格式检查 | 只读 | 不写 | 写检查记录 | 写报告 |
| 翻译（原生） | 确认后写 EN TXT（写前备份、原子写） | 不写 | 写翻译/费用记录 | 写翻译报告 |
| 翻译（历史导入） | 只读 | 不写 | 写导入记录 | 只读原报告 |
| 正式质检保存 | 可写 TXT（保持现状，用户已确认） | 写 | 写事件索引 | 写截图 |
| 记录导入 | 只读 | 不写 | 写导入索引 | 可选 |
| 归档 | 只读 | 不写 | 写归档记录 | 写日志 |
| 统计 | 只读 | 只读 | 只读/写缓存 | 写导出 |

> 表中“可写 TXT”是现状行为，用户已确认**保持现状**（见 `00-decisions.md` D-002）；新增模块不得改变它，只记录日志与索引。
> 新模块（格式检查、扫描、导入、原生预审）默认只读；只有用户显式确认后才写英文 TXT，且写前备份。

---

## 7. 状态与并发

### 7.1 状态分层

- **页面状态**：`st.session_state`，只放 UI 临时状态；
- **持久状态**：SQLite + 文件；
- **派生状态**：扫描/检查/统计数据，可重建；
- **权威记录**：CSV（v1.25.x）。

### 7.2 并发

- 单机单用户默认；
- SQLite `WAL` + `busy_timeout`；
- 长任务（扫描/检查/归档）使用后台线程/进程，UI 轮询进度；
- 同一批次同一时间只允许一个写操作（应用层锁 + DB 唯一约束）；
- 不承诺 SMB 多端并发写（见 `05-database-design.md`）。

### 7.3 中断恢复

- 扫描/检查：结果分批提交，中断保留已完成部分；
- 归档：`archive_jobs` + `archive_items` 记录每个文件状态，支持断点；
- 导入：文件指纹 + 行指纹，重复导入幂等；
- 数据库迁移：事务 + 备份 + 失败回滚。

---

## 8. 配置优先级

```text
命令行/环境变量（仅开发）
  > 用户设置（数据库 app_settings）
  > 用户配置文件（config/*.json）
  > 模块默认值（代码）
  > 内置模板
```

规则：

- 新配置写数据库/用户数据目录，不写程序目录；
- 旧 `config/settings.json`、`scan_rules.json` 继续读取，首次启动迁移为“默认方案/默认设置”，但保留原文件；
- 密钥单独存储（DPAPI），不进数据库明文、不进日志、不进报告。

---

## 9. 日志与可观测性

### 9.1 结构化操作日志

`operation_logs`：

- 操作人、时间、模块、动作；
- 批次/任务/路径；
- 源路径、目标路径；
- 结果、错误码、错误信息；
- 耗时；
- 关联 run_id/job_id。

### 9.2 应用日志

- 保留 `app_error.log`；
- 新模块用 `logging.getLogger("velkoz.<module>")`；
- 不记录 API Key、用户文本内容、完整 CSV 行；
- 日志轮转，避免无限增长。

### 9.3 诊断包

设置页提供“导出诊断信息”：

- 版本、依赖、数据库 schema 版本；
- 最近错误摘要；
- 不包含用户数据/密钥/完整路径（可脱敏）。

---

## 10. 安全与数据保护

| 风险 | 措施 |
|---|---|
| 路径穿越 | `path_guard` 规范化 + 根目录校验 + 拒绝 `..` |
| 误删源 | 新增模块禁止 `rmtree(src)`；旧“不合格归集”保持现状（用户已确认），删除需独立授权 |
| 误覆盖 | 复制前冲突检测；覆盖需确认；备份 |
| 密钥泄露 | DPAPI/本机存储；UI 掩码；日志过滤 |
| CSV 注入 | 导出时转义公式前缀（`= + - @`） |
| 长路径/UNC | 统一 `\\?\` 处理；超时/断线重试 |
| 符号链接 | 默认不跟随 |
| 其他验收员数据 | 只读导入；保留原始标签/备注；不改原文件 |
| 数据库损坏 | 自动备份 + `PRAGMA integrity_check` + 恢复 |

---

## 11. 部署与打包

### 11.1 运行形态

- 继续绿色版：内嵌 Python + Streamlit；
- 不需要安装服务/数据库；
- 默认本机访问 `localhost`。

### 11.2 打包改造（暂缓，用户已确认）

> **本轮忽略打包问题**，不作为 v1.25.0 阻塞项；以下为后续版本待办，保留作为审计结论。

1. 用 manifest/文件清单驱动，不手写 `copy`；
2. 必须包含：
   - `image_viewer/`、`excel_importer.py`、`search_service.py`、`src/`；
   - `modules/`、`ui/`；
   - 依赖清单；
3. 排除：
   - `config/settings.json`、`config/tags.json` 的真实值；
   - `__pycache__`、`.pyc`、`.bak`、用户数据、测试 fixture；
4. 配置模板化：`*.example.json`，首次启动生成用户配置；
5. 单一版本源：`APP_VERSION` + 构建时注入；
6. 干净目录冒烟：
   - 启动；
   - 加载 fixture；
   - 打开查看器；
   - 快速查询；
   - 格式检查；
   - 导出。

### 11.3 数据目录

```text
D:\VelkozDATA\
├── data\management.db
├── backups\
├── reports\
│   ├── format_check\
│   ├── translation\
│   └── statistics\
├── logs\
└── secrets\        # DPAPI 加密文件（如需要）
```

用户可在设置中改数据目录；程序目录只放代码/内置模板。

---

## 12. 迁移路线（增量，不推倒）

```text
v1.25.0  基础设施 + 规则模型 + 扫描诊断
   │
v1.25.1  候选批次确认 + 持久化 + 差异
   │
v1.25.2  格式检查模块
   │
v1.25.3  翻译/预审适配
   │
v1.25.4  验收记录导入
   │
v1.25.5  批次管理 + 工作台
   │
v1.25.6  归档
   │
v1.25.7  统计/报表
```

每个版本：

- 只迁移一个职责；
- 旧接口保持；
- 新接口有测试；
- 可独立回滚；
- 不要求用户一次性接受全部新页面。

---

## 13. 架构验收标准

v1.25.x 完成时，应满足：

1. 旧版启动方式、快捷键、查看器、查询、导出全部可用；
2. 旧 CSV/配置/规则/截图可读；
3. 新模块不 import Streamlit 也能单测；
4. 扫描/检查/导入/归档的源目录零修改（或显式确认）；
5. 每个批量操作有预览、日志、失败恢复；
6. 数据库可备份/恢复/迁移；
7. 格式检测规则可配置、可解释、可导出；
8. 其他验收员继续用旧版不受影响；
9. ~~打包产物在干净目录可冷启动~~（**暂缓**，开发环境可运行即可）；
10. 每个版本有测试报告和回滚说明。

---

## 14. 明确不做的架构变更

- 不引入 MySQL/Redis/消息队列；
- 不把 Streamlit 改成 FastAPI + 前端（本期）；
- 不重写现有 CSV 写入；
- 不合并两套 Qt 查看器（本期只做懒启动）；
- 不把图片存入数据库；
- 不改变图片/TXT 命名规则；
- 不强制其他验收员升级。
