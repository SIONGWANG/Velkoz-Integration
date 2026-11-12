-- 审视之眼 Pro 1.25.11 管理中心重构：标签、类目、目录批次、队列与编号索引
-- 全部为增量变更：不重建、不清空 1.25.10 既有表。
-- 旧表 batches/data_groups 继续供正式质检和旧页面使用；
-- 新表 catalog_* 供以批次为核心的新工作台使用。

CREATE TABLE IF NOT EXISTS catalog_teams (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    aliases TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS catalog_reviewers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    aliases TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS catalog_categories (
    id TEXT PRIMARY KEY,
    parent_id TEXT,
    level INTEGER NOT NULL,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    image_count INTEGER NOT NULL DEFAULT 0,
    rule_set_id TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_catalog_categories_unique
    ON catalog_categories(COALESCE(parent_id, ''), name);

CREATE TABLE IF NOT EXISTS catalog_batches (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    source_type TEXT NOT NULL,
    source_root_path TEXT NOT NULL,
    batch_path TEXT NOT NULL,
    relative_path TEXT,
    name TEXT NOT NULL,
    category_l1 TEXT,
    category_l2 TEXT,
    category_l3 TEXT,
    team_id TEXT,
    team_name TEXT,
    reviewer_id TEXT,
    reviewer_name TEXT,
    submit_date TEXT,
    record_dir TEXT,
    extra_levels_json TEXT,
    main_status TEXT NOT NULL DEFAULT 'new',
    format_status TEXT NOT NULL DEFAULT 'not_checked',
    translation_status TEXT NOT NULL DEFAULT 'not_translated',
    data_count INTEGER NOT NULL DEFAULT 0,
    qualified_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    remaining_count INTEGER NOT NULL DEFAULT 0,
    identity_json TEXT,
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(batch_path)
);

CREATE INDEX IF NOT EXISTS idx_catalog_batches_project
    ON catalog_batches(project_id, source_type);
CREATE INDEX IF NOT EXISTS idx_catalog_batches_team
    ON catalog_batches(team_name);
CREATE INDEX IF NOT EXISTS idx_catalog_batches_category
    ON catalog_batches(category_l1, category_l2, category_l3);
CREATE INDEX IF NOT EXISTS idx_catalog_batches_status
    ON catalog_batches(main_status);

CREATE TABLE IF NOT EXISTS catalog_groups (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    image_count INTEGER NOT NULL DEFAULT 0,
    txt_count INTEGER NOT NULL DEFAULT 0,
    file_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT,
    UNIQUE(batch_id, task_id)
);

CREATE INDEX IF NOT EXISTS idx_catalog_groups_batch
    ON catalog_groups(batch_id);

CREATE TABLE IF NOT EXISTS catalog_batch_reviewers (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    reviewer_id TEXT,
    reviewer_name TEXT,
    folder_name TEXT,
    folder_path TEXT NOT NULL,
    file_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT,
    UNIQUE(batch_id, folder_path)
);

CREATE INDEX IF NOT EXISTS idx_catalog_batch_reviewers_batch
    ON catalog_batch_reviewers(batch_id);

CREATE TABLE IF NOT EXISTS catalog_records (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    group_id TEXT,
    number TEXT NOT NULL,
    result TEXT,
    reason TEXT,
    tags TEXT,
    evidence TEXT,
    record_time TEXT,
    reviewer_name TEXT,
    source_file TEXT,
    created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_catalog_records_number
    ON catalog_records(number);
CREATE INDEX IF NOT EXISTS idx_catalog_records_batch
    ON catalog_records(batch_id);
CREATE INDEX IF NOT EXISTS idx_catalog_records_group
    ON catalog_records(group_id);

CREATE TABLE IF NOT EXISTS catalog_queue (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    added_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    message TEXT,
    UNIQUE(batch_id, action)
);

CREATE INDEX IF NOT EXISTS idx_catalog_queue_action
    ON catalog_queue(action, status);

CREATE TABLE IF NOT EXISTS catalog_status_log (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'main',
    from_status TEXT,
    to_status TEXT,
    operator TEXT,
    reason TEXT,
    created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_catalog_status_log_batch
    ON catalog_status_log(batch_id);

INSERT OR IGNORE INTO catalog_teams(id, name, aliases, enabled, created_at, updated_at)
VALUES ('team_tongzheng', '同正', '', 1, datetime('now'), datetime('now'));
INSERT OR IGNORE INTO catalog_teams(id, name, aliases, enabled, created_at, updated_at)
VALUES ('team_henan_zhuxing', '河南铸星', '', 1, datetime('now'), datetime('now'));
INSERT OR IGNORE INTO catalog_teams(id, name, aliases, enabled, created_at, updated_at)
VALUES ('team_xingxing', '醒醒文化', '', 1, datetime('now'), datetime('now'));
INSERT OR IGNORE INTO catalog_teams(id, name, aliases, enabled, created_at, updated_at)
VALUES ('team_zhumadian', '驻马店', '', 1, datetime('now'), datetime('now'));
INSERT OR IGNORE INTO catalog_teams(id, name, aliases, enabled, created_at, updated_at)
VALUES ('team_zhumadian_qianyi', '驻马店乾亿', '', 1, datetime('now'), datetime('now'));
INSERT OR IGNORE INTO catalog_teams(id, name, aliases, enabled, created_at, updated_at)
VALUES ('team_school', '学校', '', 1, datetime('now'), datetime('now'));

INSERT OR IGNORE INTO catalog_reviewers(id, name, aliases, enabled, created_at, updated_at)
VALUES ('reviewer_liusichang', '刘斯畅', '', 1, datetime('now'), datetime('now'));
INSERT OR IGNORE INTO catalog_reviewers(id, name, aliases, enabled, created_at, updated_at)
VALUES ('reviewer_wangshaoxiong', '王少雄', '', 1, datetime('now'), datetime('now'));
INSERT OR IGNORE INTO catalog_reviewers(id, name, aliases, enabled, created_at, updated_at)
VALUES ('reviewer_zhudai', '朱代', '', 1, datetime('now'), datetime('now'));

INSERT OR IGNORE INTO catalog_categories(id, parent_id, level, name, enabled, sort_order, image_count, rule_set_id, created_at, updated_at)
VALUES ('cat_product_bg', '', 1, '产品背景替换', 1, 10, 0, '', datetime('now'), datetime('now'));
INSERT OR IGNORE INTO catalog_categories(id, parent_id, level, name, enabled, sort_order, image_count, rule_set_id, created_at, updated_at)
VALUES ('cat_person_scene', '', 1, '人物景', 1, 20, 0, '', datetime('now'), datetime('now'));
INSERT OR IGNORE INTO catalog_categories(id, parent_id, level, name, enabled, sort_order, image_count, rule_set_id, created_at, updated_at)
VALUES ('cat_school', '', 1, '学校', 1, 30, 0, '', datetime('now'), datetime('now'));
INSERT OR IGNORE INTO catalog_categories(id, parent_id, level, name, enabled, sort_order, image_count, rule_set_id, created_at, updated_at)
VALUES ('cat_virtual_wear', '', 1, '虚拟穿衣', 1, 40, 0, '', datetime('now'), datetime('now'));
INSERT OR IGNORE INTO catalog_categories(id, parent_id, level, name, enabled, sort_order, image_count, rule_set_id, created_at, updated_at)
VALUES ('cat_vw_l1', 'cat_virtual_wear', 2, '类目一_1人物+1服饰', 1, 10, 0, '', datetime('now'), datetime('now'));
INSERT OR IGNORE INTO catalog_categories(id, parent_id, level, name, enabled, sort_order, image_count, rule_set_id, created_at, updated_at)
VALUES ('cat_vw_l2', 'cat_virtual_wear', 2, '类目二_1人物+2服饰', 1, 20, 0, '', datetime('now'), datetime('now'));
