-- 审视之眼 Pro 1.25.11 团队合并：驻马店乾亿 = 驻马店
-- 保留“驻马店乾亿”为显示名，“驻马店”作为别名。

INSERT OR IGNORE INTO catalog_teams(id, name, aliases, enabled, created_at, updated_at)
VALUES ('team_zhumadian_qianyi', '驻马店乾亿', '驻马店', 1, datetime('now'), datetime('now'));

UPDATE catalog_batches
SET team_id = 'team_zhumadian_qianyi', team_name = '驻马店乾亿'
WHERE team_id = 'team_zhumadian' OR team_name IN ('驻马店', '驻马店乾亿');

UPDATE catalog_teams
SET aliases = '驻马店', updated_at = datetime('now')
WHERE id = 'team_zhumadian_qianyi';

DELETE FROM catalog_teams WHERE id = 'team_zhumadian';
