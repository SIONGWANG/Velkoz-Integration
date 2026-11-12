-- 审视之眼 Pro 1.25.11 批次格式检查
-- 检查结果独立于旧版 format_check_* 表，避免影响正式质检统计。

CREATE TABLE IF NOT EXISTS catalog_format_runs (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    rule_source TEXT,
    expected_images INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    passed INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    duplicates INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'completed',
    report_path TEXT,
    operator TEXT,
    started_at TEXT,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_catalog_format_runs_batch
    ON catalog_format_runs(batch_id, finished_at);

CREATE TABLE IF NOT EXISTS catalog_format_results (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    number TEXT NOT NULL,
    group_path TEXT,
    expected_images INTEGER NOT NULL DEFAULT 0,
    actual_images INTEGER NOT NULL DEFAULT 0,
    txt_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    reason TEXT,
    created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_catalog_format_results_run
    ON catalog_format_results(run_id);
CREATE INDEX IF NOT EXISTS idx_catalog_format_results_number
    ON catalog_format_results(batch_id, number);

CREATE TABLE IF NOT EXISTS catalog_duplicate_hits (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    number TEXT NOT NULL,
    other_batch_id TEXT,
    other_batch_name TEXT,
    other_batch_path TEXT,
    other_reviewer TEXT,
    other_source_type TEXT,
    created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_catalog_duplicate_hits_run
    ON catalog_duplicate_hits(run_id);
