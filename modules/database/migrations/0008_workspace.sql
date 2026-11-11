-- 审视之眼 Pro 1.25.8 业务目录接入与批次工作台
-- 全部为增量变更：不重建、不清空既有表。

ALTER TABLE batches ADD COLUMN source_type TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE batches ADD COLUMN source_root_id TEXT;
ALTER TABLE batches ADD COLUMN source_root_path TEXT;
ALTER TABLE batches ADD COLUMN relative_path TEXT;
ALTER TABLE batches ADD COLUMN parent_batch_id TEXT;
ALTER TABLE batches ADD COLUMN project_name TEXT;

ALTER TABLE scan_runs ADD COLUMN source_type TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE scan_runs ADD COLUMN project_root_id TEXT;
ALTER TABLE scan_runs ADD COLUMN project_name TEXT;

CREATE TABLE IF NOT EXISTS project_roots (
    id TEXT PRIMARY KEY,
    project_name TEXT NOT NULL,
    parent_path TEXT,
    pending_path TEXT NOT NULL DEFAULT '',
    final_path TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    note TEXT,
    last_scan_at TEXT,
    pending_scan_at TEXT,
    final_scan_at TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_project_roots_enabled ON project_roots(enabled);
CREATE INDEX IF NOT EXISTS idx_batches_source_type ON batches(source_type);
CREATE INDEX IF NOT EXISTS idx_batches_source_path ON batches(source_root_path);
CREATE INDEX IF NOT EXISTS idx_batches_parent ON batches(parent_batch_id);
CREATE INDEX IF NOT EXISTS idx_scan_runs_source_type ON scan_runs(source_type);
CREATE INDEX IF NOT EXISTS idx_scan_runs_project ON scan_runs(project_root_id);
