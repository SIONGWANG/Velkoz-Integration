-- 审视之眼 Pro 1.25.0 初始 schema
-- 只存索引、状态和操作日志，不存图片/TXT/CSV 原文。

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS scan_rule_sets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'global',
    scope_id TEXT,
    active_version_id TEXT,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS scan_rule_versions (
    id TEXT PRIMARY KEY,
    rule_set_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    config_json TEXT NOT NULL,
    note TEXT,
    created_at TEXT,
    created_by TEXT,
    UNIQUE(rule_set_id, version),
    FOREIGN KEY(rule_set_id) REFERENCES scan_rule_sets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS scan_runs (
    id TEXT PRIMARY KEY,
    root_path TEXT NOT NULL,
    rule_set_id TEXT,
    rule_version_id TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    total_dirs INTEGER NOT NULL DEFAULT 0,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    group_count INTEGER NOT NULL DEFAULT 0,
    issue_count INTEGER NOT NULL DEFAULT 0,
    operator TEXT,
    summary_json TEXT
);

CREATE TABLE IF NOT EXISTS scan_candidates (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    batch_path TEXT NOT NULL,
    batch_name TEXT,
    identity_json TEXT,
    status TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    reason_json TEXT,
    group_count INTEGER NOT NULL DEFAULT 0,
    file_count INTEGER NOT NULL DEFAULT 0,
    confirmed_batch_id TEXT,
    confirmed_by TEXT,
    confirmed_at TEXT,
    FOREIGN KEY(run_id) REFERENCES scan_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS scan_run_items (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    path TEXT NOT NULL,
    item_type TEXT NOT NULL,
    task_id TEXT,
    reason_code TEXT,
    detail_json TEXT,
    FOREIGN KEY(run_id) REFERENCES scan_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS operation_logs (
    id TEXT PRIMARY KEY,
    operator TEXT,
    module TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ok',
    target_path TEXT,
    source_path TEXT,
    detail_json TEXT,
    error_code TEXT,
    error_message TEXT,
    duration_ms INTEGER,
    run_id TEXT,
    batch_id TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scan_candidates_run ON scan_candidates(run_id);
CREATE INDEX IF NOT EXISTS idx_scan_run_items_run ON scan_run_items(run_id);
CREATE INDEX IF NOT EXISTS idx_scan_runs_started ON scan_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_operation_logs_created ON operation_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_operation_logs_module ON operation_logs(module);
