# OpenSpec 审查报告 — 审视之眼 V1.1.2

> 审查时间：2026-05-25
> 审查范围：`app.py` 全量（3136 行）
> 审查方法：逐模块代码审查 + 调用链追踪 + 边界场景推演

---

## 一、功能模块概览

| 模块 | 核心功能 | 状态 |
|------|----------|------|
| 文件扫描 | 递归扫描目录，识别图片组和文本文件 | ✅ 正常 |
| 操作员隔离 | 不同操作员生成独立 CSV，避免并发冲突 | ✅ 正常 |
| 标注员确认 | 多标注员场景下的名称确认和过滤 | ✅ 正常 |
| 分类管理 | L1/L2 分类的增删改查和持久化 | ✅ 正常 |
| 标签系统 | 快捷标签选择、同步到备注 | ⚠️ 有问题 |
| 抽检复核 | 从已有记录中抽样复核 | ✅ 正常 |
| 抽检验收 | 从全量数据中抽样验收 | ✅ 正常 |
| 导出功能 | 合格数据导出、不合格归集、Excel 报表 | ⚠️ 有问题 |
| AI 预识别 | 加载质检表显示 AI 预判结果 | ✅ 正常 |
| 快捷键 | 键盘快捷键支持 | ⚠️ 有风险 |

---

## 二、发现的问题

### 🔴 严重问题

#### 问题 1：标签同步逻辑可能导致备注被意外覆盖

**位置**：`_sync_tags_to_notes`（app.py:412-441）

**现象**：
- 用户手动编辑备注后，切换标签时可能覆盖用户输入
- `_last_tags_` 标记基于字符串前缀匹配，如果用户备注恰好以标签文本开头，会被误判

**复现步骤**：
1. 选择标签 "模糊"
2. 手动修改备注为 "模糊区域需要重拍"
3. 取消标签 "模糊"，选择其他标签
4. 用户输入的 "模糊区域需要重拍" 被意外修改

**建议**：改用更可靠的标签-备注分离机制，或在用户手动编辑后禁止自动同步。

---

#### 问题 2：抽检复核模式下 `submit` 按钮跳转逻辑缺陷

**位置**：`render_control_panel`（app.py:2339-2347）

**现象**：
```python
if curr_idx < len(all_ids) - 1:
    st.session_state.current_id = all_ids[curr_idx + 1]
```
- `all_ids` 来自 `nav_groups`（可能是 inspection_data_groups）
- 但 `curr_idx` 是从 `nav_ids`（filtered_ids）计算的
- 当筛选条件不是"全部"时，`all_ids[curr_idx + 1]` 可能跳到错误的条目

**影响**：抽检复核时使用筛选功能，提交后可能跳转到非预期的下一条。

**建议**：统一使用 `filtered_ids` 或 `nav_ids` 进行跳转，而非 `all_ids`。

---

### 🟡 中等问题

#### 问题 3：分页逻辑中 `current_idx` 计算未考虑筛选后的偏移

**位置**：`render_a_zone`（app.py:1935-1936）

**现象**：
```python
current_idx = filtered_ids.index(st.session_state.current_id) if st.session_state.current_id in filtered_ids else 0
current_page = current_idx // PAGE_SIZE + 1
```
- 当 `current_id` 不在 `filtered_ids` 中时，默认跳到第 1 页第 1 项
- 但如果用户刚切换筛选条件，可能期望保持在当前页码

**影响**：切换筛选条件时，列表可能跳回第 1 页，体验不流畅。

---

#### 问题 4：导出功能未处理网络路径（UNC）的特殊情况

**位置**：`export_qualifies_with_check`（app.py:1515-1520）

**现象**：
```python
if raw_path.startswith('\\\\'):
    parent_dir = os.path.dirname(raw_path.rstrip('\\'))
    if parent_dir == raw_path.rstrip('\\'):
        parent_dir = os.path.dirname(raw_path)
```
- UNC 路径如 `\\server\share\folder` 处理逻辑存在边界问题
- 当路径为 `\\server\share` 时，`parent_dir` 会变成 `\\server`，可能不是预期行为

**建议**：增加 UNC 路径的专门处理逻辑，或提示用户使用映射盘符。

---

#### 问题 5：图片缓存未设置内存上限

**位置**：`get_display_image_bytes`（app.py:139-163）

**现象**：
```python
if len(cache) > 50:
    oldest = next(iter(cache))
    del cache[cache]
```
- 仅限制了条目数量（50），未考虑单条目大小
- 高分辨率图片（如 4K）的 JPEG bytes 可能达到数 MB
- 50 张 4K 图片的缓存可能占用 200MB+ 内存

**建议**：增加总内存上限检查，或改用 LRU 策略。

---

### 🟢 轻微问题

#### 问题 6：快捷键实现依赖 DOM 操作，可能与 Streamlit 内部冲突

**位置**：`inject_hotkeys`（app.py:856-908）

**现象**：
- 通过 `window.parent.document` 访问父文档
- 监听 `keydown` 事件并模拟按钮点击
- Streamlit 版本更新可能改变 DOM 结构，导致选择器失效

**风险**：Streamlit 升级后快捷键可能失效。

**建议**：增加版本检测和降级方案。

---

#### 问题 7：`_get_df` 缓存失效时机不够精确

**位置**：`save_to_disk`（app.py:1012-1017）

**现象**：
```python
cache_key = f"_df_cache_{csv_file}"
st.session_state.pop(cache_key, None)
```
- 保存后立即清除缓存
- 但如果同一轮 Streamlit 渲染中有多个组件读取 DataFrame，可能读到不一致的状态

**影响**：极低概率下，A 区统计和 B 区显示可能不同步。

---

#### 问题 8：冷启动动画中版本号硬编码

**位置**：`render_cold_start_animation`（app.py:733）

**现象**：
```python
<div class="coldstart-title">审视之眼 V1.1.0</div>
```
- 版本号硬编码为 V1.1.0，实际版本为 V1.1.2
- 底部版本号 `v{version}` 正确读取自目录名

**建议**：统一使用动态版本号。

---

## 三、调用链覆盖验证

### `get_csv_filename` — 14 个调用点

| 行号 | 调用函数 | 操作员隔离生效 |
|------|----------|----------------|
| 316 | `_get_df`（默认） | ✅ |
| 507 | `get_record_status_map` | ✅ |
| 515 | `_get_active_csv_path` | ✅ |
| 920 | `save_to_disk`（内部） | ✅ |
| 1020 | `get_inspection_csv_path` | ✅ |
| 1124 | `start_inspection` | ✅ |
| 1253 | `_get_missing_ids` | ✅ |
| 1265 | `export_reject_data` | ✅ |
| 1307 | `show_reject_confirm_dialog` | ✅ |
| 1330 | `export_qualified_data` | ✅ |
| 1369 | `render_inspection_panel` | ✅ |
| 1449 | `render_inspection_panel` | ✅ |
| 1482 | `export_qualifies_with_check` | ✅ |
| 2383 | `render_completion_panel` | ✅ |

**结论：所有 CSV 路径均经过 `get_csv_filename`，操作员隔离无遗漏。**

---

## 四、Settings 持久化链路

```
_load_settings (line 279)  →  __init__ (line 226-227)  →  session_state.operator_name
_save_settings (line 292)  ←  _render_datasource_loader (line 2578-2579)
                           ←  冷启动更多设置 (line 3090-3091)
```

- ✅ `__init__` 中用 `if 'operator_name' not in st.session_state` 保护
- ✅ `_load_settings` 返回默认值含 `"operator_name": ""`
- ✅ `_save_settings` 写入 `operator_name`
- ✅ 两处 UI 入口变化时都调 `_save_settings()`

---

## 五、边界场景测试

| 场景 | 预期行为 | 实际行为 | 结果 |
|------|----------|----------|------|
| 操作员名为空 | 禁用加载按钮 | ✅ 禁用 | PASS |
| 操作员名含非法字符 | 自动剔除 | ✅ `_sanitize_operator_name` | PASS |
| CSV 文件被占用 | 提示错误 | ✅ 重试 3 次后报错 | PASS |
| 空文件夹扫描 | 提示无数据 | ✅ 显示警告 | PASS |
| 筛选后无数据 | 提示切换筛选 | ✅ 显示提示 | PASS |
| 导出时路径不存在 | 跳过并记录 | ✅ 记入 missing | PASS |
| 抽检比例为 0% | 至少抽 1 条 | ✅ `max(1, ...)` | PASS |

---

## 六、验收结论

| 检查项 | 结果 |
|--------|------|
| 操作员隔离全覆盖 | ✅ PASS |
| Settings 持久化 | ✅ PASS |
| 标注员确认流程 | ✅ PASS |
| 抽检/抽检验收互斥 | ✅ PASS |
| 导出数据验证 | ✅ PASS |
| 标签同步逻辑 | ⚠️ 有问题 |
| 提交跳转逻辑 | ⚠️ 有缺陷 |
| 缓存内存控制 | ⚠️ 不足 |
| 快捷键兼容性 | ⚠️ 有风险 |
| 版本号一致性 | ❌ 硬编码错误 |

**总体评价：核心功能完整，操作员隔离和数据持久化实现可靠。但标签同步、提交跳转和缓存控制存在需要修复的问题。**

---

## 七、第二轮审查 — 数据保存与导出（2026-05-25）

### 🔴 严重问题

#### 问题 9：`save_to_disk` 读取 CSV 未指定编码

**位置**：`app.py:987`

**现象**：
```python
df = pd.read_csv(csv_file, dtype=str)  # ❌ 无 encoding 参数
```
而 `_save_df` 写入时使用 `encoding='utf-8-sig'`，`save_inspection_result` 读取时也指定 `encoding='utf-8-sig'`。

**风险**：如果 CSV 文件包含 BOM 头（utf-8-sig 写入的），用默认编码读取可能将 BOM 字符写入第一列列名，导致列名错位。

**修复**：添加 `encoding='utf-8-sig'`

---

#### 问题 10：`export_excel_with_images` 导出全量数据

**位置**：`app.py:1722`

**现象**：
```python
df = self._get_df(csv_file)  # ❌ 获取 CSV 全量数据
```
没有过滤当前数据组 `all_ids`，导出的 Excel 包含其他批次的记录。

**影响**：用户导出的 Excel 报表中可能混入其他任务的数据。

**修复**：添加 `df = df[df['图片ID'].astype(str).isin(set(all_ids))]`

---

### 🟡 中等问题

#### 问题 11：`_save_df` 缺少重试机制

**位置**：`app.py:372-392`

**现象**：`_save_df` 在 `os.replace` 失败时直接抛异常，而 `_save_df_at` 有 3 次重试。两者行为不一致。

**影响**：Windows 下文件被占用时，`_save_df` 可能失败而 `_save_df_at` 能成功。

**建议**：统一重试机制。

---

#### 问题 12：`save_to_disk` 图片证据保存路径问题

**位置**：`app.py:952`

**现象**：
```python
evidence_dir = os.path.join(BASE_DIR, EVIDENCE_FOLDER_NAME)
```
证据图片保存到 `BASE_DIR/_错误截图/`，但 CSV 中记录的是相对路径 `EVIDENCE_FOLDER_NAME/filename`。如果 BASE_DIR 与 CSV 所在目录不同，导出时路径会断裂。

**影响**：操作员隔离模式下，CSV 在 `_质检记录/{operator}/` 但证据在 `BASE_DIR/_错误截图/`，路径不一致。

---

### 🟢 轻微问题

#### 问题 13：`_save_df_at` 会删除备份文件

**位置**：`app.py:1124-1125`

**现象**：
```python
if os.path.isfile(bak_file):
    os.remove(bak_file)
```
写入成功后立即删除 `.bak` 文件，失去了备份保护。而 `_save_df` 不删除 `.bak`。

---

## 八、修复优先级

| 优先级 | 问题 | 状态 |
|--------|------|------|
| P0 | 问题 2：提交跳转逻辑缺陷 | ✅ 已修复 V1.1.3 |
| P0 | 问题 1：标签同步覆盖备注 | ✅ 已修复 V1.1.3 |
| P0 | 问题 9：save_to_disk 编码不一致 | ⬜ 待修复 |
| P0 | 问题 10：Excel 导出全量数据 | ⬜ 待修复 |
| P1 | 问题 5：缓存内存上限 | ⬜ 待修复 |
| P1 | 问题 8：版本号硬编码 | ⬜ 待修复 |
| P1 | 问题 11：_save_df 缺少重试 | ⬜ 待修复 |
| P1 | 问题 12：证据路径不一致 | ⬜ 待修复 |
| P2 | 问题 3：分页筛选联动 | ⬜ 待修复 |
| P2 | 问题 4：UNC 路径处理 | ⬜ 待修复 |
| P2 | 问题 13：备份文件删除策略 | ⬜ 待修复 |
| P3 | 问题 6：快捷键兼容性 | ⬜ 待修复 |
| P3 | 问题 7：缓存失效时机 | ⬜ 待修复 |
