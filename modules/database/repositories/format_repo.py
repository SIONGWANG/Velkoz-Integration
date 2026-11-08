"""格式检查持久化：模板版本、检查记录、任务结果与问题。"""
from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from modules.common.timeutil import now_iso
from modules.database.connection import Database

if TYPE_CHECKING:
    from modules.format_checker.models import FormatCheckResult, Template


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4()}"


def _loads(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


# ── 模板 ─────────────────────────────────────────────────


def seed_builtin_template(db: Database, template: Template, created_by: str = "system") -> bool:
    """首次启动写入内置模板；已存在则不覆盖用户版本。"""
    conn = db.connect()
    existing = conn.execute(
        "SELECT 1 FROM format_templates WHERE id = ?", (template.id,)
    ).fetchone()
    if existing:
        return False
    now = now_iso()
    version_id = _new_id("fmtv")
    conn.execute(
        """
        INSERT INTO format_templates(id, name, scope, scope_id, active_version_id, builtin, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            template.id,
            template.name,
            template.scope,
            template.scope_id,
            version_id,
            now,
            now,
        ),
    )
    conn.execute(
        """
        INSERT INTO format_template_versions(id, template_id, version, config_json, note, created_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            version_id,
            template.id,
            template.version,
            template.to_json(),
            "内置默认模板",
            now,
            created_by,
        ),
    )
    conn.commit()
    return True


def list_templates(db: Database) -> list[dict[str, Any]]:
    conn = db.connect()
    rows = conn.execute(
        """
        SELECT t.*, v.version AS active_version
        FROM format_templates t
        LEFT JOIN format_template_versions v ON v.id = t.active_version_id
        ORDER BY t.builtin DESC, t.name COLLATE NOCASE
        """
    ).fetchall()
    return [dict(row) for row in rows]


def load_active_template(
    db: Database, template_id: str
) -> tuple[Template, str, int] | None:
    conn = db.connect()
    row = conn.execute(
        """
        SELECT v.id AS version_id, v.version, v.config_json
        FROM format_templates t
        JOIN format_template_versions v ON v.id = t.active_version_id
        WHERE t.id = ?
        """,
        (template_id,),
    ).fetchone()
    if not row:
        return None
    from modules.format_checker.models import Template

    data = _loads(row["config_json"], {})
    template = Template.from_dict(data)
    template.version = int(row["version"] or template.version)
    return template, row["version_id"], int(row["version"] or 1)


def load_template_version(
    db: Database, version_id: str
) -> tuple[Template, str, int] | None:
    conn = db.connect()
    row = conn.execute(
        "SELECT id, version, config_json FROM format_template_versions WHERE id = ?",
        (version_id,),
    ).fetchone()
    if not row:
        return None
    from modules.format_checker.models import Template

    template = Template.from_dict(_loads(row["config_json"], {}))
    template.version = int(row["version"] or template.version)
    return template, row["id"], int(row["version"] or 1)


def save_template(
    db: Database,
    template: Template,
    *,
    created_by: str = "",
    note: str = "",
) -> str:
    """保存为新版本并设为当前版本；内置模板不可覆盖。"""
    conn = db.connect()
    now = now_iso()
    row = conn.execute(
        "SELECT id, builtin FROM format_templates WHERE id = ?", (template.id,)
    ).fetchone()
    if row is not None and int(row["builtin"] or 0) == 1:
        raise ValueError("内置模板只读；请复制为新模板后再修改")
    if row is None:
        conn.execute(
            """
            INSERT INTO format_templates(id, name, scope, scope_id, active_version_id, builtin, created_at, updated_at)
            VALUES (?, ?, ?, ?, NULL, 0, ?, ?)
            """,
            (
                template.id,
                template.name,
                template.scope,
                template.scope_id,
                now,
                now,
            ),
        )
    max_row = conn.execute(
        "SELECT MAX(version) AS v FROM format_template_versions WHERE template_id = ?",
        (template.id,),
    ).fetchone()
    version = int(max_row["v"] or 0) + 1
    version_id = _new_id("fmtv")
    template.version = version
    conn.execute(
        """
        INSERT INTO format_template_versions(id, template_id, version, config_json, note, created_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            version_id,
            template.id,
            version,
            template.to_json(),
            note,
            now,
            created_by,
        ),
    )
    conn.execute(
        "UPDATE format_templates SET name = ?, scope = ?, scope_id = ?, active_version_id = ?, updated_at = ? WHERE id = ?",
        (
            template.name,
            template.scope,
            template.scope_id,
            version_id,
            now,
            template.id,
        ),
    )
    conn.commit()
    return version_id


def delete_template(db: Database, template_id: str) -> None:
    conn = db.connect()
    row = conn.execute(
        "SELECT builtin FROM format_templates WHERE id = ?", (template_id,)
    ).fetchone()
    if not row:
        raise ValueError("模板不存在")
    if int(row["builtin"] or 0) == 1:
        raise ValueError("内置模板不能删除")
    conn.execute("DELETE FROM format_templates WHERE id = ?", (template_id,))
    conn.execute(
        "DELETE FROM format_template_versions WHERE template_id = ?", (template_id,)
    )
    conn.commit()


def template_versions(db: Database, template_id: str) -> list[dict[str, Any]]:
    conn = db.connect()
    rows = conn.execute(
        """
        SELECT id, template_id, version, note, created_at, created_by, config_json
        FROM format_template_versions
        WHERE template_id = ?
        ORDER BY version DESC
        """,
        (template_id,),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["config"] = _loads(item.pop("config_json", None), {})
        items.append(item)
    return items


# ── 检查记录 ─────────────────────────────────────────────


def save_check_result(
    db: Database,
    result: FormatCheckResult,
    *,
    template: Template,
    template_version_id: str | None,
    batch_id: str | None = None,
    operator: str = "",
    previous_run_id: str | None = None,
    report_path: str = "",
) -> str:
    """保存一次检查（run + tasks + issues），只写管理数据库。"""
    summary = result.summary
    run_id = summary.run_id or _new_id("fmt")
    summary.run_id = run_id
    summary.batch_id = batch_id or summary.batch_id or ""
    summary.report_path = report_path or summary.report_path
    conn = db.connect()
    summary_json = {
        "issues_by_code": summary.issues_by_code,
        "integrity_changes": result.integrity_changes,
        "duration_ms": summary.duration_ms,
        "truncated": summary.truncated,
        "template_name": summary.template_name,
        "template_version": summary.template_version,
    }
    try:
        conn.execute("BEGIN")
        conn.execute(
            """
            INSERT INTO format_check_runs(
                id, batch_id, root_path, template_id, template_version_id, rule_snapshot_json,
                started_at, finished_at, status, scanned_tasks, passed_tasks, failed_tasks,
                warning_tasks, skipped_tasks, integrity_status, report_path, operator,
                previous_run_id, summary_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                batch_id,
                summary.root_path,
                summary.template_id or template.id,
                template_version_id,
                template.to_json(),
                summary.started_at,
                summary.finished_at,
                summary.status,
                summary.scanned_tasks,
                summary.passed_tasks,
                summary.failed_tasks,
                summary.warning_tasks,
                summary.skipped_tasks,
                summary.integrity_status,
                report_path,
                operator or summary.operator,
                previous_run_id,
                json.dumps(summary_json, ensure_ascii=False),
            ),
        )
        for task in result.tasks:
            task_result_id = _new_id("fmtt")
            conn.execute(
                """
                INSERT INTO format_check_task_results(
                    id, run_id, data_group_id, task_id, task_path, detected_mode, result,
                    image_count, txt_count, file_count, issue_count, fingerprint
                ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_result_id,
                    run_id,
                    task.task_id,
                    task.task_path,
                    task.detected_mode,
                    task.result,
                    task.image_count,
                    task.txt_count,
                    task.file_count,
                    task.issue_count,
                    task.fingerprint,
                ),
            )
            for issue in task.issues:
                conn.execute(
                    """
                    INSERT INTO format_check_issues(
                        id, run_id, task_result_id, code, severity, task_id, task_path,
                        file_path, expected, actual, message, suggestion
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _new_id("fmti"),
                        run_id,
                        task_result_id,
                        issue.code,
                        issue.severity,
                        issue.task_id or task.task_id,
                        issue.task_path or task.task_path,
                        issue.file_path,
                        issue.expected,
                        issue.actual,
                        issue.message,
                        issue.suggestion,
                    ),
                )
        for issue in result.run_issues:
            conn.execute(
                """
                INSERT INTO format_check_issues(
                    id, run_id, task_result_id, code, severity, task_id, task_path,
                    file_path, expected, actual, message, suggestion
                ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _new_id("fmti"),
                    run_id,
                    issue.code,
                    issue.severity,
                    issue.task_id,
                    issue.task_path,
                    issue.file_path,
                    issue.expected,
                    issue.actual,
                    issue.message,
                    issue.suggestion,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return run_id


def update_run_report(db: Database, run_id: str, report_path: str) -> None:
    conn = db.connect()
    conn.execute(
        "UPDATE format_check_runs SET report_path = ? WHERE id = ?",
        (report_path, run_id),
    )
    conn.commit()


def list_format_runs(db: Database, limit: int = 50) -> list[dict[str, Any]]:
    conn = db.connect()
    rows = conn.execute(
        """
        SELECT r.*, t.name AS template_name_current
        FROM format_check_runs r
        LEFT JOIN format_templates t ON t.id = r.template_id
        ORDER BY r.started_at DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        summary = _loads(item.get("summary_json"), {})
        item["template_name"] = (
            item.pop("template_name_current", None)
            or summary.get("template_name")
            or item.get("template_id")
            or ""
        )
        item["template_version"] = summary.get("template_version") or 0
        items.append(item)
    return items


def get_format_run(db: Database, run_id: str) -> dict[str, Any] | None:
    conn = db.connect()
    row = conn.execute(
        "SELECT * FROM format_check_runs WHERE id = ?", (run_id,)
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["summary"] = _loads(item.get("summary_json"), {})
    return item


def run_task_results(db: Database, run_id: str) -> list[dict[str, Any]]:
    conn = db.connect()
    rows = conn.execute(
        """
        SELECT * FROM format_check_task_results
        WHERE run_id = ?
        ORDER BY task_path COLLATE NOCASE
        """,
        (run_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def run_issues(db: Database, run_id: str) -> list[dict[str, Any]]:
    conn = db.connect()
    rows = conn.execute(
        """
        SELECT * FROM format_check_issues
        WHERE run_id = ?
        ORDER BY CASE severity WHEN 'error' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END,
                 task_path COLLATE NOCASE, code
        """,
        (run_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def run_task_paths(
    db: Database, run_id: str, results: list[str] | None = None
) -> list[str]:
    conn = db.connect()
    sql = "SELECT task_path FROM format_check_task_results WHERE run_id = ?"
    params: list[Any] = [run_id]
    if results:
        placeholders = ",".join("?" for _ in results)
        sql += f" AND result IN ({placeholders})"
        params.extend(results)
    rows = conn.execute(sql, params).fetchall()
    return [row["task_path"] for row in rows]


def run_task_snapshot(db: Database, run_id: str) -> dict[str, dict[str, Any]]:
    """用于差异对比：任务路径 → 结果/问题码/文件数。"""
    conn = db.connect()
    tasks = conn.execute(
        "SELECT * FROM format_check_task_results WHERE run_id = ?", (run_id,)
    ).fetchall()
    codes: dict[str, list[str]] = {}
    for row in conn.execute(
        "SELECT task_result_id, code FROM format_check_issues WHERE run_id = ? AND task_result_id IS NOT NULL",
        (run_id,),
    ).fetchall():
        codes.setdefault(row["task_result_id"], []).append(row["code"])
    snapshot: dict[str, dict[str, Any]] = {}
    for row in tasks:
        snapshot[row["task_path"]] = {
            "task_path": row["task_path"],
            "task_id": row["task_id"],
            "result": row["result"],
            "file_count": row["file_count"],
            "image_count": row["image_count"],
            "txt_count": row["txt_count"],
            "issue_codes": sorted(set(codes.get(row["id"], []))),
        }
    return snapshot


def delete_run(db: Database, run_id: str) -> None:
    conn = db.connect()
    conn.execute("DELETE FROM format_check_runs WHERE id = ?", (run_id,))
    conn.execute("DELETE FROM format_check_task_results WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM format_check_issues WHERE run_id = ?", (run_id,))
    conn.commit()
