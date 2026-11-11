# 数据库设计文档（SQLite / 管理索引）

> 目标版本：v1.25.0 起
> 数据库：SQLite 3（Python 标准库 `sqlite3`）
> 默认位置：`D:\VelkozDATA\data\management.db`
> 原则：**只存路径、元信息、状态和索引，不存图片/原始 TXT/原始 CSV 内容**

---

## 1. 设计原则

1. **本地优先**：单机 SQLite，不要求服务器/MySQL。
2. **不替代原始数据**：图片、TXT、CSV 仍在文件系统；数据库只做索引。
3. **不改旧 CSV**：v1.25.x 期间 CSV 是验收记录权威来源；数据库是派生索引。
4. **可追溯**：验收事件、导入、检查、归档、状态变化都有记录。
5. **可重建**：扫描/检查/统计索引可从文件重建；原始事件不可重建的才做权威存储。
6. **可迁移**：版本化迁移 + 迁移前备份 + 失败回滚。
7. **可恢复**：自动/手动备份，`PRAGMA integrity_check`，恢复向导。
8. **不共享写**：默认不支持 SMB 多端高并发写；共享模式另设计。
9. **安全**：密钥不进数据库明文；日志不写敏感内容。

---

## 2. 数据库位置与运行参数

### 2.1 位置

```text
默认：D:\VelkozDATA\data\management.db
可配置：设置 → 数据目录
禁止：程序安装目录、EXE 临时目录、UNC 共享目录（默认）
```

### 2.2 初始化参数

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
PRAGMA temp_store = MEMORY;
PRAGMA integrity_check;   -- 启动/备份时
```

### 2.3 文件布局

```text
data/
├── management.db
├── management.db-wal
├── management.db-shm
backups/
├── management_20261107_103000.db
└── ...
```

---

## 3. 身份模型（核心）

1.25.x 明确要求：**不能再用图片 ID 作为所有历史记录的唯一主键。**

### 3.1 四层身份

| 身份 | 含义 | 示例 |
|---|---|---|
| `data_group` | 数据本身（一个任务目录） | 批次 A / 任务 12345678 |
| `batch` | 数据所在批次 | 供应商 X / 2026-11-07 / batch_20261107_001 |
| `acceptance_event` | 一次验收事件（人 + 时间 + 结果） | 张三 2026-11-07 14:00 合格 |
| `record_import` | 一次外部记录导入 | 导入李四的 CSV |

### 3.2 唯一键

```text
data_group:
  UNIQUE(batch_id, task_id, normalized_path)

acceptance_event:
  UNIQUE(data_group_id, operator, event_time, result_hash)
  -- 不覆盖历史；同一数据可多次验收

record_import_row:
  UNIQUE(import_id, row_fingerprint)
  -- 增量导入；同一 CSV 新增行可识别
```

### 3.3 ID 生成

- 业务 ID 用 UUIDv4/ULID 字符串（TEXT），避免自增跨库冲突；
- 显示用短 ID（如 `B-20261107-001`）单独存 `display_id`；
- 时间统一 ISO 8601（本地时区 + 偏移），另存 `date` 便于统计。

---

## 4. 实体关系

```text
suppliers 1─* teams 1─* batches 1─* sub_batches
                              │
                              ├─* scan_runs ─* scan_candidates
                              ├─* data_groups ─* data_group_files
                              │        │
                              │        └─* acceptance_events ─* acceptance_event_tags
                              │                    │
                              │                    └─* acceptance_event_evidence
                              ├─* format_check_runs ─* format_check_task_results ─* format_check_issues
                              ├─* translation_jobs ─* translation_items
                              ├─* archive_jobs ─* archive_items
                              └─* record_imports ─* imported_records
projects 1─* batches
categories (L1/L2) 1─* batches
scan_rule_sets 1─* scan_rule_versions 1─* scan_runs
format_templates 1─* format_template_versions 1─* format_check_runs
operation_logs（独立，关联任意 run/job）
```

---

## 5. 表设计

> 以下为逻辑 schema；实际 DDL 在 `modules/database/migrations/` 中版本化。字段类型为 SQLite 亲和类型。

### 5.1 基础/组织

#### `schema_migrations`

| 列 | 类型 | 说明 |
|---|---|---|
| version | INTEGER PK | 迁移版本 |
| applied_at | TEXT | 应用时间 |
| checksum | TEXT | 迁移文件校验 |

#### `app_settings`

| 列 | 类型 | 说明 |
|---|---|---|
| key | TEXT PK | 配置键 |
| value | TEXT | JSON/字符串 |
| updated_at | TEXT | |

#### `suppliers`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| name | TEXT UNIQUE | 供应商名 |
| contact | TEXT | 可选 |
| note | TEXT | |
| created_at/updated_at | TEXT | |

#### `teams`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| supplier_id | TEXT FK | 可空 |
| name | TEXT | |
| note | TEXT | |
| UNIQUE(supplier_id, name) | | |

#### `projects`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| name | TEXT | |
| note | TEXT | |

#### `categories`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| level | INTEGER | 1/2 |
| parent_id | TEXT FK | L2 指向 L1 |
| name | TEXT | |
| sort_order | INTEGER | |
| UNIQUE(level, parent_id, name) | | |

### 5.2 批次

#### `batches`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| display_id | TEXT | 人读 ID |
| name | TEXT | 批次名 |
| project_id | TEXT FK | |
| supplier_id | TEXT FK | |
| team_id | TEXT FK | |
| category_l1_id | TEXT FK | |
| category_l2_id | TEXT FK | |
| submit_date | TEXT | 提交日期 |
| data_root | TEXT | 数据根路径 |
| data_count | INTEGER | 数据组数 |
| format_status | TEXT | not_checked/partial/passed/failed |
| translation_status | TEXT | not_started/partial/done/failed |
| acceptance_status | TEXT | not_started/in_progress/done/reopened |
| qualified_count | INTEGER | |
| rejected_count | INTEGER | |
| remaining_count | INTEGER | |
| status | TEXT | registered/downloaded/scan_pending/confirmed/translating/accepting/archived/paused |
| source | TEXT | manual/scan/import |
| source_type | TEXT | v1.25.8：pending_acceptance/final_acceptance/legacy |
| source_root_id | TEXT | v1.25.8：项目目录配置 ID |
| source_root_path | TEXT | v1.25.8：本次扫描的业务根目录 |
| relative_path | TEXT | v1.25.8：批次相对业务根目录的路径 |
| parent_batch_id | TEXT | v1.25.8：申请终验关联的原始待验批次 |
| project_name | TEXT | v1.25.8：项目名称快照 |
| created_at/updated_at | TEXT | |
| UNIQUE(project_id, name, data_root) | | |

> v1.25.8 迁移 `0008_workspace.sql` 增量添加 `source_type/source_root_id/source_root_path/relative_path/parent_batch_id/project_name`；旧批次默认 `legacy`，不会因迁移丢失。

#### `sub_batches`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| batch_id | TEXT FK | |
| name | TEXT | |
| data_root | TEXT | |
| data_count | INTEGER | |
| status | TEXT | |
| note | TEXT | |

#### `batch_status_log`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| batch_id | TEXT FK | |
| from_status/to_status | TEXT | |
| operator | TEXT | |
| reason | TEXT | |
| created_at | TEXT | |

### 5.3 供应商提交

#### `supplier_submissions`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| supplier_id | TEXT FK | |
| team_id | TEXT FK | |
| category_l1_id/l2_id | TEXT FK | |
| submit_date | TEXT | |
| netdisk_url | TEXT | 网盘链接 |
| batch_name | TEXT | |
| expected_count | INTEGER | |
| actual_count | INTEGER | |
| download_path | TEXT | |
| extracted | INTEGER | 0/1 |
| format_status | TEXT | |
| note | TEXT | |
| created_at/updated_at | TEXT | |

### 5.4 扫描规则

#### `scan_rule_sets`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| name | TEXT | 规则方案名 |
| scope | TEXT | global/supplier/team/project |
| scope_id | TEXT | 对应实体 ID |
| active_version_id | TEXT | 当前版本 |
| is_default | INTEGER | |
| created_at/updated_at | TEXT | |

#### `scan_rule_versions`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| rule_set_id | TEXT FK | |
| version | INTEGER | |
| config_json | TEXT | 目录层级/命名/槽位 |
| note | TEXT | |
| created_at | TEXT | |
| created_by | TEXT | |
| UNIQUE(rule_set_id, version) | | |

#### `scan_runs`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| root_path | TEXT | |
| rule_set_id | TEXT FK | |
| rule_version_id | TEXT FK | |
| started_at/finished_at | TEXT | |
| status | TEXT | running/completed/cancelled/failed |
| total_dirs | INTEGER | |
| candidate_count | INTEGER | |
| group_count | INTEGER | |
| issue_count | INTEGER | |
| operator | TEXT | |
| summary_json | TEXT | 诊断统计 |

#### `scan_candidates`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| run_id | TEXT FK | |
| batch_path | TEXT | 候选批次目录 |
| batch_name | TEXT | |
| identity_json | TEXT | 大类目/小类目/团队/日期/批次 |
| status | TEXT | pending/confirmed/ignored/abnormal/partial/duplicate/rescan |
| confidence | REAL | 0–1 |
| reason_json | TEXT | 匹配/跳过原因 |
| group_count | INTEGER | |
| file_count | INTEGER | |
| confirmed_batch_id | TEXT FK | 确认后关联 |
| confirmed_by/confirmed_at | TEXT | |

#### `scan_run_items`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| run_id | TEXT FK | |
| path | TEXT | |
| item_type | TEXT | task/group/skipped/issue |
| task_id | TEXT | |
| reason_code | TEXT | |
| detail_json | TEXT | |

### 5.5 数据组

#### `data_groups`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| batch_id | TEXT FK | |
| sub_batch_id | TEXT FK | 可空 |
| task_id | TEXT | 原图片 ID/文件夹名 |
| normalized_path | TEXT | 规范化绝对路径 |
| root_path | TEXT | |
| user_name | TEXT | 推断的标注员/团队 |
| image_count | INTEGER | |
| txt_count | INTEGER | |
| file_count | INTEGER | |
| fingerprint | TEXT | 目录快照哈希 |
| status | TEXT | pending/format_ok/format_failed/translating/translating_done/inspecting/done/reopened |
| first_seen_at/last_seen_at | TEXT | |
| UNIQUE(batch_id, task_id, normalized_path) | | |

#### `data_group_files`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| data_group_id | TEXT FK | |
| role | TEXT | primary/result/psd/zh/en/other |
| file_name | TEXT | |
| relative_path | TEXT | |
| size | INTEGER | |
| mtime_ns | INTEGER | |
| ext | TEXT | |
| fingerprint | TEXT | |

### 5.6 验收事件

#### `acceptance_events`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| data_group_id | TEXT FK | |
| batch_id | TEXT FK | 冗余便于查询 |
| operator | TEXT | 验收员 |
| result | TEXT | 合格/修改后合格/不合格/待定 |
| l1/l2 | TEXT | 分类 |
| note | TEXT | 备注原文 |
| source | TEXT | local_csv/excel_import/other_csv |
| source_file | TEXT | 来源文件（可空） |
| source_row_hash | TEXT | 去重 |
| event_time | TEXT | |
| created_at | TEXT | |
| UNIQUE(data_group_id, operator, event_time, source_row_hash) | | |

#### `acceptance_event_tags`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| event_id | TEXT FK | |
| raw_text | TEXT | 原始标签文本 |
| normalized_text | TEXT | 仅用于检索 |
| source | TEXT | local/import |
| tag_definition_id | TEXT | 可空；本地没有定义也保留 |

#### `acceptance_event_evidence`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| event_id | TEXT FK | |
| relative_path | TEXT | 原始相对路径 |
| absolute_path | TEXT | 解析后的绝对路径（可空） |
| exists_at_import | INTEGER | |
| note | TEXT | 无法解析时说明 |

#### `acceptance_sessions`（可选）

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| operator | TEXT | |
| batch_id | TEXT FK | |
| started_at/ended_at | TEXT | |
| csv_path | TEXT | |
| status | TEXT | |

### 5.7 格式检查

#### `format_templates`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| name | TEXT | |
| scope | TEXT | global/supplier/team/category/project |
| scope_id | TEXT | |
| active_version_id | TEXT | |
| builtin | INTEGER | |
| created_at/updated_at | TEXT | |

#### `format_template_versions`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| template_id | TEXT FK | |
| version | INTEGER | |
| config_json | TEXT | 模板快照 |
| note | TEXT | |
| created_at/created_by | TEXT | |
| UNIQUE(template_id, version) | | |

#### `format_check_runs`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| batch_id | TEXT FK | 可空（临时检查） |
| root_path | TEXT | |
| template_id | TEXT FK | |
| template_version_id | TEXT FK | |
| rule_snapshot_json | TEXT | |
| started_at/finished_at | TEXT | |
| status | TEXT | running/completed/cancelled/failed |
| scanned_tasks/passed_tasks/failed_tasks/warning_tasks/skipped_tasks | INTEGER | |
| integrity_status | TEXT | ok/warning |
| report_path | TEXT | |
| operator | TEXT | |
| previous_run_id | TEXT | 重跑来源 |
| summary_json | TEXT | 错误码统计 |

#### `format_check_task_results`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| run_id | TEXT FK | |
| data_group_id | TEXT FK | 可空 |
| task_id | TEXT | |
| task_path | TEXT | |
| detected_mode | TEXT | single/multi |
| result | TEXT | pass/fail/warn/skipped |
| image_count/txt_count/file_count | INTEGER | |
| issue_count | INTEGER | |
| fingerprint | TEXT | |
| UNIQUE(run_id, task_path) | | |

#### `format_check_issues`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| run_id | TEXT FK | |
| task_result_id | TEXT FK | |
| code | TEXT | FC-xxx |
| severity | TEXT | error/warn/info |
| file_path | TEXT | |
| expected/actual | TEXT | |
| message | TEXT | |
| suggestion | TEXT | |

### 5.8 翻译/预审

#### `translation_jobs`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| batch_id | TEXT FK | |
| provider | TEXT | ark/dashscope/openai_compat/external_report |
| model | TEXT | |
| status | TEXT | pending/running/partial/done/failed/cancelled |
| prompt_version | TEXT | 翻译 Prompt 版本 |
| started_at/finished_at | TEXT | |
| operator | TEXT | |
| source_report | TEXT | 外部历史报告路径（仅导入任务） |
| note | TEXT | |

#### `translation_items`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| job_id | TEXT FK | |
| data_group_id | TEXT FK | 可空 |
| task_id | TEXT | |
| folder_path | TEXT | |
| zh_txt_path/en_txt_path | TEXT | |
| status | TEXT | no_zh/pending/translated/failed/skipped |
| model | TEXT | |
| translated_at | TEXT | |
| grammar_checked | INTEGER | |
| grammar_passed | INTEGER | |
| consistency_checked | INTEGER | |
| consistency_passed | INTEGER | |
| suggestion | TEXT | 智能修正建议（不自动写） |
| manual_fixed | INTEGER | |
| error_message | TEXT | |
| token_input/token_output | INTEGER | |
| cost | REAL | |
| report_row_hash | TEXT | 去重 |

#### `translation_usage`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| job_id | TEXT FK | |
| date/time | TEXT | |
| api_type/endpoint | TEXT | endpoint 脱敏 |
| total/success/failed | INTEGER | |
| token_in/token_out/token_total | INTEGER | |
| cost | REAL | |
| currency | TEXT | |
| note | TEXT | |
| source_report | TEXT | |
| row_hash | TEXT | |
| UNIQUE(row_hash) | | |

### 5.9 记录导入

#### `record_imports`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| source_path | TEXT | 原文件路径（只读） |
| source_type | TEXT | legacy_csv/export_csv/other_stats/backup |
| file_size | INTEGER | |
| file_mtime_ns | INTEGER | |
| file_hash | TEXT | |
| rows_total/rows_new/rows_skipped/rows_error | INTEGER | |
| operator_detected | TEXT | |
| batch_id | TEXT FK | 可空 |
| status | TEXT | completed/partial/failed |
| imported_at | TEXT | |
| imported_by | TEXT | |
| note | TEXT | |

#### `imported_records`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| import_id | TEXT FK | |
| data_group_id | TEXT FK | 可空（无法匹配时保留） |
| task_id | TEXT | |
| operator | TEXT | |
| result | TEXT | |
| l1/l2 | TEXT | |
| note | TEXT | |
| tags_raw | TEXT | 原样 |
| evidence_raw | TEXT | 原样 |
| record_time | TEXT | |
| source_row_fingerprint | TEXT | |
| accepted_event_id | TEXT FK | 生成的验收事件 |
| UNIQUE(import_id, source_row_fingerprint) | | |

### 5.10 归档

#### `archive_jobs`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| batch_id | TEXT FK | |
| source_root | TEXT | |
| output_root | TEXT | |
| start_layer | TEXT | root/大类目/小类目/团队/日期/批次名/自定义 |
| name_template | TEXT | |
| status | TEXT | preview/running/paused/completed/failed |
| total_items/copied_items/skipped_items/failed_items | INTEGER | |
| bytes_total/bytes_copied | INTEGER | |
| created_at/started_at/finished_at | TEXT | |
| operator | TEXT | |
| note | TEXT | |

#### `archive_items`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| job_id | TEXT FK | |
| data_group_id | TEXT FK | |
| source_path/dest_path | TEXT | |
| status | TEXT | pending/copied/skipped/conflict/failed/verified |
| file_count/bytes | INTEGER | |
| source_fingerprint | TEXT | |
| dest_fingerprint | TEXT | |
| error_message | TEXT | |
| updated_at | TEXT | |

### 5.11 统计/日志

#### `project_roots`（v1.25.8）

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| project_name | TEXT | 项目名称 |
| parent_path | TEXT | 可选共同父目录 |
| pending_path | TEXT | 02-待验数据 |
| final_path | TEXT | 03-申请终验 |
| enabled | INTEGER | 是否启用 |
| note | TEXT | |
| last_scan_at | TEXT | |
| pending_scan_at | TEXT | |
| final_scan_at | TEXT | |
| created_at/updated_at | TEXT | |

> 每个项目独立配置；修改/删除配置不会删除历史批次。扫描记录 `scan_runs` 同步增加 `source_type/project_root_id/project_name`。

#### `stats_daily`（缓存，可重建）

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| date | TEXT | |
| batch_id | TEXT | 可空 |
| operator | TEXT | 可空 |
| new_batches | INTEGER | 当日新增批次 |
| new_groups | INTEGER | 当日新增数据组 |
| format_checked | INTEGER | |
| format_failed | INTEGER | |
| format_warnings | INTEGER | |
| translated | INTEGER | |
| translation_failed | INTEGER | |
| accepted | INTEGER | |
| qualified | INTEGER | |
| rejected | INTEGER | |
| archived | INTEGER | |
| pending | INTEGER | |
| built_at | TEXT | 缓存重建时间 |
| source | TEXT | live/rebuild |
| UNIQUE(date, COALESCE(batch_id,''), COALESCE(operator,'')) | | 作用域唯一 |

> v1.25.7 迁移 `0007_stats.sql`。缓存只保存按日聚合值，可按源表重建；不保存图片/TXT/CSV 原文。

#### `operation_logs`

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| created_at | TEXT | |
| operator | TEXT | |
| module | TEXT | scan/format/import/archive/... |
| action | TEXT | |
| batch_id/job_id/run_id | TEXT | 关联 |
| source_path/target_path | TEXT | |
| result | TEXT | success/fail/partial |
| error_code | TEXT | |
| message | TEXT | 脱敏 |
| detail_json | TEXT | |

#### `external_tools`（可选）

| 列 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | |
| name | TEXT | ai_precheck/image_toolbox |
| path | TEXT | |
| version | TEXT | |
| last_seen_at | TEXT | |
| last_health | TEXT | |
| note | TEXT | 不存密钥 |

---

## 6. 索引建议

```sql
CREATE INDEX idx_batches_status ON batches(status);
CREATE INDEX idx_data_groups_batch ON data_groups(batch_id);
CREATE INDEX idx_data_groups_task ON data_groups(task_id);
CREATE INDEX idx_events_group ON acceptance_events(data_group_id);
CREATE INDEX idx_events_operator_time ON acceptance_events(operator, event_time);
CREATE INDEX idx_events_batch_result ON acceptance_events(batch_id, result);
CREATE INDEX idx_issue_run_code ON format_check_issues(run_id, code);
CREATE INDEX idx_task_results_run_result ON format_check_task_results(run_id, result);
CREATE INDEX idx_import_records_task ON imported_records(task_id);
CREATE INDEX idx_archive_items_job_status ON archive_items(job_id, status);
CREATE INDEX idx_logs_time ON operation_logs(created_at);
```

---

## 7. 迁移

### 7.1 机制

```text
modules/database/migrations/
├── 0001_initial.sql
├── 0002_add_format_checker.sql
└── ...
```

- 启动时读 `schema_migrations`；
- 按版本顺序执行；
- 每个迁移在事务中；
- 执行前自动备份；
- 失败回滚 + 保留错误日志；
- 迁移文件带 checksum，防止手改。

### 7.2 兼容

- 新表只增不改；
- 加列用 `ALTER TABLE ... ADD COLUMN`；
- 不删除旧列；
- 数据回填用幂等脚本；
- 旧 CSV 不迁移，只索引。

### 7.3 版本记录

`app_settings` 存：

```text
db_schema_version
db_created_at
db_last_migration_at
```

---

## 8. 备份与恢复

### 8.1 自动备份

- 启动时若 schema 将变更；
- 每天首次启动（可配）；
- 手动“立即备份”；
- 保留最近 N 份（默认 14）；
- 命名 `management_YYYYMMDD_HHMMSS.db`；
- 备份后 `PRAGMA integrity_check`；
- 记录 `operation_logs`。

### 8.2 恢复

- 选择备份文件；
- 校验完整性；
- 关闭连接；
- 原库改名为 `.before_restore_时间`；
- 替换；
- 重新打开并检查 schema；
- 失败自动回退。

### 8.3 导出/交换

- 导出“管理快照” JSON/CSV（不含图片）；
- 用于多机交换/审计；
- 不把 SQLite 放 SMB 共享做并发写。

---

## 9. 数据保留与清理

| 数据 | 默认保留 | 清理方式 |
|---|---|---|
| 验收事件 | 永久 | 不自动删 |
| 扫描记录 | 1 年 | 手动/按策略 |
| 格式检查记录 | 1 年 | 手动 |
| 导入记录 | 永久（元信息） | 不自动删 |
| 操作日志 | 1 年 | 轮转 |
| 报告文件 | 1 年 | 手动 |
| 备份 | 14 份 | 自动轮转 |
| 统计缓存 | 可重建 | 自动 |

清理必须显式确认，不能由扫描/检查/归档间接触发。

---

## 10. 数据量与性能

估算：

- 10 万数据组 × 10 文件 = 100 万 `data_group_files` 行；
- 10 万验收事件 + 标签；
- 100 万格式检查 issue（按 10% 异常）；
- SQLite 可承受，但需要索引与分页查询。

策略：

- 列表查询分页；
- 统计用汇总表/视图；
- 批量插入用事务（每 1000 行提交）；
- 大报告分页/流式写；
- 避免 `SELECT *`；
- 历史 run 可按需归档到独立 DB。

---

## 11. 安全

- 不在数据库存 API Key；
- Key 使用 Windows DPAPI 加密文件（`secrets/`）；
- endpoint 脱敏（只存 host，不存 query/token）；
- 用户文本/备注只在本地库；
- 日志不写完整 CSV 行/备注全文；
- 数据库文件权限继承用户目录；
- 导出诊断包默认脱敏路径。

---

## 12. 与现有 CSV 的映射

| CSV 列 | 数据库 |
|---|---|
| 姓名 | `acceptance_events.operator` 或 `data_groups.user_name` |
| 图片ID | `data_groups.task_id` + `acceptance_events` 关联 |
| 一级/二级 | `acceptance_events.l1/l2` |
| 结果 | `acceptance_events.result` |
| 备注 | `acceptance_events.note` |
| 标签 | `acceptance_event_tags.raw_text`（按 `;` 拆分，保留原文） |
| 错误截图 | `acceptance_event_evidence.relative_path` |
| 质检时间 | `acceptance_events.event_time` |
| 路径 | `data_groups.normalized_path` / `data_group_files` |

**写入顺序**：

1. 现有 `save_to_disk()` 写 CSV（权威）；
2. 成功后调用 `record_acceptance_event()` 幂等写数据库；
3. 数据库失败不回滚 CSV，只记日志/重试队列；
4. 导入旧 CSV 时只写数据库，不改原文件。

---

## 13. 已确认与待确认

### 13.1 已确认（见 `00-decisions.md` D-005）

1. 默认数据库位置采用 `D:\VelkozDATA\data\management.db`；
2. 设置中允许改为其他本地目录；
3. 默认不提供“便携模式”（数据目录不跟随程序目录）。

### 13.2 待后续确认（不阻塞 v1.25.0）

1. 验收事件是否要保存每次修改的完整快照（增量 vs 全量）？
2. 统计缓存的刷新频率（按需/每日/每次写入）？
3. 历史 run 是否需要在 1 年后自动归档到单独 DB？
4. 是否需要数据库加密（SQLCipher）？当前默认不加密，依赖系统账户权限。
