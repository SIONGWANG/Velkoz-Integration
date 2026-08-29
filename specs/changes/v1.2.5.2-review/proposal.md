# V1.2.5.2 审查提案

## 背景

V1.2.5.1 完成了 14 项扫描/交互层面的审查修复。本次对质检记录保存可靠性、前端状态联动正确性、用户体感做深层审查。

## P0 修复清单

| 编号 | 问题 | 影响范围 | 修复方案 |
|------|------|----------|----------|
| P0-1 | 7 处 pd.read_csv 缺 encoding | app.py 多处 | 统一加 encoding='utf-8-sig' |
| P0-2 | 截图保存失败中断整体流程 | save_to_disk + render_control_panel | 返回值改为 (bool, str)，失败不中断 |
| P0-3 | CSV 读取失败静默返回空 DataFrame | _get_df | st.warning 升级为 st.error，附带操作建议 |
| P0-4 | _save_df 缺 fsync | _save_df | to_csv 后、replace 前加 _fsync_path |
| P0-5 | _save_df_at 备份逻辑缺失 | _save_df_at | 补上 os.replace(path, bak_file) 备份 |
| P0-6 | pills 状态与 selected_tags 不同步 | sync_state_from_history + reset_task_state | 清除 freq_pills_ 和 other_pills_ 缓存 |
