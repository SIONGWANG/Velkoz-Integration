-- 审视之眼 Pro 1.25.11 目录索引：标记已消失/被忽略的批次
-- 增量变更，不重建既有表。

ALTER TABLE catalog_batches ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;
ALTER TABLE catalog_groups ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;

CREATE INDEX IF NOT EXISTS idx_catalog_batches_active
    ON catalog_batches(is_active);
CREATE INDEX IF NOT EXISTS idx_catalog_groups_active
    ON catalog_groups(is_active);
