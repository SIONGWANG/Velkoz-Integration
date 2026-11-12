"""管理中心统计（v1.25.11 P6）：只读 catalog_* 表。

口径：
- 批次数：catalog_batches 去重计数；
- 数据量：catalog_groups 编号文件夹数；
- 合格/不合格：catalog_groups.status = qualified / rejected；
- 未覆盖：pending 或空状态。
"""
from __future__ import annotations

from typing import Any

from modules.database.connection import Database

LEVEL_FIELDS = {1: "category_l1", 2: "category_l2", 3: "category_l3"}
LEVEL_LABELS = {1: "大类", 2: "小类", 3: "三级类"}


def _where(project_id: str = "", source_type: str = "") -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if project_id:
        clauses.append("b.project_id = ?")
        params.append(project_id)
    if source_type:
        clauses.append("b.source_type = ?")
        params.append(source_type)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def overview(db: Database, project_id: str = "", source_type: str = "") -> dict[str, Any]:
    where, params = _where(project_id, source_type)
    conn = db.connect()
    batches = conn.execute(
        f"SELECT COUNT(*) AS c, COALESCE(SUM(b.data_count), 0) AS data_count FROM catalog_batches b{where}",
        params,
    ).fetchone()
    row = conn.execute(
        f"""
        SELECT
            COUNT(g.id) AS data,
            SUM(CASE WHEN g.status = 'qualified' THEN 1 ELSE 0 END) AS qualified,
            SUM(CASE WHEN g.status = 'rejected' THEN 1 ELSE 0 END) AS rejected,
            SUM(CASE WHEN g.status IS NULL OR g.status IN ('', 'pending') THEN 1 ELSE 0 END) AS pending
        FROM catalog_batches b
        LEFT JOIN catalog_groups g ON g.batch_id = b.id
        {where}
        """,
        params,
    ).fetchone()
    completed = conn.execute(
        f"SELECT COUNT(*) AS c FROM catalog_batches b{where}{' AND' if where else ' WHERE'} b.main_status = 'completed'",
        params,
    ).fetchone()["c"]
    format_counts = {
        str(item["format_status"] or "not_checked"): int(item["c"])
        for item in conn.execute(
            f"SELECT b.format_status, COUNT(*) AS c FROM catalog_batches b{where} GROUP BY b.format_status",
            params,
        )
    }
    translation_counts = {
        str(item["translation_status"] or "not_translated"): int(item["c"])
        for item in conn.execute(
            f"SELECT b.translation_status, COUNT(*) AS c FROM catalog_batches b{where} GROUP BY b.translation_status",
            params,
        )
    }
    data = int(row["data"] or 0)
    qualified = int(row["qualified"] or 0)
    return {
        "batches": int(batches["c"] or 0),
        "data": data,
        "qualified": qualified,
        "rejected": int(row["rejected"] or 0),
        "pending": int(row["pending"] or 0),
        "completed_batches": int(completed or 0),
        "qualified_rate": round(qualified / data * 100, 2) if data else 0.0,
        "format_counts": format_counts,
        "translation_counts": translation_counts,
    }


def by_level(db: Database, level: int, project_id: str = "", source_type: str = "") -> list[dict[str, Any]]:
    field = LEVEL_FIELDS.get(int(level), "category_l1")
    where, params = _where(project_id, source_type)
    rows = db.connect().execute(
        f"""
        SELECT
            COALESCE(NULLIF(b.{field}, ''), '未分类') AS name,
            COUNT(DISTINCT b.id) AS batches,
            COUNT(g.id) AS data,
            SUM(CASE WHEN g.status = 'qualified' THEN 1 ELSE 0 END) AS qualified,
            SUM(CASE WHEN g.status = 'rejected' THEN 1 ELSE 0 END) AS rejected,
            SUM(CASE WHEN g.status IS NULL OR g.status IN ('', 'pending') THEN 1 ELSE 0 END) AS pending
        FROM catalog_batches b
        LEFT JOIN catalog_groups g ON g.batch_id = b.id
        {where}
        GROUP BY COALESCE(NULLIF(b.{field}, ''), '未分类')
        ORDER BY data DESC, name
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def by_team(db: Database, project_id: str = "", source_type: str = "") -> list[dict[str, Any]]:
    where, params = _where(project_id, source_type)
    rows = db.connect().execute(
        f"""
        SELECT
            COALESCE(NULLIF(b.team_name, ''), '未识别团队') AS name,
            COUNT(DISTINCT b.id) AS batches,
            COUNT(g.id) AS data,
            SUM(CASE WHEN g.status = 'qualified' THEN 1 ELSE 0 END) AS qualified,
            SUM(CASE WHEN g.status = 'rejected' THEN 1 ELSE 0 END) AS rejected,
            SUM(CASE WHEN g.status IS NULL OR g.status IN ('', 'pending') THEN 1 ELSE 0 END) AS pending
        FROM catalog_batches b
        LEFT JOIN catalog_groups g ON g.batch_id = b.id
        {where}
        GROUP BY COALESCE(NULLIF(b.team_name, ''), '未识别团队')
        ORDER BY data DESC, name
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def by_date(db: Database, project_id: str = "", source_type: str = "") -> list[dict[str, Any]]:
    where, params = _where(project_id, source_type)
    rows = db.connect().execute(
        f"""
        SELECT
            COALESCE(NULLIF(b.submit_date, ''), '日期未知') AS name,
            COUNT(*) AS batches,
            COALESCE(SUM(b.data_count), 0) AS data
        FROM catalog_batches b
        {where}
        GROUP BY COALESCE(NULLIF(b.submit_date, ''), '日期未知')
        ORDER BY name DESC
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]
