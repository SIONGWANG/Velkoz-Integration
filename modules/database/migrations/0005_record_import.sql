-- 审视之眼 Pro 1.25.4 其他验收员记录导入
-- 只保存导入索引与记录元信息，不保存图片/TXT/CSV 原文。

CREATE TABLE IF NOT EXISTS record_imports (
    id TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    source_type TEXT NOT NULL,
    file_size INTEGER NOT NULL DEFAULT 0,
    file_mtime_ns INTEGER NOT NULL DEFAULT 0,
    file_hash TEXT,
    rows_total INTEGER NOT NULL DEFAULT 0,
    rows_new INTEGER NOT NULL DEFAULT 0,
    rows_skipped INTEGER NOT NULL DEFAULT 0,
    rows_error INTEGER NOT NULL DEFAULT 0,
    operator_detected TEXT,
    batch_id TEXT,
    status TEXT NOT NULL DEFAULT 'failed',
    imported_at TEXT,
    imported_by TEXT,
    note TEXT,
    created_at TEXT,
    FOREIGN KEY(batch_id) REFERENCES batches(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS imported_records (
    id TEXT PRIMARY KEY,
    import_id TEXT NOT NULL,
    data_group_id TEXT,
    task_id TEXT,
    operator TEXT,
    result TEXT,
    l1 TEXT,
    l2 TEXT,
    note TEXT,
    tags_raw TEXT,
    evidence_raw TEXT,
    record_time TEXT,
    source_row INTEGER,
    source_row_fingerprint TEXT NOT NULL,
    accepted_event_id TEXT,
    link_status TEXT,
    created_at TEXT,
    UNIQUE(import_id, source_row_fingerprint),
    FOREIGN KEY(import_id) REFERENCES record_imports(id) ON DELETE CASCADE,
    FOREIGN KEY(data_group_id) REFERENCES data_groups(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_record_imports_time ON record_imports(imported_at);
CREATE INDEX IF NOT EXISTS idx_record_imports_status ON record_imports(status);
CREATE INDEX IF NOT EXISTS idx_record_imports_batch ON record_imports(batch_id);
CREATE INDEX IF NOT EXISTS idx_import_records_import ON imported_records(import_id);
CREATE INDEX IF NOT EXISTS idx_import_records_task ON imported_records(task_id);
CREATE INDEX IF NOT EXISTS idx_import_records_group ON imported_records(data_group_id);
CREATE INDEX IF NOT EXISTS idx_import_records_fp ON imported_records(source_row_fingerprint);
