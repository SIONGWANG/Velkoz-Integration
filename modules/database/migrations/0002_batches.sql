-- 审视之眼 Pro 1.25.1 批次确认与差异
-- 只保存索引与状态，不存图片/TXT/CSV 原文。

ALTER TABLE scan_candidates ADD COLUMN group_paths_json TEXT;
ALTER TABLE scan_candidates ADD COLUMN notes TEXT;
ALTER TABLE scan_candidates ADD COLUMN updated_at TEXT;

CREATE TABLE IF NOT EXISTS batches (
    id TEXT PRIMARY KEY,
    display_id TEXT,
    name TEXT NOT NULL,
    project_id TEXT,
    supplier_id TEXT,
    team_id TEXT,
    category_l1 TEXT,
    category_l2 TEXT,
    submit_date TEXT,
    data_root TEXT,
    batch_path TEXT,
    data_count INTEGER NOT NULL DEFAULT 0,
    format_status TEXT NOT NULL DEFAULT 'not_checked',
    translation_status TEXT NOT NULL DEFAULT 'not_started',
    acceptance_status TEXT NOT NULL DEFAULT 'not_started',
    qualified_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    remaining_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'confirmed',
    source TEXT NOT NULL DEFAULT 'scan',
    rule_set_id TEXT,
    rule_version_id TEXT,
    identity_json TEXT,
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(name, data_root)
);

CREATE TABLE IF NOT EXISTS sub_batches (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    name TEXT NOT NULL,
    data_root TEXT,
    data_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    note TEXT,
    created_at TEXT,
    FOREIGN KEY(batch_id) REFERENCES batches(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS data_groups (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    sub_batch_id TEXT,
    task_id TEXT NOT NULL,
    normalized_path TEXT NOT NULL,
    root_path TEXT,
    user_name TEXT,
    image_count INTEGER NOT NULL DEFAULT 0,
    txt_count INTEGER NOT NULL DEFAULT 0,
    file_count INTEGER NOT NULL DEFAULT 0,
    fingerprint TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    first_seen_at TEXT,
    last_seen_at TEXT,
    UNIQUE(batch_id, task_id, normalized_path),
    FOREIGN KEY(batch_id) REFERENCES batches(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS data_group_files (
    id TEXT PRIMARY KEY,
    data_group_id TEXT NOT NULL,
    role TEXT NOT NULL,
    file_name TEXT NOT NULL,
    relative_path TEXT,
    size INTEGER,
    mtime_ns INTEGER,
    ext TEXT,
    fingerprint TEXT,
    FOREIGN KEY(data_group_id) REFERENCES data_groups(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS batch_status_log (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    operator TEXT,
    reason TEXT,
    created_at TEXT,
    FOREIGN KEY(batch_id) REFERENCES batches(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_batches_status ON batches(status);
CREATE INDEX IF NOT EXISTS idx_batches_root ON batches(data_root);
CREATE INDEX IF NOT EXISTS idx_data_groups_batch ON data_groups(batch_id);
CREATE INDEX IF NOT EXISTS idx_data_group_files_group ON data_group_files(data_group_id);
CREATE INDEX IF NOT EXISTS idx_scan_candidates_status ON scan_candidates(status);
