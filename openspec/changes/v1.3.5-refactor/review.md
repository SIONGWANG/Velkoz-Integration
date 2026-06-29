# 深度审查报告：app.py 拆分可行性

## 1. 方法依赖关系图

### DataMixin 方法自依赖
- `_get_df` → `get_csv_filename`, `_build_lookup`
- `_get_record_by_id` → `get_csv_filename`, `_get_df`
- `_save_df` → `get_csv_filename`
- `sync_state_from_history` → `_get_record_by_id`
- `get_record_status_map` → `_get_active_csv_path`, `_get_df`
- `_get_filtered_ids` → `_get_active_csv_path`, `get_record_status_map`
- `go_to_next_group` → `_get_filtered_ids`, `preload_next_images`
- `save_to_disk` → `get_csv_filename`, `_save_df`
- `start_sampling_acceptance` → (无内部依赖)

### SettingsMixin 方法自依赖
- `_render_settings_panel` → `_render_layout_sliders`, `_render_datasource_loader`, `_render_view_mode_toggle`, `_render_scan_rules_panel`, `_render_hotkeys_section`, `render_fullscreen_button`
- `_render_datasource_loader` → `_save_settings`, `_sanitize_operator_name`
- `render_tag_selector` → `_sync_tags_to_notes` (在 DataMixin 中)

### ExportMixin 方法自依赖
- `render_export_panel` → `export_qualifies_with_check`, `show_reject_confirm_dialog`, `export_excel_with_images`, `show_missing_data_dialog`, `_get_missing_ids`
- `export_qualifies_with_check` → `_get_df`, `get_csv_filename`, `verify_exported_data`
- `show_reject_confirm_dialog` → `export_reject_data`, `get_csv_filename`

### 跨 Mixin 依赖
- SettingsMixin.render_tag_selector → DataMixin._sync_tags_to_notes
- ExportMixin → DataMixin (get_csv_filename, _get_df, _get_missing_ids)
- ZoneA → DataMixin (get_record_status_map, _get_filtered_ids, _get_active_csv_path)
- ZoneB → DataMixin (sync_state_from_history), SettingsMixin (_render_datasource_loader, _render_settings_panel)
- ZoneC → DataMixin (save_to_disk, go_to_next_group, _get_filtered_ids, _sync_tags_to_notes), SettingsMixin (render_tag_selector, open_in_system)

## 2. session_state 使用全景图

### 初始化变量 (38 个)
data_groups, root_path, current_id, last_loaded_id, layout_width, layout_height,
view_mode, focus_img_idx, needs_scroll_top, _batch_completed, _completion_balloons_shown,
filter_pills, qa_df, qa_source, error_screenshots, uploaded_file_tokens,
generated_excel_path, is_navigating, is_scanning, scan_cache_buster, categories_config,
scan_rules, operator_name, task_type, sticky_l1, sticky_l2, _saved_data_groups,
_saved_current_id, sampling_acceptance_mode, sampling_acceptance_ratio, confirm_pending,
pending_groups, annotator_map, annotator_confirm_enabled, annotator_inclusion,
custom_tags, selected_tags

### 动态 key 模式 (按 ID 生成)
- feedback_{id}, zh_{id}, en_{id}
- evidence_pool_{id}
- paste_btn_{id}_{n}, clr_{id}
- freq_pills_{id}, other_pills_{id}
- _last_tags_{id}, _manual_edit_{id}, _pending_tag_sync_{id}
- form_submit_{id}

### 废弃变量 (可删除)
- `is_navigating` — 仅初始化，从未使用
- `error_screenshots` — 仅在 reset_task_state 中重置，从未读取
- `uploaded_file_tokens` — 同上
- `generated_excel_path` — 同上

## 3. 渲染架构树

```
run()
├── CSS/JS 注入 (→ css_styles.py)
├── A 区 (col_a)
│   ├── render_a_zone()         (→ zone_a.py)
│   ├── render_inspection_panel() (→ export_mixin.py)
│   ├── render_export_panel()   (→ export_mixin.py)
│   ├── AI 预识别
│   ├── _render_settings_panel() (→ settings_mixin.py)
│   └── render_category_manager() (→ settings_mixin.py)
├── B 区 (col_b)
│   ├── render_cold_start_animation() (→ zone_b.py)
│   ├── render_confirm_panel()   (→ zone_b.py)
│   ├── render_b_image_area()    (→ zone_b.py)
│   └── render_completion_panel() (→ zone_b.py)
└── C 区 (col_c)
    └── render_control_panel()   (→ zone_c.py)
```

## 4. 死代码识别

| 变量 | 状态 | 原因 |
|------|------|------|
| is_navigating | 废弃 | 仅初始化，从未读取 |
| error_screenshots | 废弃 | 仅在 reset 中重置 |
| uploaded_file_tokens | 废弃 | 仅在 reset 中重置 |
| generated_excel_path | 废弃 | 仅在 reset 中重置 |
