# V1.2.5.2 审查报告

## 审查维度

1. **数据持久化可靠性**：CSV 读写、原子保存、备份机制
2. **前端状态联动**：pills 状态与 selected_tags 同步
3. **用户体感**：截图保存失败的中断行为、CSV 读取失败的提示

## 审查发现

### P0-1：pd.read_csv 缺 encoding
- **问题**：7 处 pd.read_csv 调用未指定 encoding，在 Windows 环境下默认使用系统编码（GBK），遇到 UTF-8 文件可能乱码
- **位置**：app.py 第 199/1196/1332/1356/1381/1419/1445/1498/3130 行
- **影响**：质检记录读取失败或乱码

### P0-2：截图保存失败中断
- **问题**：save_to_disk 中某张截图保存失败后，st.error 显示但不中断循环，然而整体返回 True，调用方无法区分"全部成功"和"部分失败"
- **位置**：save_to_disk（app.py:1000）+ render_control_panel（app.py:2332）
- **影响**：用户以为全部保存成功，实际部分截图丢失

### P0-3：CSV 读取失败静默返回
- **问题**：_get_df 中文件存在但读取失败时，st.warning 提示过于温和，用户可能忽略
- **位置**：_get_df（app.py:331）
- **影响**：用户不知道数据读取失败，继续操作可能丢失数据

### P0-4：_save_df 缺 fsync
- **问题**：_save_df 写入 tmp 后直接 os.replace，未 fsync，断电可能丢失数据
- **位置**：_save_df（app.py:392）
- **影响**：极端情况下数据丢失

### P0-5：_save_df_at 备份逻辑缺失
- **问题**：_save_df_at 没有备份原文件的逻辑（_save_df 有），原子写入失败时无法回滚
- **位置**：_save_df_at（app.py:1160）
- **影响**：抽检复核记录写入失败时数据丢失

### P0-6：pills 状态与 selected_tags 不同步
- **问题**：sync_state_from_history 恢复标签后，freq_pills_ 和 other_pills_ 缓存未清除，导致 pills 显示与实际标签不一致；reset_task_state 也未清除这两类缓存
- **位置**：sync_state_from_history（app.py:489）+ reset_task_state（app.py:530）
- **影响**：切换图片后 pills 状态残留，用户看到的标签与实际保存的不一致
