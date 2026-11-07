# 现有项目架构审计报告

> 审计对象：`Velkoz-Integration` / 审视之眼 Pro
> 代码基线：`b97e0df`，tag `v1.24.0`
> 审计日期：2026-11-07
> 审计性质：只读架构审计，不是重构方案，不是验收报告

---

## 1. 执行摘要

### 1.1 一句话结论

审视之眼 Pro 目前是一个**以 Streamlit 单页应用为外壳、以 `st.session_state` 为全局状态、以 CSV 和文件夹为数据库、由 6 个 Mixin 拼装的图片质检工作台**。它的质检主链路（加载 → 查看 → 标注 → 保存 → 查询 → 导出）已经稳定，但数据边界、发布清单、扫描规则持久化和自动化测试仍然是薄弱环节。

1.25.x 的目标不是替换它，而是在它旁边增量建立“管理平台层”。因此：

- **质检核心保持原样**；
- **新能力放进独立模块**；
- **旧 CSV、旧配置、旧截图、旧扫描规则必须继续可读**；
- **数据库只做索引，不做原始文件仓库**。

### 1.2 与历史审计的关系

`docs/architecture-audit.md` 的基线是 `v1.22.4-dirty`。本次没有直接沿用其结论，而是重新核对当前 `v1.24.0` 代码。核对结果：

- 历史审计提出的 P0/P1 风险大部分**仍然存在**；
- 新增了 v1.23.0 快速查询导航、v1.24.0 锁定视图/异步加载；
- 扫描规则从历史审计记录的“6 位数字 + 4 图槽 + `_ZH`”变成了当前“8 位数字 + 5 图槽 + `_CH`”，证明**规则漂移是真实风险**；
- 打包脚本仍停留在 `1.21.3`，且仍会打包真实 `config/`。

---

## 2. 当前仓库与运行形态

### 2.1 版本与 Git 状态

| 项目 | 当前值 |
|---|---|
| 应用版本 | `utils.APP_VERSION = "1.24.0"` |
| Git HEAD | `b97e0df v1.24.0: 图片查看器锁定视图与大图异步加载` |
| Tag | `v1.24.0`（已推送） |
| 分支状态 | detached HEAD |
| 未跟踪文档 | `docs/4879.docx`、`docs/architecture-audit.md`、`docs/quick-query-navigation-plan.md` |
| 工作区噪音 | 大量 `python/**/__pycache__/*.pyc` 显示 modified |
| 运行方式 | 内嵌 Python 3.11 + Streamlit，`启动.bat` 自动找 8502 起的空闲端口 |

### 2.2 顶层目录

```text
Velkoz-Integration1.21.3/
├── app.py                  # 入口 + 组合根 + 主布局
├── data_mixin.py           # CSV/状态/导航/扫描触发
├── settings_mixin.py       # 设置、数据源、扫描规则、查看器入口
├── export_mixin.py         # 导出、归集、Excel
├── export_utils.py         # 导出纯计算逻辑（含删除源目录）
├── zone_a.py               # A 区：指标/筛选/搜索/列表
├── zone_b.py               # B 区：图片/文本/AI 预识别/完成页
├── zone_c.py               # C 区：截图/分类/状态/备注/保存
├── disk_io.py              # 扫描、图片显示缓存、预加载
├── utils.py                # 常量、扫描规则、图片工具、姓名提取
├── excel_importer.py       # 历史质检 Excel 恢复
├── search_service.py       # 快速查询纯函数
├── viewer/                 # ImageDock 进程内 Qt 悬浮窗
├── image_viewer/           # 独立 Qt 图片查看器（子进程）
├── src/core/               # 插件抽象骨架（未接线）
├── config/                 # settings.json / tags.json（含真实用户值）
├── scan_rules.json         # 当前扫描规则（含真实规则）
├── categories_config.json  # 当前为空 {"L1": [], "L2": {}}
├── docs/                   # 历史审计与用户资料
├── specs/                  # 旧版规格/手册
├── python/                 # 内嵌 Python 运行时（已跟踪）
└── 启动.bat / 打包.bat / setup_embedded_python.bat
```

---

## 3. 运行时架构

### 3.1 组合根

```python
# app.py:38
class AcceptanceApp(DataMixin, SettingsMixin, ExportMixin,
                    ZoneAMixin, ZoneBMixin, ZoneCMixin):
```

`app.py:101-284` 的 `run()` 负责：

1. 轮询独立查看器上传的截图；
2. 注入 CSS / 文本框高度脚本 / 快捷键；
3. 计算三栏宽度（A/B/C）；
4. A 区：指标、筛选、搜索、列表、抽检、导出、AI 预识别、更多设置、分类管理；
5. B 区：冷启动数据源加载 / 图片与文本 / 标注员确认 / 完成页；
6. C 区：截图、分类、状态、备注、保存、导航。

**结论**：`app.py` 是页面编排器，不是业务实现。新增管理平台页面时，不能继续把所有逻辑塞进 `run()`。

### 3.2 状态模型

- 几乎所有运行时状态都在 `st.session_state`：
  - `data_groups`：当前扫描出的数据组；
  - `current_id`：当前组 ID；
  - `search_results` / `search_result_ids` / `search_active`：快速查询；
  - `selected_tags` / `custom_tags`：标签；
  - `scan_rules` / `_sr_working`：扫描规则；
  - `categories_config` / `sticky_l1` / `sticky_l2`：分类；
  - `_df_cache_*` / `_df_lookup_*`：CSV 缓存。
- 没有独立的应用状态层，也没有事件总线。
- 数据组是 `dict`，CSV 行是隐式领域模型。

**影响**：新模块不能假设自己有独立进程或独立状态；必须通过显式参数/返回值与 Streamlit 状态交互，避免继续扩大 `session_state` 的隐式耦合。

### 3.3 持久化路径

| 数据 | 路径 | 代码 |
|---|---|---|
| 质检记录 CSV | `{root_path}/_质检记录/{operator}/{task}_{operator}_{date}_验收记录.csv` | `data_mixin.py:586-611` |
| 截图 | `{root_path}/_00_Evidence_{task}/{id}_{ts}_{i}.png` | `data_mixin.py:520-541, 634-669` |
| 应用设置 | `{BASE_DIR}/config/settings.json` | `settings_mixin.py:56-110` |
| 标签库 | `{BASE_DIR}/config/tags.json` | `settings_mixin.py:28-54` |
| 扫描规则 | `{BASE_DIR}/scan_rules.json` | `utils.py:50-87` |
| 分类配置 | `{BASE_DIR}/categories_config.json` | `utils.py:327-366` |
| 查看器设置 | `settings.json` 的 `image_viewer` + `~/.velkoz_image_viewer/` | `image_viewer/config.py` |
| ImageDock 设置 | `~/.imagedock_settings.json` | `viewer/*` |

**风险**：配置写在程序目录，升级/换目录会丢；管理数据库不能沿用这一做法（见 `05-database-design.md`）。

### 3.4 CSV 记录模型

`data_mixin.py:674-685` 的 `record`：

| 列 | 含义 | 兼容要求 |
|---|---|---|
| 姓名 | 数据组推断的标注员/验收组 | 保留原文本 |
| 图片ID | 当前唯一键 | **不能删改**；新库要用“批次+ID”复合身份 |
| 一级 | 分类 L1 | 允许空 |
| 二级 | 分类 L2 | 允许空 |
| 结果 | 合格/修改后合格/不合格/待定 | 枚举不能随意扩展 |
| 备注 | 人工备注 | 保留原文本 |
| 标签 | 分号分隔的多标签 | 保留原始标签文本 |
| 错误截图 | 分号分隔的相对路径 | 兼容旧路径解析 |
| 质检时间 | `YYYY-MM-DD HH:MM:SS` | 保留 |
| 路径 | 数据组根目录 | 保留 |

`save_to_disk()` 使用 `图片ID` 去重并保留最后一条（`data_mixin.py:687-712`），`_save_df()` 使用临时文件 + fsync + `os.replace` + `.bak`（`data_mixin.py:100-132`）。**这是现有最可靠的数据写入路径，不能重写。**

---

## 4. 模块职责与边界

| 模块 | 职责 | 对外接口 | 主要风险 |
|---|---|---|---|
| `app.py` | 组合根、页面编排 | `AcceptanceApp.run()` | 继续膨胀；新页面容易塞进 `run()` |
| `data_mixin.py` | CSV 读写、导航、扫描触发、查看器上传消费 | `save_to_disk`、`get_csv_filename`、`_handle_scanning` | 跨天写旧 CSV；TXT 写回源目录 |
| `settings_mixin.py` | 设置、数据源加载、扫描规则 UI、查看器入口 | `_render_settings_panel`、`_render_datasource_loader` | 设置写程序目录；规则 UI 与扫描器耦合 |
| `export_mixin.py` | 不合格/合格导出、Excel | `export_reject_data`、`export_qualifies_with_check` | 复制前 `rmtree` 目标；无断点恢复 |
| `export_utils.py` | 导出纯逻辑 | `collect_reject_folders`、`copy_qualified_folders`、`verify_exported_data` | `collect_reject_folders` 复制后 `rmtree` 源 |
| `zone_a/b/c.py` | 三栏 UI | `render_*` | UI 与业务混合，难单测 |
| `disk_io.py` | 扫描、显示图缓存、预加载 | `scan_files_from_disk`、`get_display_image_bytes` | 扫描无诊断；`ensure_batch_metadata` 写源目录（当前未被调用） |
| `utils.py` | 常量、规则编译、图片工具、姓名推断 | 纯函数 | 默认值与用户 `scan_rules.json` 可能漂移 |
| `excel_importer.py` | Excel 预览/恢复 | `parse_excel`、`build_import_preview`、`apply_restore` | 内嵌环境缺 `openpyxl`（已确认） |
| `search_service.py` | 快速查询 | `search_items` | 纯函数，可单测 |
| `viewer/` | 进程内 Qt 悬浮窗 | `get_sync_manager` | 未打开也会启动并加载整组图 |
| `image_viewer/` | 独立 Qt 查看器 | `open_image_viewer` 等 | 与 `viewer/` 两套逻辑并存 |
| `src/core/` | 插件抽象 | 无运行链路 import | 未接线骨架 |

### 4.1 扫描模型（现状）

`disk_io.py:22-132`：

1. `os.walk` 遍历根目录；
2. 跳过 `_00_Evidence_*` 与名为“不合格”的目录；
3. 识别 `batch_YYYYMMDD_NNN_keyword` 批次目录，保存 `current_batch`；
4. 文件夹名匹配 `folder_pattern` 才算“组”；
5. 每个 `image_slots` 按后缀顺序找图（大小写不敏感，可限扩展名）；
6. 每个 `text_slots` 按后缀找 `.txt`；
7. **至少一张图**才成为一个 group；
8. 从路径段推断 `user_name`；
9. 按 `root` 排序返回。

当前用户规则（`scan_rules.json`）：

- 文件夹：8 位纯数字；
- 图片槽位：`""`、`_3`、`_result`、`_1`、`_2`；
- 文本槽位：`_CH`、`_EN`。

**缺口**（相对 1.25.x）：

- 没有规则方案/版本；
- 没有逐层目录校验；
- 没有扫描预览、诊断、原因解释；
- 没有候选批次确认；
- 没有扫描结果持久化；
- 没有多根目录；
- 没有“同 ID 不同批次”身份区分。

---

## 5. 已确认风险清单

> 级别依据：P0 = 可能导致数据错误/丢失或无法交付；P1 = 重要但可绕过；P2 = 维护性问题。

### P0-1 跨天新记录写入旧 CSV

- **位置**：`data_mixin.py:586-611`、`:100-132`、`:634-742`
- **机制**：`get_csv_filename()` 在没有当天文件时回退到最近历史 CSV；写入路径复用同一函数。
- **影响**：跨天后第一条记录写进上一天文件；日期、操作员、任务归属失真；多人共用 UNC 时可能互相覆盖。
- **1.25.x 处理**：**保持现状（用户已确认）**。不在本版本改变读写路径；数据库索引层记录“文件实际路径 + 记录时间”，并提供只读诊断。行为基线按现状测试。

### P0-2 发布脚本过期、缺模块、泄露真实配置

- **位置**：`打包.bat`
- **已确认**：
  - `VERSION=1.21.3`；
  - 不复制 `image_viewer/`、`excel_importer.py`、`search_service.py`、`src/`；
  - 直接 `xcopy config`，会把真实 `settings.json`/`tags.json` 打进包；
  - 文档名仍是 `用户操作手册_v1.3.0.html`，实际不存在。
- **影响**：冷启动可能因 `excel_importer` 缺失失败；查看器/搜索不可用；泄露真实 UNC/操作员信息。
- **1.25.x 处理**：**暂缓（用户已确认）**。本轮先忽略打包脚本问题，不作为 v1.25.0 阻塞项；审计结论保留，后续版本再处理。

### P0-3 内嵌环境缺 `openpyxl`

- **已确认**：`python/Lib/site-packages/openpyxl` 不存在；`xlsxwriter` 存在。
- **影响**：`excel_importer.parse_excel()` 明确使用 `openpyxl`，Excel 恢复不可用。
- **1.25.x 处理**：在 v1.25.0 的依赖自检中显式提示；安装/打包校验；格式检查报告优先用 `xlsxwriter`（已可用），避免新增依赖。

### P1-1 “不合格归集”复制后删除源目录

- **位置**：`export_utils.py:22-53`、`export_mixin.py:20-65`
- **机制**：目标存在先 `rmtree`，再 `copytree`，成功后 `rmtree(src)`；无 staging、无校验、无回滚。
- **影响**：网络中断/权限/磁盘满时可能目标不完整、源已删。
- **1.25.x 处理**：**保持现状（用户已确认）**。默认行为不变；新归档模块（v1.25.6）按“只复制”设计，但旧归集入口继续保留原行为。操作写日志。

### P1-2 `save_to_disk()` 写回源目录 TXT

- **位置**：`data_mixin.py:718-733`
- **机制**：保存记录时，如果 `group['txt_zh']`/`txt_en']` 存在且内容非空，直接 `os.replace` 写回源目录 TXT。
- **影响**：与 1.25.x “原始 TXT 必须受保护”冲突；误编辑会直接改供应商文件；无版本备份。
- **1.25.x 处理**：**保持现状（用户已确认）**。质检保存仍写回源 TXT；不新增默认备份/二次确认。未来如增加保护，只做可选开关。

### P1-3 ImageDock 未打开也启动并加载整组图

- **位置**：`app.py:239-268`、`viewer/dock_manager.py`、`viewer/image_dock.py`
- **影响**：额外 CPU/内存/UNC IO；与 `image_viewer` 重复解码。
- **1.25.x 处理**：v1.25.0 只做懒启动，不改查看器协议；保持 v1.24.0 锁定视图行为。

### P1-4 无自动化测试、无 CI、版本号多处漂移

- **已确认**：项目内无 `tests/`、`pytest.ini`、`pyproject.toml`、`.github/`；打包脚本 1.21.3、应用 1.24.0、手册 1.3.0。
- **1.25.x 处理**：每个版本必须新增纯函数单测 + fixture 目录 + 只读断言；见 `08-version-plan-and-tests.md`。

### P1-5 扫描规则是全局单例

- **位置**：`scan_rules.json` + `utils.load_scan_rules/save_scan_rules`
- **影响**：切换供应商/项目会静默改变识别结果；旧数据集可能 0 组。
- **1.25.x 处理**：v1.25.0 引入规则方案 + 版本 + 按供应商/团队绑定；旧 `scan_rules.json` 作为“默认方案”迁移。

### P2-1 配置/分类为空

- `categories_config.json` 当前是 `{"L1": [], "L2": {}}`，会让分类校验整体关闭。
- 需要确认是临时状态还是用户有意清空。

### P2-2 `ensure_batch_metadata()` 会写源目录

- `disk_io.py:146-177` 会在批次目录写 `batch_meta.json`。
- **当前无调用点**（已核对），但属于未来误用风险。1.25.x 扫描器禁止调用任何写源函数。

### P2-3 两套 Qt 查看器并存

- `image_viewer/`（子进程）与 `viewer/`（进程内）各自有配置、加载、EXIF、缩放逻辑。
- 1.25.x 不改协议，只保证新页面不触发额外加载。

### P2-4 证据截图路径解析有限

- `data_mixin.py:542-567` 只有绝对路径、数据根、`BASE_DIR`、证据目录文件名四类候选。
- 跨机器/移动目录后可能“文件不存在”。导入其他验收员记录时需保留原始路径并标注不可解析，不能丢弃。

---

## 6. 现有能力清单（必须保留）

| 能力 | 现状 | 1.25.x 约束 |
|---|---|---|
| 双图/多图质检 | 支持，图片槽位可配 | 不限制只能 4 张 |
| 图片查看器 | `image_viewer/` + 锁定视图 + 异步大图 | 不改查看器协议；不修改原图 |
| 2K/4K/8K | v1.24.0 已支持异步解码/预览/LRU | 保持 |
| 缩放/拖动/截图/标注 | 已有 | 保持 |
| 快捷键 | `settings_mixin.inject_hotkeys` | 保持 |
| 自定义错误标签 | `config/tags.json` + CSV `标签` 列 | 兼容其他验收员标签文本 |
| 备注 | CSV `备注` | 保留原文 |
| 快速查询 | `search_service.py` + 导航统一 | 保持 |
| 历史记录 | 按操作员目录 + CSV 选择器 | 不改字段、不合并覆盖 |
| 质检记录导出 | CSV + Excel（含截图） | 保持；新增格式检查报告独立 |
| 合格数据导出 | 复制到目标目录 | 新归档模块只复制、可校验、可恢复 |
| Excel 导入 | `excel_importer.py` | 依赖修复后保持；导入只读 |
| 局域网目录 | UNC 路径 | 新扫描器必须处理长路径、权限、断线 |

---

## 7. 兼容性基线

### 7.1 必须继续可读的旧数据

- 旧版 `*_验收记录.csv`（列可能缺失）；
- 旧版 `config/settings.json`、`config/tags.json`；
- 旧版 `scan_rules.json`（含 `folder_digit_length` 旧字段）；
- 旧版截图（相对/绝对路径、旧证据目录）；
- 旧版 Excel 导出（含内嵌截图）；
- 旧版查看器配置（`image_viewer.*`、`~/.imagedock_settings.json`）。

### 7.2 兼容策略

1. **读时兼容，写时保守**：新代码读取旧格式时补默认值；写回时保持原列/原顺序。
2. **不迁移就不破坏**：数据库首次启动只建索引，不改 CSV；迁移失败可回滚。
3. **双写可选**：验收保存仍以 CSV 为准；数据库索引异步/幂等更新。
4. **旧版用户零要求**：不要求其他验收员升级、不改导出方式、不建新统一目录。
5. **规则迁移显式化**：旧 `scan_rules.json` 自动生成“默认规则方案 v1”，但保留原文件。

---

## 8. 与 1.25.x 目标的差距

| 1.25.x 要求 | 现状 | 差距 |
|---|---|---|
| 多套扫描规则 | 单一全局 JSON | 需规则方案/版本/绑定 |
| 逐层目录识别 | 只看文件夹名 | 需层级模型 + 匹配算法 + 原因 |
| 扫描预览/诊断 | 无 | 需 dry-run + 原因码 + 统计 |
| 候选批次人工确认 | 无 | 需候选状态机 + 合并/拆分/忽略 |
| 格式检查 | 外部 EXE | 需原生只读模块 |
| 翻译/预审 | 外部 EXE | 需原生仿照（provider/Prompt/费用/安全写入）+ 历史报告只读导入 |
| 其他验收员导入 | 无 | 需 CSV 类型识别 + 去重 + 标签兼容 |
| 统计/日报 | 只有抽检进度 | 需事件索引 + 统计查询 |
| 归档 | 复制/移动混合 | 需只复制 + 预览 + 校验 + 恢复 |
| 操作日志 | 仅 logging | 需结构化审计日志 |

---

## 9. 审计结论

1. **保留现有质检核心**，包括 CSV 写入、查看器、快捷键、截图、查询、导出。
2. **新增管理平台层必须独立模块化**，通过显式接口调用旧代码，不把新逻辑写进 `app.py`。
3. **三个数据边界行为按用户决定保持现状**：源 TXT 写回、归集删除源、跨天 CSV 回退都不改变默认行为；新增管理能力不得绕过它们，只做日志与索引。
4. **格式检测适合原生实现**，按确认顺序在 v1.25.2 落地（详见 `03-format-checker-design.md`）。
5. **豆包 AI 预审工具按原生仿照实现**，保留历史报告只读导入；不能 `import` 原 EXE，也不能保证与其 Prompt/质量完全等价。
6. **每个版本必须有测试与回滚**，否则不进入下一版本。

---

## 10. 只读确认

本次审计未修改任何源码、配置、用户数据或 Git 状态；未启动应用或外部工具；未执行任何迁移、安装、打包或提交。唯一新增内容为 `docs/1.25.x/` 下的文档。
