# 逐层目录规则设计文档

> 目标版本：v1.25.0（模型 + 预览）→ v1.25.1（确认 + 持久化）
> 适用范围：数据扫描、候选批次识别、合格数据输出目录
> 原则：可配置、可解释、可版本化、不写死七层

---

## 1. 背景与目标

典型目录：

```text
root/大类目/小类目/团队/日期/批次名/数据
```

但真实目录可能：

- 缺少小类目；
- 缺少团队；
- 多出日期/场景/项目目录；
- 有额外中间层；
- 多个根目录；
- 不同供应商结构不同。

因此不能把固定七层写死。需要：

1. 逐层配置；
2. 允许跳过/额外层；
3. 多套规则方案；
4. 扫描预览与原因解释；
5. 规则版本与回滚；
6. 与输出目录规则分离。

---

## 2. 规则模型

### 2.1 层级定义

默认层级：

| 顺序 | key | 默认名称 | 示例 | 默认启用 | 默认必选 | 参与身份 | 参与输出 |
|---|---|---|---|---|---|---|---|
| 0 | root | 根目录 | `D:\数据` | 是 | 是 | 是 | 否 |
| 1 | category_l1 | 大类目 | 人物/物体/场景 | 是 | 是 | 是 | 是 |
| 2 | category_l2 | 小类目 | 具体类型 | 是 | 否 | 是 | 是 |
| 3 | team | 团队 | 供应商团队 | 是 | 是 | 是 | 是 |
| 4 | date | 日期 | 2026-11-07 | 是 | 是 | 是 | 是 |
| 5 | batch | 批次名 | batch_20261107_001 | 是 | 是 | 是 | 是 |
| 6 | task | 数据/任务 | 12345678 | 是 | 是 | 是 | 是（数据层） |

### 2.2 每层可配置字段

| 字段 | 类型 | 含义 |
|---|---|---|
| `key` | string | 唯一键 |
| `label` | string | 中文名 |
| `enabled` | bool | 是否启用 |
| `required` | bool | 是否必须匹配 |
| `skippable` | bool | 源目录缺失时是否允许跳过 |
| `order` | int | 顺序（允许调整） |
| `match.mode` | string | `any`/`literal`/`list`/`regex`/`date`/`id`/`name` |
| `match.values` | list | literal/list 的值 |
| `match.regex` | string | 正则 |
| `match.case_sensitive` | bool | 默认 false |
| `allow_extra_between` | bool | 该层之前是否允许额外目录 |
| `human_confirm` | bool | 识别后是否需人工确认 |
| `identity` | bool | 是否参与批次身份 |
| `output` | bool | 是否参与输出目录 |
| `default_value` | string | 缺失时的回退值 |
| `fallback` | string | `skip`/`use_default`/`error` |
| `exclusive` | bool | 是否独占一层（不允许合并） |

### 2.3 规则方案

```json
{
  "schema_version": 1,
  "id": "ruleset_supplier_a",
  "name": "供应商 A - 标准七层",
  "scope": "supplier",
  "scope_id": "sup_a",
  "version": 3,
  "root": { "mode": "user_selected" },
  "layers": [
    {
      "key": "category_l1",
      "label": "大类目",
      "enabled": true,
      "required": true,
      "skippable": false,
      "order": 1,
      "match": { "mode": "list", "values": ["人物", "物体", "场景"], "case_sensitive": false },
      "allow_extra_between": false,
      "human_confirm": false,
      "identity": true,
      "output": true,
      "fallback": "error"
    },
    {
      "key": "category_l2",
      "label": "小类目",
      "enabled": true,
      "required": false,
      "skippable": true,
      "order": 2,
      "match": { "mode": "any" },
      "allow_extra_between": true,
      "human_confirm": false,
      "identity": true,
      "output": true,
      "fallback": "skip"
    },
    {
      "key": "team",
      "label": "团队",
      "enabled": true,
      "required": true,
      "skippable": false,
      "order": 3,
      "match": { "mode": "regex", "regex": "^[\\u4e00-\\u9fa5A-Za-z0-9_\\-]{1,30}$" },
      "allow_extra_between": true,
      "human_confirm": true,
      "identity": true,
      "output": true,
      "fallback": "error"
    },
    {
      "key": "date",
      "label": "日期",
      "enabled": true,
      "required": true,
      "skippable": false,
      "order": 4,
      "match": { "mode": "date", "regex": "^(20\\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\\d|3[01])$" },
      "allow_extra_between": true,
      "identity": true,
      "output": true,
      "fallback": "error"
    },
    {
      "key": "batch",
      "label": "批次名",
      "enabled": true,
      "required": true,
      "skippable": false,
      "order": 5,
      "match": { "mode": "any" },
      "allow_extra_between": true,
      "human_confirm": true,
      "identity": true,
      "output": true,
      "fallback": "error"
    },
    {
      "key": "task",
      "label": "数据",
      "enabled": true,
      "required": true,
      "skippable": false,
      "order": 6,
      "match": { "mode": "id", "id_rule": { "mode": "digits", "length": 8 } },
      "allow_extra_between": false,
      "human_confirm": false,
      "identity": false,
      "output": true,
      "fallback": "error"
    }
  ],
  "extra_dirs": {
    "allow": true,
    "max_extra_between": 2,
    "ignore_names": ["_00_Evidence_*", "不合格", "归档", "node_modules", "$RECYCLE.BIN", "System Volume Information"]
  },
  "diagnostics": {
    "explain": true,
    "sample_limit": 20
  }
}
```

---

## 3. 匹配算法

### 3.1 目标

给定：

- 根目录 `root`；
- 规则层序列 `L[1..n]`；
- 路径段 `S[1..m]`（相对 root）。

求：

- 一种段到层的分配（允许跳过可选层、允许额外段）；
- 置信度；
- 原因说明；
- 若无法匹配，给出“最接近的失败原因”。

### 3.2 算法（带回溯的动态匹配）

```python
def match_path(segments, layers):
    results = []

    def dfs(i, j, assignment, extras, skipped, score):
        # i: 当前层索引；j: 当前路径段索引
        if i == len(layers):
            # 允许剩余段按配置作为额外层或交给 task 子目录
            if j == len(segments):
                results.append((score, assignment, extras, skipped))
            elif layers[-1].allow_extra_between:
                results.append((score - 0.1 * (len(segments) - j), assignment, extras, skipped))
            return

        layer = layers[i]
        if not layer.enabled:
            dfs(i + 1, j, assignment, extras, skipped, score)
            return

        # 跳过可选层
        if not layer.required and layer.skippable:
            dfs(i + 1, j, assignment, extras, skipped + [layer.key], score - 0.05)

        # 尝试匹配当前段
        if j < len(segments) and match_layer(layer, segments[j]):
            assignment[layer.key] = segments[j]
            dfs(i + 1, j + 1, assignment, extras, skipped, score + 1.0)
            assignment.pop(layer.key, None)

        # 允许额外段：消费当前段，层不变
        if layer.allow_extra_between and j < len(segments):
            extras.append(segments[j])
            dfs(i, j + 1, assignment, extras, skipped, score - 0.2)
            extras.pop()

    dfs(0, 0, {}, [], [], 0.0)
    return best(results)
```

### 3.3 规则

1. **整体匹配优先**：先尝试完整层序列；找不到再放宽。
2. **必需层缺失**：如果某层 `required=true` 且无法匹配 → 该路径判为“结构异常”，记录原因。
3. **可选层跳过**：`skippable=true` 才允许跳过；否则必须匹配。
4. **额外层**：只有相邻层 `allow_extra_between=true` 才允许；超过 `max_extra_between` 判异常。
5. **歧义**：多个分配得分相同 → 标记 `human_confirm=true`，不自动确认。
6. **任务层**：最后一个业务层（通常是 `task`）识别为数据组目录；其下才是文件。
7. **不因单层缺失判整棵目录无效**：只要任务层可识别，批次可作为“部分识别”候选。
8. **深度限制**：默认最多 12 层，防止异常递归。

### 3.4 置信度

```text
score = 匹配层数 * 1.0
      - 跳过可选层 * 0.05
      - 额外段 * 0.2
      - 歧义 * 0.3
confidence = clamp(score / 期望层数, 0, 1)
```

- `>= 0.9`：高置信；
- `0.6–0.9`：需人工确认；
- `< 0.6`：结构异常/部分识别。

---

## 4. 原因码（可解释性）

> 需求明确：不能只显示“扫描结果为 0 组”，必须解释为什么识别/不识别。

| 码 | 含义 | 输出 |
|---|---|---|
| DR-001 | 根目录不存在/不可读 | 错误 |
| DR-002 | 任务目录名不符合 ID 规则 | 跳过原因 |
| DR-003 | 层级深度不足 | 缺少哪些层 |
| DR-004 | 层级深度超出 | 额外段列表 |
| DR-005 | 必需层无法匹配 | 层 key + 实际段 |
| DR-006 | 可选层被跳过 | 跳过的层 |
| DR-007 | 存在额外中间层 | 额外段列表 |
| DR-008 | 多个分配歧义 | 候选分配 |
| DR-009 | 目录下无图片 | 文件数 |
| DR-010 | 目录下无 TXT | 可配置 |
| DR-011 | 同 ID 重复批次 | 两个路径 |
| DR-012 | 目录被忽略 | 忽略规则 |
| DR-013 | 符号链接/ junction 跳过 | 路径 |
| DR-014 | 权限不足 | 路径 |
| DR-015 | 超过扫描上限 | 已扫描数 |
| DR-016 | 批次名重复 | 已存在批次 |
| DR-017 | 日期格式不匹配 | 实际值 |
| DR-018 | 规则版本冲突 | 方案/版本 |

每个候选批次保存 `reason_json`：

```json
{
  "matched_layers": {"category_l1": "人物", "team": "团队A", "date": "2026-11-07", "batch": "batch_001"},
  "skipped_layers": ["category_l2"],
  "extra_segments": ["场景1"],
  "confidence": 0.82,
  "reasons": ["DR-006", "DR-007"],
  "explain": "小类目未配置，已按可选层跳过；团队与日期之间多出“场景1”目录，按允许额外层处理。"
}
```

---

## 5. 多套规则方案

### 5.1 作用域

| 作用域 | 适用 |
|---|---|
| global | 默认方案 |
| supplier | 按供应商 |
| team | 按团队 |
| project | 按项目 |
| user | 用户自定义（本机） |

### 5.2 管理操作

- 新建/编辑/复制/删除；
- 导入/导出 JSON；
- 版本记录；
- 恢复旧版本；
- 设为默认；
- 预览影响。

### 5.3 选择优先级

```text
批次显式指定
  > project
  > team
  > supplier
  > global
  > 内置默认
```

### 5.4 修改保护

- 修改规则前必须“扫描预览”；
- 预览显示：
  - 受影响目录数；
  - 新增/消失的候选批次；
  - 身份字段变化；
  - 可能导致已确认批次失效的项；
- 用户确认后才保存新版本；
- 已确认批次保留其原规则版本快照，不因规则更新而改变。

---

## 6. 扫描流程

```text
1. 用户选择一个或多个根目录。
2. 选择规则方案（或自动按供应商）。
3. 只读扫描：
   - 遍历目录；
   - 匹配层；
   - 识别任务层；
   - 统计图片/TXT；
   - 记录原因。
4. 生成候选批次：
   - status = pending / abnormal / partial / duplicate；
   - identity = 大类目/小类目/团队/日期/批次；
   - group_count / file_count。
5. 展示扫描报告：
   - 候选列表；
   - 每个候选的路径、身份、置信度、原因；
   - 未识别目录的 Top N 原因。
6. 人工确认/修改/合并/拆分/忽略。
7. 确认后写入 batches / data_groups。
8. 保存扫描结果，下次打开不重复全量扫描。
```

### 6.1 多根目录

- 每个根目录独立扫描；
- 结果合并展示；
- 同一批次跨根目录 → 标记“重复/需确认”；
- 不自动合并不同根目录的同名批次。

### 6.2 扫描差异

重新扫描时对比：

- 新增数据组；
- 删除数据组；
- 修改（指纹变化）；
- 新增目录；
- 结构变化；
- 状态变化。

差异写入 `scan_candidates`/`scan_run_items`，UI 可筛选。

---

## 7. 输出目录规则（独立设计）

> 与源目录规则**分开**，不能复用同一份配置。

### 7.1 起始层级

用户选择从源目录哪一层开始保留：

- root；
- 大类目；
- 小类目；
- 团队；
- 日期；
- 批次名；
- 自定义。

### 7.2 模板

默认：

```text
{批次名}_{验收员}合格{合格数量}条
```

示例：

```text
批次001_张三合格120条
```

支持：

```text
{日期}*{批次名}*{验收员}_{合格数量}条
```

变量：

| 变量 | 来源 | 缺失回退 |
|---|---|---|
| `{批次名}` | batch.name | `未命名批次` |
| `{验收员}` | operator | `未指定` |
| `{合格数量}` | stats | `0` |
| `{日期}` | batch.submit_date | 当天 |
| `{大类目}`/`{小类目}`/`{团队}` | identity | 空/跳过 |
| `{自定义}` | 用户输入 | 必填校验 |

### 7.3 预览

复制前展示：

- 输出根目录；
- 起始层级；
- 完整目录树；
- 批次名/验收员/合格数量；
- 预计文件数/占用空间；
- 已存在冲突；
- 缺失变量回退说明。

### 7.4 安全

- 默认只复制，禁止移动；
- 不删除源、不覆盖源、不改 TXT/CSV/图片；
- 目标同名批次：
  - 跳过；
  - 生成新归档目录；
  - 对比已有文件；
  - 取消。
- 不自动调用 `collect_reject_folders` 的删除逻辑。

---

## 8. 兼容与迁移

### 8.1 旧 `scan_rules.json`

当前结构：

```json
{
  "folder_digit_length": 8,
  "folder_pattern": {"blocks": [{"rule":"digits","count":8,"label":""}]},
  "image_slots": [...],
  "text_slots": [...]
}
```

迁移：

1. 生成 `ruleset_default` + `version=1`；
2. `task` 层 = 8 位数字；
3. 其余层 `enabled=false`（或按用户确认启用）；
4. 图片/文本槽位保留原样；
5. 原 `scan_rules.json` 不删除、不改写；
6. 现有 `scan_files_from_disk` 继续可用（兼容读取旧格式）。

### 8.2 兼容要求

- 旧规则扫描结果与 v1.24.0 行为一致（回归测试）；
- 新规则方案不改变旧文件命名；
- 不要求用户立即配置七层；
- 默认方案可“只识别任务层”，逐步启用层级。

---

## 9. 校验与冲突

### 9.1 规则自身校验

- 层 key 唯一；
- order 不重复/可排序；
- regex 可编译；
- 至少一个 `required` 层；
- 任务层必须启用；
- `allow_extra_between` 不超过上限；
- 作用域 ID 存在。

### 9.2 运行时冲突

- 同一路径匹配多个方案 → 按优先级 + 提示；
- 同一批次名多个路径 → 重复标记；
- 同一任务 ID 多个批次 → 允许，但身份必须含批次；
- 层值非法（日期格式、空团队）→ 异常/人工确认。

---

## 10. 性能

- 只读 `os.scandir`；
- 每层匹配用预编译正则；
- 回溯限制深度/分支数；
- 大目录分批提交；
- 预览只扫描样本（默认前 500 个目录）；
- 全量扫描可取消；
- 结果缓存到 DB，避免每次全量。

---

## 11. 测试清单

| 场景 | 期望 |
|---|---|
| 标准七层 | 全部识别，高置信 |
| 缺少小类目 | 跳过可选层，仍识别 |
| 缺少团队 | 若 required → 结构异常；若 skippable → 部分识别 |
| 多余日期目录 | 作为额外层，警告 |
| 多余场景目录 | 作为额外层，警告 |
| 多个根目录 | 分别扫描，不自动合并 |
| 不同供应商规则 | 各自生效，互不影响 |
| 空目录 | 不产生候选，原因 DR-009 |
| 不符合规则 | 0 候选 + 原因，不静默 |
| 重复批次名 | 标记 duplicate |
| 同一编号不同项目 | 身份含批次/项目，不冲突 |
| 规则版本回滚 | 已确认批次不变 |
| 输出起始层级 | 目录树正确，不生成错误目录 |
| 模板变量缺失 | 回退 + 预览提示 |
| 目标已存在 | 不覆盖，给选项 |

---

## 12. 待后续确认（不阻塞 v1.25.0）

> 已确认内容见 `00-decisions.md`；以下仅为规则默认值细化，不影响架构。

1. 默认是否启用七层？还是只启用任务层？
2. 小类目默认 optional 还是 required？
3. 团队/批次是否需要人工确认？
4. 日期格式支持哪些（`2026-11-07`、`20261107`、`2026.11.07`）？
5. 额外层上限默认 2 是否合适？
6. 是否允许规则方案按“根目录”绑定？
7. 输出目录起始层级的默认值？
8. 规则导入/导出是否需要脱敏（含真实路径）？
