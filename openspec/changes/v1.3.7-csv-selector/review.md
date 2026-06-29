# 审查报告：CSV 历史文件选择器

## 改动摘要

| 文件 | 改动类型 | 行数 |
|------|----------|------|
| `data_mixin.py` | 新增方法 + 修改方法 + 修改方法 | +18 行 |
| `settings_mixin.py` | 新增 UI 控件 | +14 行 |

## 改动详情

### data_mixin.py

1. **新增 `_scan_existing_csvs()`**（第 410-425 行）
   - 扫描 `_质检记录/{operator}/` 目录下所有 `*_验收记录.csv`
   - 按文件名倒序排列（日期在文件名中，倒序即最新在前）
   - 异常安全：`OSError` 时返回空列表

2. **修改 `get_csv_filename()`**（第 427-436 行）
   - 新增逻辑：优先检查 `st.session_state.selected_csv`
   - 若已选择且文件存在，直接返回该路径
   - 否则走原有日期逻辑（零影响）

3. **修改 `reset_task_state()`**（第 289 行）
   - 新增 `st.session_state.pop('selected_csv', None)`
   - 重置任务时清除 CSV 选择，回到默认"今天"模式

### settings_mixin.py

4. **`_render_datasource_loader()` 中新增 CSV 选择器**（第 394-407 行）
   - 位于"加载文件夹"按钮之后、"验收组名称预确认"之前
   - 下拉框选项："📅 今天（自动）" + 历史 CSV 文件名
   - 选择"今天"时 `selected_csv = None`，否则存储完整路径

## 兼容性分析

- **缓存机制**：`_get_df()` 的缓存 key 包含文件路径，切换文件自动失效，无需额外处理
- **保存逻辑**：`save_to_disk()` 调用 `get_csv_filename()` 获取路径，选中历史文件后保存会写入该历史文件（符合预期）
- **重置逻辑**：`reset_task_state()` 已清除 `selected_csv`，不会残留

## 风险评估

- **低风险**：改动集中在一个入口方法（`get_csv_filename()`），全局生效
- **向后兼容**：`selected_csv` 为 `None` 时行为与原版完全一致
- **无副作用**：不改变现有缓存、保存、导航逻辑

## 结论

改动最小化、逻辑清晰、向后兼容，可以合入。
