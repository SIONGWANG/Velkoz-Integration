-- 审视之眼 Pro 1.25.7 统计与报表
-- stats_daily 是可按源表重建的缓存，不保存图片/TXT/CSV 原文。

CREATE TABLE IF NOT EXISTS stats_daily (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    batch_id TEXT,
    operator TEXT,
    new_batches INTEGER NOT NULL DEFAULT 0,
    new_groups INTEGER NOT NULL DEFAULT 0,
    format_checked INTEGER NOT NULL DEFAULT 0,
    format_failed INTEGER NOT NULL DEFAULT 0,
    format_warnings INTEGER NOT NULL DEFAULT 0,
    translated INTEGER NOT NULL DEFAULT 0,
    translation_failed INTEGER NOT NULL DEFAULT 0,
    accepted INTEGER NOT NULL DEFAULT 0,
    qualified INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    pending INTEGER NOT NULL DEFAULT 0,
    built_at TEXT,
    source TEXT NOT NULL DEFAULT 'live'
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_stats_daily_scope
    ON stats_daily(date, COALESCE(batch_id, ''), COALESCE(operator, ''));
CREATE INDEX IF NOT EXISTS idx_stats_daily_date ON stats_daily(date);
