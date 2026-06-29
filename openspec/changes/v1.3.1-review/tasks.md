# V1.3.1 任务清单

## 启动界面
- [x] 新增 `task_type` session_state 变量（`新标` / `返修`）
- [x] 启动界面添加任务类型单选按钮
- [x] 操作员姓名改为必填验证

## 命名函数重构
- [x] 新增 `get_output_prefix()` 函数，返回 `{标签}_{操作员}_{日期}` 前缀
- [x] 新增 `get_evidence_folder_name()` 函数，返回 `_00_Evidence_{标签}`
- [x] 修改 `get_csv_filename()` 使用新命名格式
- [x] 修改 `export_excel_with_images()` 使用新命名格式
- [x] 修改 `export_qualifies_with_check()` 使用新命名格式
- [x] 修改 `export_reject_data()` 使用新命名格式

## 截图证据文件夹
- [x] 修改 `save_to_disk()` 使用动态证据文件夹名称
- [x] 更新扫描逻辑，跳过所有 `_00_Evidence_*` 文件夹

## 测试验证
- [ ] 验证 CSV 命名正确
- [ ] 验证 Excel 命名正确
- [ ] 验证合格导出文件夹命名正确
- [ ] 验证不合格归集文件夹命名正确
- [ ] 验证截图证据文件夹命名正确
- [ ] 更新维护日志
