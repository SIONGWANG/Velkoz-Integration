-- 审视之眼 Pro 1.25.11 批次翻译对接 8765
-- 记录每次翻译运行与逐编号结果；源文件写入由 8765 工具完成，本平台只做备份、调用与审计。

CREATE TABLE IF NOT EXISTS catalog_translation_runs (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    endpoint TEXT,
    model TEXT,
    total INTEGER NOT NULL DEFAULT 0,
    translated INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'not_translated',
    message TEXT,
    backup_dir TEXT,
    operator TEXT,
    started_at TEXT,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_catalog_translation_runs_batch
    ON catalog_translation_runs(batch_id, finished_at);

CREATE TABLE IF NOT EXISTS catalog_translation_items (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    number TEXT NOT NULL,
    action TEXT,
    status TEXT,
    message TEXT,
    en_path TEXT,
    created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_catalog_translation_items_run
    ON catalog_translation_items(run_id);
CREATE INDEX IF NOT EXISTS idx_catalog_translation_items_number
    ON catalog_translation_items(batch_id, number);
