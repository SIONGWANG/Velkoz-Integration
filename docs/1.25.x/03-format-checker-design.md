# 原生格式检测模块设计（modules/format_checker）

> 目标版本：v1.25.2（可独立提前开发）
> 设计原则：只读、可配置、可解释、可追溯、可测试
> 范围：只实现“格式检测”，不实现人物一致性、分组整理、压缩、水印、修复

---

## 1. 目标与非目标

### 1.1 目标

1. 保留图片编辑工具箱 v0.7.2 的成熟格式检测规则：
   - 任务 ID 规则；
   - 单图结构；
   - 多图结构；
   - 图片命名/扩展名；
   - PSD；
   - TXT 缺失；
   - 文件缺失/多余；
   - 目录层级；
   - 重复编号；
   - 不同类目规则；
   - 异常文件。
2. 比原工具更强：
   - 规则模板可版本化；
   - 可按供应商/团队/类目绑定；
   - 与批次、扫描结果、验收记录关联；
   - 检查结果入库、可对比、可重跑；
   - 错误码统一、可解释、可导出；
   - 强制只读，检查前后可证明文件未变。
3. 与现有质检核心解耦：不 import `data_mixin`/`settings_mixin`，不修改源文件，不依赖 Streamlit 才能运行。

### 1.2 非目标

- 不判断图片质量、分辨率、PSD 图层、语义内容；
- 不自动改名、移动、删除、修复任何源文件；
- 不实现人物一致性检测；
- 不实现数据分组整理/压缩/水印/加英文；
- 不把外部 EXE 嵌入主进程；
- 不在检查阶段写源目录（包括日志、缓存、`.json`）。

---

## 2. 模块结构

```text
modules/
└── format_checker/
    ├── __init__.py          # 公开 API：check_path / preview_path / export_report
    ├── models.py            # dataclass：Template, TaskResult, Issue, RunSummary
    ├── templates.py         # 内置模板 + JSON 校验 + 版本 + 绑定
    ├── id_rules.py          # 任务 ID 编译/匹配（digits / prefix_digits）
    ├── discovery.py         # 只读目录遍历、任务目录发现、批次目录跳过
    ├── matcher.py           # 单图/多图/双模式匹配、编号/扩展名/重复判断
    ├── checker.py           # 编排：发现 → 匹配 → 问题聚合 → 结果
    ├── report.py            # Excel（xlsxwriter）/CSV 报告
    ├── service.py           # 持久化编排（调用 database 层，可选）
    ├── errors.py            # 错误码、严重级别、中文说明、修复建议
    └── ui.py                # Streamlit 页面（只做展示/触发，不含规则逻辑）
```

依赖方向：

```text
ui → service → checker → discovery/matcher/id_rules/templates
report ← checker 结果
database ← service（仅写检查记录，不写源目录）
```

**禁止**：`format_checker` 反向 import `app.py`、`data_mixin.py`、`settings_mixin.py`、`zone_*.py`。

---

## 3. 领域模型

```python
@dataclass(frozen=True)
class IdRule:
    mode: str            # "digits" | "prefix_digits"
    length: int          # 例如 8
    prefix: str = "JD"   # mode=prefix_digits 时使用

@dataclass(frozen=True)
class FileRule:
    suffix: str                    # "" 或 "_result" / "_CH"
    extensions: tuple[str, ...]    # ("jpg","jpeg","png") / ("psd",) / ("txt",)
    required: bool
    role: str                      # "primary" | "result" | "psd" | "zh" | "en" | "other"

@dataclass(frozen=True)
class MultiRule:
    suffix_prefix: str = "_"
    start: int = 1
    max_count: int | None = 4      # None = 不限制
    contiguous: bool = True
    min_count: int = 1
    extensions: tuple[str, ...] = ("jpg","jpeg","png")
    allow_result: bool = False

@dataclass(frozen=True)
class Template:
    id: str
    name: str
    version: int
    schema_version: int
    id_rule: IdRule
    modes: tuple[str, ...]          # ("single",) / ("multi",) / ("single","multi")
    single: tuple[FileRule, ...]
    multi: MultiRule | None
    extra_files: str                # "ignore" | "warn" | "error"
    scan: ScanRule

@dataclass
class Issue:
    code: str
    severity: str          # "error" | "warn" | "info"
    task_id: str
    task_path: str
    file_path: str | None
    expected: str | None
    actual: str | None
    message: str
    suggestion: str | None

@dataclass
class TaskResult:
    task_id: str
    task_path: str
    detected_mode: str | None       # "single" | "multi" | None
    result: str                     # "pass" | "fail" | "warn" | "skipped"
    image_count: int
    txt_count: int
    file_count: int
    issues: list[Issue]
    fingerprint: str                # 任务目录快照哈希

@dataclass
class RunSummary:
    run_id: str
    root_path: str
    template_id: str
    template_version: int
    started_at: str
    finished_at: str
    scanned_tasks: int
    passed_tasks: int
    failed_tasks: int
    warning_tasks: int
    skipped_tasks: int
    issues_by_code: dict[str, int]
    integrity_status: str           # "ok" | "warning"
```

---

## 4. 任务 ID 规则

### 4.1 两种内置模式

| 模式 | 正则 | 示例 |
|---|---|---|
| `digits` | `^\d{N}$` | `12345678` |
| `prefix_digits` | `^JD\d{N}$` | `JD12345678` |

默认 `N=8`，默认前缀 `JD`，均可在模板中配置。

### 4.2 一致性要求

- 任务目录名必须**整体匹配** ID 规则；
- 目录名即任务 ID（不再从文件名反推 ID）；
- 所有业务文件的 stem 必须是：
  - `ID`；或
  - `ID + 模板后缀`（如 `ID_result`、`ID_CH`、`ID_1`）。
- 文件名中的 ID 部分与目录 ID 不一致 → `FC-003`。
- 大小写：默认**不敏感**；报告保留原始大小写，并给出 `FC-303` 警告（若模板要求严格大小写则升级为错误）。

### 4.3 未来扩展（不在 v1.25.2）

- 多段 ID（项目码 + 数字）；
- 年份/月份前缀；
- 自定义正则模板；
- 供应商专属 ID。

实现上保留 `IdRule` 抽象，后续加 `regex` 模式即可，不破坏现有模板。

---

## 5. 模板数据结构

### 5.1 默认模板（与外部工具对齐）

```json
{
  "schema_version": 1,
  "id": "builtin_default",
  "name": "默认：单图 + 多图",
  "version": 1,
  "id_rule": { "mode": "digits", "length": 8, "prefix": "JD" },
  "modes": ["single", "multi"],
  "single": [
    { "suffix": "",        "extensions": ["jpg","jpeg","png"], "required": true,  "role": "primary" },
    { "suffix": "_result", "extensions": ["jpg","jpeg","png"], "required": false, "role": "result" },
    { "suffix": "",        "extensions": ["psd"],              "required": false, "role": "psd" },
    { "suffix": "_CH",     "extensions": ["txt"],              "required": true,  "role": "zh" },
    { "suffix": "_EN",     "extensions": ["txt"],              "required": true,  "role": "en" }
  ],
  "multi": {
    "suffix_prefix": "_",
    "start": 1,
    "max_count": 4,
    "contiguous": true,
    "min_count": 1,
    "extensions": ["jpg","jpeg","png"],
    "allow_result": false
  },
  "extra_files": "warn",
  "scan": {
    "recursive": false,
    "ignore_names": ["Thumbs.db", "desktop.ini", ".DS_Store"],
    "max_tasks": 200000,
    "follow_symlinks": false
  }
}
```

> 说明：PSD 规则使用 `suffix=""` 与 `extensions=["psd"]`，与主图规则区分；匹配时按“后缀 + 扩展名”组合，不会把 `ID.psd` 当主图。

### 5.2 模板管理

- 内置模板只读；
- 用户模板可新建/编辑/复制/删除/导入/导出；
- 每次保存生成新版本（`format_template_versions`）；
- 检查记录保存**模板快照**，后续修改模板不影响历史结果；
- 模板作用域：
  - `global`：所有批次；
  - `supplier`：按供应商；
  - `team`：按团队；
  - `category`：按 L1/L2；
  - `project`：按项目。
- 选择优先级：批次显式指定 > category > team > supplier > global > 内置默认。
- 模板 JSON 必须通过 schema 校验：未知字段警告、类型错误拒绝、正则可编译、扩展名白名单非空。

### 5.3 不同类目规则

1. 类目绑定：`categories_config.json` 的 L1/L2 或数据库 `categories` 表；
2. 批次检查时自动选择该类目的模板；
3. 未绑定则回退默认；
4. 报告中记录“实际使用的模板 + 选择原因”。

---

## 6. 检查模式与匹配算法

### 6.1 目录发现

```text
输入：root_path, template.scan
规则：
1. root_path 必须存在且是目录，否则返回 FC-000（前置错误）。
2. 默认只遍历 root_path 及其“直接子目录”；
   recursive=true 时遍历全部层级。
3. 跳过：
   - 证据目录（_00_Evidence_*）；
   - 不合格/归档输出目录（可配）；
   - 模板 ignore_names；
   - 符号链接/junction（默认不跟随，避免循环）。
4. 对每个目录：
   - 目录名匹配 ID 规则 → 候选任务目录；
   - 不匹配但包含候选任务子目录 → 继续（递归时）；
   - 目录不可读 → FC-006，跳过并记录。
5. 任务目录去重：同一绝对路径只检查一次。
6. 统计扫描上限，超过 max_tasks 则停止并标记 run 为 partial。
```

### 6.2 单图匹配

对任务目录下的文件（仅当前层，不含子目录）：

```text
1. 基础 stem = 目录名（任务 ID）。
2. 对 single 中每条 FileRule：
   - 候选文件名 stem = ID + rule.suffix；
   - 扩展名 ∈ rule.extensions（大小写不敏感）；
   - 找到 0 个：
       required=true → issue（缺失）
       required=false → 跳过
   - 找到 1 个 → 记录
   - 找到多个（同 stem 不同扩展名）：
       - 若 rule.extensions 允许多种 → issue“扩展名冲突/重复”
       - 选择优先级：模板声明顺序 / 明确扩展名优先级
3. 文件名 stem 不以 ID 开头 → 可能是“额外文件”，走 extra_files 策略；
4. 文件名包含其他 ID → FC-003。
```

### 6.3 多图匹配

```text
1. 扫描文件名：^<ID>_(\d+)(\.ext)$，ext ∈ multi.extensions。
2. 收集编号集合 indices。
3. 数量检查：
   - len(indices) < min_count → FC-201
   - max_count != None 且 len(indices) > max_count → FC-202
4. 连续性检查（contiguous=true）：
   - 期望 1..max(indices)；
   - 缺失编号 → FC-203（列出缺失）。
5. 重复编号：
   - 同一编号出现多个扩展名/多个文件 → FC-204。
6. 起始编号：
   - start=1 时出现 _0 → FC-205（可配 warn/error）。
7. 扩展名：
   - 同编号不同扩展名 → FC-204；
   - 扩展名不在白名单 → FC-206。
8. allow_result=false 时出现 ID_result → 额外文件策略（通常 warn/error）。
```

### 6.4 双模式（single + multi）

```text
1. 先分别计算 single 和 multi 的“完整通过”条件。
2. 任一通过 → task.result = pass，detected_mode = 通过的模式。
3. 都未通过：
   - 选择“问题更少/更接近通过”的模式作为 detected_mode；
   - 输出该模式的 issues；
   - 若两种模式都缺少关键文件，额外输出 FC-002（无有效结构）。
4. 不允许把单图和多图的文件混在一起“凑通过”。
```

### 6.5 额外文件策略

- `ignore`：只记录 info，不影响结果；
- `warn`：记录 warn，任务仍可 pass；
- `error`：记录 error，任务 fail。
- 默认忽略：`Thumbs.db`、`desktop.ini`、`.DS_Store`、`~$*`、`.tmp`。
- 隐藏文件/系统文件：默认 warn，不直接判失败。

---

## 7. 错误码体系

### 7.1 前置/目录

| 码 | 级别 | 含义 | 建议 |
|---|---|---|---|
| FC-000 | error | 根目录不存在/不是目录 | 检查路径 |
| FC-001 | error | 任务目录名不符合 ID 规则 | 按模板改名/确认规则 |
| FC-002 | error | 任务目录下没有任何可识别图片 | 检查是否放错目录 |
| FC-003 | error | 文件名 ID 与目录 ID 不一致 | 统一 ID |
| FC-004 | warn | 同一 ID 在本次范围内重复出现 | 确认是否不同批次 |
| FC-005 | error | 目录层级不符合规则（可选启用） | 调整目录或规则 |
| FC-006 | error | 目录不可读/权限不足 | 检查共享权限 |
| FC-007 | warn | 跳过符号链接/junction | 确认是否应跟随 |
| FC-008 | warn | 超过最大任务数，扫描提前结束 | 分批检查 |
| FC-009 | warn | 路径过长（>260） | 缩短路径/启用长路径 |

### 7.2 单图

| 码 | 级别 | 含义 |
|---|---|---|
| FC-101 | error | 缺少主图 `ID.<ext>` |
| FC-102 | error/warn | 缺少结果图 `ID_result.<ext>`（按模板 required） |
| FC-103 | error/warn | 缺少 PSD `ID.psd`（按模板 required） |
| FC-104 | error/warn | 缺少中文 TXT `ID_CH.txt` |
| FC-105 | error/warn | 缺少英文 TXT `ID_EN.txt` |
| FC-106 | error | 主图扩展名不在允许范围 |
| FC-107 | error | 结果图扩展名不在允许范围 |
| FC-108 | error | 同一规则匹配到多个扩展名（冲突） |

### 7.3 多图

| 码 | 级别 | 含义 |
|---|---|---|
| FC-201 | error | 多图数量少于下限 |
| FC-202 | error | 多图数量超过上限 |
| FC-203 | error | 多图编号不连续 |
| FC-204 | error | 多图编号重复/同号多扩展名 |
| FC-205 | warn/error | 编号从 0 或非约定起点开始 |
| FC-206 | error | 多图扩展名不在允许范围 |
| FC-207 | error | 多图文件名格式不符合 `ID_n` |

### 7.4 文件/内容

| 码 | 级别 | 含义 |
|---|---|---|
| FC-301 | warn/error | 未预期的额外文件（按策略） |
| FC-302 | error | 文件大小为 0 |
| FC-303 | warn | 大小写/空格/全角字符异常 |
| FC-304 | error | 文件被占用/无法读取 |
| FC-305 | warn | TXT 为空或编码异常（可选） |
| FC-306 | info | 忽略的临时/系统文件 |
| FC-307 | warn | 同名文件大小写冲突 |
| FC-308 | warn | 文件修改时间异常（未来时间） |
| FC-309 | info | 隐藏文件 |
| FC-310 | warn | 图片扩展名与实际格式不符（可选，读文件头） |

### 7.5 结果判定

```text
若 issues 中存在 severity=error → result = fail
否则若存在 severity=warn  → result = warn（可配置是否算通过）
否则                      → result = pass
```

默认：`warn` 不影响“合格”，但报告中单列。模板可配置 `warn_as_fail`。

---

## 8. 只读保证（硬约束）

### 8.1 代码级约束

- 只使用 `os.scandir`/`os.walk`/`os.stat`/`open(path, "rb")`；
- 不调用 `shutil.copy/copy2/copytree/move/rmtree`；
- 不调用 `os.remove/rename/replace/makedirs`（对源路径）；
- 不写 `.json`、`.log`、`.tmp`、缓存到源目录；
- 报告只写到：
  - 用户选择的导出目录；或
  - 管理数据目录 `reports/format_check/`。
- 所有源路径经过 `normalize + resolve`，并校验仍在 `root_path` 之下（防止路径穿越/拼接错误）；
- 不跟随符号链接/junction（默认）；
- 不在检查期间修改文件时间/属性。

### 8.2 运行时证明

每次运行：

1. 检查前对任务目录做轻量快照：
   - 相对路径、文件大小、`mtime_ns`；
2. 检查后再次快照；
3. 比较：
   - 完全一致 → `integrity_status = ok`；
   - 有差异 → `integrity_status = warning`，记录具体文件与差异，报告显著提示；
4. 快照只存大小/时间/相对路径，不存文件内容，不写源目录。

### 8.3 与“修复”的边界

- v1.25.2 **不提供任何修复**；
- 后续如做“修复向导”，必须：
  - 独立页面/独立授权；
  - 逐项 diff 预览；
  - 先备份到 `_00_Repair_Backup/{run_id}/`；
  - 用户逐项确认；
  - 可回滚；
  - 绝不由批量检查自动触发。

---

## 9. 持久化

### 9.1 表

- `format_templates`
- `format_template_versions`
- `format_check_runs`
- `format_check_task_results`
- `format_check_issues`

字段与索引见 `05-database-design.md`。

### 9.2 记录内容

每次运行至少保存：

- 批次 ID（可空）；
- 根目录；
- 模板 ID + 版本 + 规则快照；
- 检查时间/耗时；
- 检查数量、合格、异常、警告、跳过；
- 异常文件路径、错误类型、错误说明；
- 检查结果；
- 操作员；
- 完整性状态；
- 报告路径。

### 9.3 重跑与对比

- `re-check`：基于上次 run 的 `task_path` 列表重跑；
- `only_anomalies`：只跑上次 `fail/warn` 的任务；
- `diff`：
  - 新增任务；
  - 消失任务；
  - 由通过变失败；
  - 由失败变通过；
  - 错误码变化；
  - 文件数变化。

---

## 10. 报告

### 10.1 Excel（默认）

文件：`格式检查_{batch}_{yyyyMMdd_HHmmss}.xlsx`

工作表：

| Sheet | 内容 |
|---|---|
| 总览 | 运行信息、模板、耗时、数量、错误码汇总 |
| 任务结果 | 每个任务一行：ID、路径、模式、结果、图片数、TXT 数、问题数 |
| 异常明细 | 每个 issue 一行：任务 ID、路径、错误码、级别、文件、期望、实际、说明、建议 |
| 规则说明 | 模板快照 + 字段解释 + 只读声明 |
| 差异（可选） | 与上次运行的对比 |

### 10.2 CSV（兼容）

- `format_check_tasks_*.csv`
- `format_check_issues_*.csv`
- UTF-8 BOM，列名固定，便于其他工具读取。

### 10.3 报告原则

- 报告本身不写入源目录；
- 报告包含“检查时使用的规则版本”，保证可追溯；
- 报告包含“只读校验结果”；
- 不把图片二进制写入 Excel（与原工具一致，只写路径/元信息）。

---

## 11. UI 设计（Streamlit）

### 11.1 页面位置

新增主导航“格式检查”，不挤压现有正式质检三栏。

```text
侧边栏/顶部导航：
  工作台 | 供应商提交 | 数据扫描 | 批次管理 | 格式检查 | 翻译与预审 |
  正式质检 | 验收记录 | 合格归档 | 数据统计 | 系统设置
```

v1.25.2 只实现“格式检查”页面 + 必要的设置入口；“正式质检”仍是原页面，布局不变。

### 11.2 页面结构

```text
┌ 格式检查 ─────────────────────────────────────────────┐
│ 检查范围：○ 单批次  ○ 多批次  ○ 整个根目录            │
│ 批次/路径：[选择/输入]                                 │
│ 模板：[下拉]  [管理模板]  [预览规则]                   │
│ 选项：□ 递归  □ 仅检查异常  □ 包含警告                 │
│ [预览] [开始检查] [停止]                               │
├ 进度/摘要 ────────────────────────────────────────────┤
│ 已检查 1,234 / 5,678 | 合格 1,100 | 异常 120 | 警告 14 │
├ 结果表 ───────────────────────────────────────────────┤
│ 搜索：[    ]  结果：[全部/合格/异常/警告]  错误码：[ ] │
│ ID | 路径 | 模式 | 结果 | 图片 | TXT | 问题数 | 操作    │
│ ...                                                   │
├ 详情 ─────────────────────────────────────────────────┤
│ 选中任务的 issue 列表 + [打开任务文件夹]               │
├ 历史 ─────────────────────────────────────────────────┤
│ 最近检查记录 + [查看报告] [重跑] [对比]                │
└───────────────────────────────────────────────────────┘
```

### 11.3 交互要求

- 开始前显示“预计检查任务数/文件数/模板摘要”；
- 运行中可停止，已检查结果保留；
- 结果表可搜索、筛选、排序、分页；
- “打开任务文件夹”用系统资源管理器，不修改文件；
- 报告生成失败时给出明确错误，不吞异常；
- 所有按钮有 disabled 状态，避免重复运行。

### 11.4 与正式质检的衔接

- 检查结果可一键“带到正式质检”：设置 `root_path` 并触发扫描（等价于现有“加载文件夹”）；
- 不在格式检查页直接修改验收记录；
- 异常任务可在正式质检中人工判断。

---

## 12. 与扫描/批次模块的接口

### 12.1 输入

```python
FormatCheckRequest(
    root_path: str,
    template_id: str,
    batch_id: str | None,
    recursive: bool | None,
    only_anomalies: bool = False,
    previous_run_id: str | None = None,
    max_tasks: int | None = None,
    operator: str = "",
)
```

### 12.2 输出

```python
FormatCheckResult(
    summary: RunSummary,
    tasks: list[TaskResult],
    report_path: str | None,
)
```

### 12.3 与批次身份

- 如果传入 `batch_id`，每条结果关联该批次；
- 如果只传 `root_path`，生成“临时检查”，`batch_id=None`；
- 不要求先有批次才能检查（降低使用门槛）；
- 后续可由批次管理页发起“批次级检查”。

### 12.4 与目录规则

- 格式检查只负责“任务目录内的文件结构”；
- 任务目录的**层级识别**由 `scanning` 模块负责；
- 两者共享 `Template.scan` 的递归/忽略配置，但不重复实现层级规则；
- 如果目录规则识别出“任务层”，格式检查直接使用该层路径。

---

## 13. 性能与可靠性

- 使用 `os.scandir` 而不是 `os.walk` 收集当前层文件；
- 每个任务只读一次目录项；
- 大目录分页展示，不一次性渲染全部行；
- 支持 `max_tasks`、`timeout`、取消令牌；
- 单任务异常不终止全量检查；
- 报告生成用 `xlsxwriter`（已在内嵌环境，避免 `openpyxl` 缺失）；
- 10,000 任务目标：常规 SSD 本地目录 < 60 秒；UNC 视网络而定，UI 不阻塞；
- 检查过程不持有图片对象，只读文件元信息；可选读文件头判断真实格式（`FC-310`）。

---

## 14. 行为对齐与差异说明

| 项目 | 外部工具 | 本模块 | 说明 |
|---|---|---|---|
| 模板存储 | EXE 内部 | JSON + DB + 版本 | 可导出/导入 |
| 单图默认 | ID / _result / .psd / _CH / _EN | 相同 | 默认 required 可按业务调整 |
| 多图默认 | _1–_4 | 相同 | 可配上限/不限/连续 |
| 递归 | 可选 | 可选 | 默认关闭 |
| 报告 | 自动 Excel | Excel + CSV | 可追溯规则版本 |
| 打开任务目录 | 支持 | 支持 | 系统资源管理器 |
| 修复 | 未见说明 | 不提供 | 另立版本、显式确认 |
| 人物一致性 | 支持 | 不做 | 超出范围 |
| 其他编辑功能 | 支持 | 不做 | 超出范围 |

> 由于不能读取 EXE 内部实现，以上是“产品行为对齐”。若用户发现外部工具的实际边界与本文不同，以用户确认后的规则为准，并更新模板默认值。

---

## 15. 测试策略

### 15.1 单元测试

- ID 规则：纯数字/前缀/错误位数/大小写/全角；
- 单图：主图缺失、结果图、PSD、CH/EN 缺失、扩展名冲突；
- 多图：数量上下限、连续/不连续、重复编号、从 0 开始、不限上限；
- 双模式：只满足单图、只满足多图、都不满足、混合文件；
- 额外文件策略：ignore/warn/error；
- 忽略名单：Thumbs.db/desktop.ini/.DS_Store；
- 路径安全：`..`、绝对路径、符号链接、超长路径；
- 报告：列名、行数、错误码汇总。

### 15.2 Fixture 目录

```text
tests/fixtures/format_checker/
├── valid_single/
├── valid_multi/
├── missing_primary/
├── missing_result/
├── missing_psd/
├── missing_txt/
├── wrong_extension/
├── multi_gap/
├── multi_duplicate/
├── multi_too_many/
├── id_mismatch/
├── extra_files/
├── mixed_single_multi/
├── nested_recursive/
├── nested_non_recursive/
├── unreadable/            (权限模拟)
└── empty/
```

### 15.3 只读测试

- 运行前后递归快照（路径/大小/mtime）完全一致；
- 源目录不出现任何新文件（尤其是 `.json`/`.log`/`.tmp`）；
- 报告只出现在指定输出目录；
- 失败/取消/异常路径也不写源。

### 15.4 兼容测试

- 旧 `scan_rules.json` 仍可被现有扫描器读取；
- 格式检查不依赖 `openpyxl`；
- 不修改 `config/settings.json`；
- 不修改现有 CSV。

---

## 16. 待后续确认（不阻塞 v1.25.0）

> 建议默认值已记录在 `00-decisions.md` 与 `README.md`；以下不影响格式检查模块开发。

1. 单图默认是否强制 `_CH`/`_EN` 都存在？外部工具默认“允许”，但“必需文件可切换”。建议默认 required=false，业务模板再开启。
2. 多图最小数量默认 1 还是 2？
3. `ID_result` 在多图模式是否允许？
4. 扩展名是否包含 `webp/bmp/tif`？
5. 大小写严格程度：默认不敏感 + 警告，是否需要直接判错？
6. 是否需要“按类目自动选模板”？如果需要，先配置类目。
7. 报告是否需要“每个任务的文件清单”明细？会增加体积。
8. 是否允许检查 UNC 根目录？需要处理断线/超时。
9. 检查结果是否要自动推送工作台“待处理”？
