# V1.1.7 修复任务清单

## 1. 冷启动版本号修复（P0）

- [ ] 1.1 `render_cold_start_animation` else 分支添加 `f` 前缀 → `anim_html += f"""` ✅ `app.py:765`
- [ ] 1.2 `page_title` 改用动态版本号 `os.path.basename(BASE_DIR)` ✅ `app.py:2785`

## 2. app.run() 调用修复（P0）

- [ ] 2.1 `__main__` 块中添加 `app.run()` ✅ `app.py:3199`

## 3. 标签同步竞态修复（P0）

- [ ] 3.1 将 `_manual_edit_` 检测逻辑移入 `_sync_tags_to_notes` 内部
- [ ] 3.2 在 `reset_task_state` 的 `prefixes` 中添加 `"_manual_edit_"` ✅ `app.py:517`
- [ ] 3.3 验证标签→手动编辑→切换标签的完整流程

## 4. 异常处理健壮性（P1）

- [ ] 4.1 `preload_next_images` 裸 `except` → `except Exception` + 日志 ✅ `app.py:189`
- [ ] 4.2 `_get_missing_ids` 裸 `except` → `except Exception` + 日志 + 添加 encoding ✅ `app.py:1296`
- [ ] 4.3 `_save_settings` 添加 try/except ✅ `app.py:301`

## 5. 备份策略统一（P1）

- [ ] 5.1 `_save_df_at` 保留 `.bak` 文件（与 `_save_df` 行为一致）✅ `app.py:1144`

## 6. 交互鲁棒性优化（P1）

- [ ] 6.1 标签选择器使用 `st.fragment` 避免全局 rerun ✅ `app.py:2138`
- [ ] 6.2 扫描规则空后缀过滤 ✅ `app.py:2709`

## 7. 验证

- [ ] 7.1 冷启动页面显示正确版本号
- [ ] 7.2 `python app.py` 能正常启动
- [ ] 7.3 标签选择→手动编辑备注→切换标签，备注不被意外覆盖
- [ ] 7.4 切换任务后 `_manual_edit_` 键被清除
- [ ] 7.5 预加载失败时有日志输出
