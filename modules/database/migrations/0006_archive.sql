-- 审视之眼 Pro 1.25.6 合格数据归档
-- 只记录归档任务、数据组与文件校验结果；源图片/TXT/CSV 始终只读。
-- 默认只复制，不移动、不删除、不覆盖目标。

CREATE TABLE IF NOT EXISTS archive_jobs (
    id TEXT PRIMARY KEY,
    batch_id TEXT,
    source_root TEXT NOT NULL DEFAULT '',
    output_root TEXT NOT NULL,
    start_layer TEXT NOT NULL DEFAULT 'batch',
    name_template TEXT NOT NULL DEFAULT '',
    custom_prefix TEXT,
    filter_mode TEXT NOT NULL DEFAULT 'all',
    filter_value TEXT,
    status TEXT NOT NULL DEFAULT 'preview',
    total_items INTEGER NOT NULL DEFAULT 0,
    copied_items INTEGER NOT NULL DEFAULT 0,
    skipped_items INTEGER NOT NULL DEFAULT 0,
    conflict_items INTEGER NOT NULL DEFAULT 0,
    failed_items INTEGER NOT NULL DEFAULT 0,
    verified_items INTEGER NOT NULL DEFAULT 0,
    total_files INTEGER NOT NULL DEFAULT 0,
    bytes_total INTEGER NOT NULL DEFAULT 0,
    bytes_copied INTEGER NOT NULL DEFAULT 0,
    verify_hash INTEGER NOT NULL DEFAULT 1,
    report_path TEXT,
    operator TEXT,
    note TEXT,
    created_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT,
    FOREIGN KEY(batch_id) REFERENCES batches(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS archive_items (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    data_group_id TEXT,
    task_id TEXT,
    source_path TEXT NOT NULL,
    dest_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    file_count INTEGER NOT NULL DEFAULT 0,
    bytes INTEGER NOT NULL DEFAULT 0,
    copied_bytes INTEGER NOT NULL DEFAULT 0,
    source_fingerprint TEXT,
    dest_fingerprint TEXT,
    error_message TEXT,
    created_at TEXT,
    updated_at TEXT,
    FOREIGN KEY(job_id) REFERENCES archive_jobs(id) ON DELETE CASCADE,
    FOREIGN KEY(data_group_id) REFERENCES data_groups(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_archive_jobs_batch ON archive_jobs(batch_id);
CREATE INDEX IF NOT EXISTS idx_archive_jobs_status ON archive_jobs(status);
CREATE INDEX IF NOT EXISTS idx_archive_items_job ON archive_items(job_id);
CREATE INDEX IF NOT EXISTS idx_archive_items_group ON archive_items(data_group_id);
CREATE INDEX IF NOT EXISTS idx_archive_items_status ON archive_items(job_id, status);
