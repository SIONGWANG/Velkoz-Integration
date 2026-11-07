"""管理数据库仓储：规则方案与扫描记录。"""
from __future__ import annotations

import json
import uuid
from typing import Any

from modules.common.timeutil import now_iso
from modules.database.connection import Database
from modules.scanning.rules import RuleSet
from modules.scanning.scanner import ScanResult


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4()}"


def list_rule_sets(db: Database) -> list[dict[str, Any]]:
    conn = db.connect()
    rows = conn.execute(
        """
        SELECT s.*, v.version AS active_version, v.config_json AS active_config,
               v.id AS active_version_id_resolved
        FROM scan_rule_sets s
        LEFT JOIN scan_rule_versions v ON v.id = s.active_version_id
        ORDER BY s.is_default DESC, s.name COLLATE NOCASE
        """
    ).fetchall()
    return [dict(row) for row in rows]


def get_rule_set(db: Database, rule_set_id: str) -> RuleSet | None:
    conn = db.connect()
    row = conn.execute(
        """
        SELECT v.config_json
        FROM scan_rule_sets s
        JOIN scan_rule_versions v ON v.id = s.active_version_id
        WHERE s.id = ?
        """,
        (rule_set_id,),
    ).fetchone()
    if not row:
        return None
    data = json.loads(row["config_json"])
    return RuleSet.from_dict(data)


def seed_builtin_ruleset(db: Database, ruleset: RuleSet, created_by: str = "system") -> bool:
    """首次启动写入内置方案；已存在则不覆盖用户版本。"""
    conn = db.connect()
    existing = conn.execute("SELECT 1 FROM scan_rule_sets WHERE id = ?", (ruleset.id,)).fetchone()
    if existing:
        return False
    now = now_iso()
    version_id = _new_id("rv")
    conn.execute(
        "INSERT INTO scan_rule_sets(id, name, scope, scope_id, active_version_id, is_default, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
        (ruleset.id, ruleset.name, ruleset.scope, ruleset.scope_id, version_id, now, now),
    )
    conn.execute(
        "INSERT INTO scan_rule_versions(id, rule_set_id, version, config_json, note, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (version_id, ruleset.id, ruleset.version, ruleset.to_json(), "内置默认方案", now, created_by),
    )
    conn.commit()
    return True


def save_rule_set(
    db: Database,
    ruleset: RuleSet,
    *,
    created_by: str = "",
    note: str = "",
    make_default: bool = False,
) -> str:
    """保存新版本；同一 id 自动递增版本号。"""
    conn = db.connect()
    now = now_iso()
    row = conn.execute(
        "SELECT s.id, v.version AS active_version FROM scan_rule_sets s LEFT JOIN scan_rule_versions v ON v.id = s.active_version_id WHERE s.id = ?",
        (ruleset.id,),
    ).fetchone()
    if row is None:
        version = ruleset.version or 1
        version_id = _new_id("rv")
        conn.execute(
            "INSERT INTO scan_rule_sets(id, name, scope, scope_id, active_version_id, is_default, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ruleset.id, ruleset.name, ruleset.scope, ruleset.scope_id, version_id, 1 if make_default else 0, now, now),
        )
    else:
        version = int(row["active_version"] or 0) + 1
        version_id = _new_id("rv")
        conn.execute(
            "UPDATE scan_rule_sets SET name = ?, scope = ?, scope_id = ?, active_version_id = ?, updated_at = ? WHERE id = ?",
            (ruleset.name, ruleset.scope, ruleset.scope_id, version_id, now, ruleset.id),
        )
    if make_default:
        conn.execute("UPDATE scan_rule_sets SET is_default = 0")
        conn.execute("UPDATE scan_rule_sets SET is_default = 1 WHERE id = ?", (ruleset.id,))
    ruleset.version = version
    conn.execute(
        "INSERT INTO scan_rule_versions(id, rule_set_id, version, config_json, note, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (version_id, ruleset.id, version, ruleset.to_json(), note, now, created_by),
    )
    conn.commit()
    return version_id


def save_scan_run(
    db: Database,
    result: ScanResult,
    *,
    operator: str = "",
    rule_set_id: str | None = None,
    rule_version_id: str | None = None,
) -> str:
    """保存扫描预览记录，不确认批次、不写 data_groups。"""
    conn = db.connect()
    run_id = _new_id("run")
    summary = {
        "stats": result.stats,
        "ruleset_id": result.ruleset_id,
        "ruleset_name": result.ruleset_name,
    }
    conn.execute(
        """
        INSERT INTO scan_runs(
            id, root_path, rule_set_id, rule_version_id, started_at, finished_at, status,
            total_dirs, candidate_count, group_count, issue_count, operator, summary_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            result.root,
            rule_set_id or result.ruleset_id,
            rule_version_id,
            result.started_at,
            result.finished_at,
            result.status,
            result.total_dirs,
            len(result.candidates),
            len(result.groups),
            len(result.issues),
            operator,
            json.dumps(summary, ensure_ascii=False),
        ),
    )

    for candidate in result.candidates:
        conn.execute(
            """
            INSERT INTO scan_candidates(
                id, run_id, batch_path, batch_name, identity_json, status, confidence,
                reason_json, group_count, file_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _new_id("cand"),
                run_id,
                candidate.batch_path,
                candidate.batch_name,
                json.dumps(candidate.identity, ensure_ascii=False),
                candidate.status,
                candidate.confidence,
                json.dumps(
                    {"reasons": candidate.reasons, "explain": candidate.explain},
                    ensure_ascii=False,
                ),
                candidate.group_count,
                candidate.file_count,
            ),
        )

    for group in result.groups:
        conn.execute(
            """
            INSERT INTO scan_run_items(id, run_id, path, item_type, task_id, reason_code, detail_json)
            VALUES (?, ?, ?, 'group', ?, NULL, ?)
            """,
            (
                _new_id("item"),
                run_id,
                group.path,
                group.task_id,
                json.dumps(
                    {
                        "images": group.images,
                        "txt_zh": group.txt_zh,
                        "txt_en": group.txt_en,
                        "file_count": group.file_count,
                        "fingerprint": group.fingerprint,
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    for issue in result.issues[:2000]:
        conn.execute(
            """
            INSERT INTO scan_run_items(id, run_id, path, item_type, task_id, reason_code, detail_json)
            VALUES (?, ?, ?, 'issue', NULL, ?, ?)
            """,
            (
                _new_id("item"),
                run_id,
                issue.path,
                issue.code,
                json.dumps(issue.detail, ensure_ascii=False),
            ),
        )

    conn.commit()
    return run_id


def recent_scan_runs(db: Database, limit: int = 20) -> list[dict[str, Any]]:
    conn = db.connect()
    rows = conn.execute(
        """
        SELECT id, root_path, status, started_at, finished_at, total_dirs,
               candidate_count, group_count, issue_count, operator, rule_set_id
        FROM scan_runs ORDER BY started_at DESC LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    return [dict(row) for row in rows]


def recent_operation_logs(db: Database, limit: int = 50) -> list[dict[str, Any]]:
    conn = db.connect()
    rows = conn.execute(
        """
        SELECT id, operator, module, action, status, target_path, error_code,
               error_message, duration_ms, created_at
        FROM operation_logs ORDER BY created_at DESC LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    return [dict(row) for row in rows]
