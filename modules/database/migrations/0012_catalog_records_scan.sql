-- 审视之眼 Pro 1.25.11 验收记录扫描
-- 增量字段：是否合格、源行号、去重指纹。

ALTER TABLE catalog_records ADD COLUMN qualified INTEGER NOT NULL DEFAULT 0;
ALTER TABLE catalog_records ADD COLUMN source_row INTEGER;
ALTER TABLE catalog_records ADD COLUMN fingerprint TEXT;

CREATE INDEX IF NOT EXISTS idx_catalog_records_qualified
    ON catalog_records(batch_id, qualified);
CREATE INDEX IF NOT EXISTS idx_catalog_records_reviewer
    ON catalog_records(reviewer_name);
