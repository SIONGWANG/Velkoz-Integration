-- 审视之眼 Pro 1.25.3 翻译与预审
-- 只保存任务索引、状态、用量与建议，不保存 API Key。

CREATE TABLE IF NOT EXISTS translation_jobs (
    id TEXT PRIMARY KEY,
    batch_id TEXT,
    provider TEXT NOT NULL,
    model TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    prompt_version TEXT,
    started_at TEXT,
    finished_at TEXT,
    operator TEXT,
    source_report TEXT,
    note TEXT,
    root_path TEXT,
    profile_id TEXT,
    summary_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS translation_items (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    data_group_id TEXT,
    task_id TEXT NOT NULL,
    folder_path TEXT NOT NULL,
    zh_txt_path TEXT,
    en_txt_path TEXT,
    status TEXT NOT NULL,
    zh_length INTEGER NOT NULL DEFAULT 0,
    en_length INTEGER NOT NULL DEFAULT 0,
    model TEXT,
    translated_at TEXT,
    grammar_checked INTEGER NOT NULL DEFAULT 0,
    grammar_passed INTEGER NOT NULL DEFAULT 0,
    consistency_checked INTEGER NOT NULL DEFAULT 0,
    consistency_passed INTEGER NOT NULL DEFAULT 0,
    suggestion TEXT,
    manual_fixed INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT,
    token_input INTEGER NOT NULL DEFAULT 0,
    token_output INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    report_row_hash TEXT,
    written_at TEXT,
    backup_path TEXT,
    updated_at TEXT,
    FOREIGN KEY(job_id) REFERENCES translation_jobs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS translation_usage (
    id TEXT PRIMARY KEY,
    job_id TEXT,
    usage_date TEXT,
    usage_time TEXT,
    api_type TEXT,
    endpoint TEXT,
    total INTEGER NOT NULL DEFAULT 0,
    success INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    token_in INTEGER NOT NULL DEFAULT 0,
    token_out INTEGER NOT NULL DEFAULT 0,
    token_total INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'CNY',
    note TEXT,
    source_report TEXT,
    row_hash TEXT,
    created_at TEXT,
    UNIQUE(row_hash)
);

CREATE INDEX IF NOT EXISTS idx_translation_jobs_started ON translation_jobs(started_at);
CREATE INDEX IF NOT EXISTS idx_translation_jobs_status ON translation_jobs(status);
CREATE INDEX IF NOT EXISTS idx_translation_items_job ON translation_items(job_id);
CREATE INDEX IF NOT EXISTS idx_translation_items_task ON translation_items(task_id);
CREATE INDEX IF NOT EXISTS idx_translation_items_status ON translation_items(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_translation_items_hash ON translation_items(report_row_hash);
CREATE INDEX IF NOT EXISTS idx_translation_usage_job ON translation_usage(job_id);
CREATE INDEX IF NOT EXISTS idx_translation_usage_date ON translation_usage(usage_date);
