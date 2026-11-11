"""业务目录刷新、批次同步与来源关联（v1.25.8）。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from modules.audit_log.service import AuditLogger
from modules.batch_management.service import BatchService
from modules.common.timeutil import now_iso
from modules.database.connection import Database, open_database
from modules.scanning.rules import RuleSet
from modules.scanning.scanner import ScanOptions
from modules.scanning.service import ScanService

from .discovery import discover_source
from .models import (
    BUSINESS_SOURCES,
    SOURCE_FINAL,
    SOURCE_LABELS,
    SOURCE_PENDING,
    ProjectRoot,
)
from .project_roots import ProjectRootService
from .repository import (
    find_batch_by_path,
    save_workspace_run,
    update_batch_snapshot,
    upsert_groups,
)

ProgressCallback = Callable[[int, int, str], None]
CancelCallback = Callable[[], bool]


class WorkspaceService:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else None
        self.db: Database = open_database(db_path)
        self.projects = ProjectRootService(db_path)

    # ── 规则 ─────────────────────────────────────────────
    def _ruleset(self) -> RuleSet:
        scanner = ScanService(self.db_path)
        try:
            return scanner.default_ruleset()
        finally:
            scanner.close()

    def _rule_version_id(self, rule_set_id: str) -> str | None:
        row = self.db.connect().execute(
            "SELECT active_version_id FROM scan_rule_sets WHERE id = ?", (rule_set_id,)
        ).fetchone()
        return row["active_version_id"] if row else None

    # ── 刷新 ─────────────────────────────────────────────
    def refresh_project(
        self,
        project_id: str,
        *,
        sources: tuple[str, ...] = BUSINESS_SOURCES,
        operator: str = "",
        options: ScanOptions | None = None,
        progress: ProgressCallback | None = None,
        should_cancel: CancelCallback | None = None,
    ) -> dict[str, Any]:
        project = self.projects.get(project_id)
        if project is None:
            raise ValueError("项目目录配置不存在")
        if not project.enabled:
            raise ValueError("该项目目录已停用，请先在管理设置中启用")
        ruleset = self._ruleset()
        rule_version_id = self._rule_version_id(ruleset.id)
        summaries: list[dict[str, Any]] = []
        for index, source_type in enumerate(sources):
            label = SOURCE_LABELS.get(source_type, source_type)
            path = project.path_for(source_type)
            summary: dict[str, Any] = {
                "source_type": source_type,
                "source_label": label,
                "root_path": path,
                "status": "skipped",
                "new_batches": 0,
                "existing_batches": 0,
                "changed_batches": 0,
                "candidates": 0,
                "groups": 0,
                "total_dirs": 0,
                "unrecognized_dirs": 0,
                "access_failures": 0,
                "duration_ms": 0,
                "truncated": False,
                "errors": [],
            }
            if not path:
                summary["status"] = "not_configured"
                summary["errors"].append(f"未配置{label}")
                summaries.append(summary)
                continue
            prefix = f"{label} {index + 1}/{len(sources)}"

            def on_progress(done: int, total: int, message: str, prefix=prefix) -> None:
                if progress:
                    progress(done, total, f"{prefix}：{message}")

            result = discover_source(
                path,
                source_type,
                ruleset,
                project_name=project.project_name,
                project_root_id=project.id,
                options=options,
                progress=on_progress,
                should_cancel=should_cancel,
            )
            pending_paths = {
                str(row["batch_path"])
                for row in self.db.connect().execute(
                    """
                    SELECT c.batch_path FROM scan_candidates c
                    JOIN scan_runs r ON r.id = c.run_id
                    WHERE r.source_type = ? AND r.project_root_id = ?
                      AND c.status IN ('pending', 'partial', 'duplicate', 'rescan')
                      AND c.confirmed_batch_id IS NULL
                    """,
                    (source_type, project.id),
                ).fetchall()
            }
            known_paths = {
                str(row["batch_path"])
                for row in self.db.connect().execute(
                    "SELECT batch_path FROM batches WHERE source_type = ?",
                    (source_type,),
                ).fetchall()
            }
            new_candidates = [
                item
                for item in result.candidates
                if item.batch_path not in pending_paths and item.batch_path not in known_paths
            ]
            save_workspace_run(
                self.db,
                result,
                operator=operator,
                rule_set_id=ruleset.id,
                rule_version_id=rule_version_id,
            )
            summary.update(
                {
                    "status": result.status,
                    "candidates": len(result.candidates),
                    "new_batches": len(new_candidates),
                    "groups": len(result.groups),
                    "total_dirs": result.total_dirs,
                    "unrecognized_dirs": result.unrecognized_dirs,
                    "access_failures": result.access_failures,
                    "duration_ms": result.duration_ms,
                    "truncated": result.truncated,
                }
            )
            summary["errors"] = [
                issue.message for issue in result.issues if issue.code in {"DR-001", "DR-014", "DR-020"}
            ][:5]
            if result.status == "failed" and not summary["errors"]:
                summary["errors"] = ["目录访问失败；历史批次已保留。"]

            for candidate in result.candidates:
                if should_cancel and should_cancel():
                    break
                existing = find_batch_by_path(self.db, source_type, candidate.batch_path)
                if existing:
                    added, changed = upsert_groups(
                        self.db, existing["id"], candidate.groups, root_path=result.root
                    )
                    relative = os.path.relpath(candidate.batch_path, result.root)
                    update_batch_snapshot(
                        self.db,
                        existing["id"],
                        candidate=candidate,
                        project_name=project.project_name,
                        source_root_id=project.id,
                        source_root_path=result.root,
                        relative_path="" if relative == "." else relative,
                        data_count=len(candidate.groups),
                    )
                    summary["existing_batches"] += 1
                    if added or changed:
                        summary["changed_batches"] += 1
                else:
                    # 新批次已计入 new_batches；待人工确认后才会创建批次。
                    pass
            self.projects.mark_scanned(project.id, source_type)
            summaries.append(summary)
            try:
                AuditLogger(self.db).log(
                    module="workspace",
                    action="refresh_source",
                    operator=operator,
                    target_path=path,
                    status="ok" if result.status in {"completed", "partial"} else "failed",
                    batch_id=None,
                    detail=summary,
                )
            except Exception:
                pass
            if should_cancel and should_cancel():
                break
        return {"project": project, "summaries": summaries}

    # ── 确认候选 ─────────────────────────────────────────
    def confirm_candidate(self, candidate_id: str, *, operator: str = "") -> str:
        service = BatchService(self.db_path)
        try:
            return service.confirm_candidate(candidate_id, operator=operator)
        finally:
            service.close()

    def ignore_candidate(self, candidate_id: str, *, operator: str = "", reason: str = "") -> None:
        service = BatchService(self.db_path)
        try:
            service.ignore_candidate(candidate_id, operator=operator, reason=reason)
        finally:
            service.close()

    # ── 来源批次关联 ─────────────────────────────────────
    def parent_suggestions(self, batch_id: str, limit: int = 10) -> list[dict[str, Any]]:
        conn = self.db.connect()
        child = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
        if not child:
            return []
        rows = conn.execute(
            """
            SELECT b.id, b.display_id, b.name, b.batch_path, b.submit_date,
                   b.source_type, b.qualified_count, b.rejected_count,
                   COUNT(DISTINCT g.task_id) AS overlap
            FROM data_groups g
            JOIN batches b ON b.id = g.batch_id
            WHERE b.id <> ?
              AND b.source_type IN ('pending_acceptance', 'legacy')
              AND g.task_id IN (
                  SELECT task_id FROM data_groups WHERE batch_id = ? AND task_id <> ''
              )
            GROUP BY b.id
            ORDER BY overlap DESC, COALESCE(b.submit_date, b.created_at) DESC
            LIMIT ?
            """,
            (batch_id, batch_id, int(limit)),
        ).fetchall()
        return [dict(row) for row in rows]

    def link_parent_batch(self, batch_id: str, parent_batch_id: str, *, operator: str = "") -> None:
        conn = self.db.connect()
        child = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
        parent = conn.execute("SELECT * FROM batches WHERE id = ?", (parent_batch_id,)).fetchone()
        if not child or not parent:
            raise ValueError("批次不存在")
        if child["source_type"] != SOURCE_FINAL:
            raise ValueError("只有申请终验批次可以关联原始待验批次")
        if parent["source_type"] not in {"pending_acceptance", "legacy"}:
            raise ValueError("原始批次必须是待验批次或历史批次")
        overlap = conn.execute(
            """
            SELECT COUNT(DISTINCT c.task_id) AS c
            FROM data_groups c
            JOIN data_groups p ON p.task_id = c.task_id
            WHERE c.batch_id = ? AND p.batch_id = ? AND c.task_id <> ''
            """,
            (batch_id, parent_batch_id),
        ).fetchone()
        if int(overlap["c"] or 0) <= 0:
            raise ValueError("两个批次没有共同任务 ID，不能建立关联")
        conn.execute(
            "UPDATE batches SET parent_batch_id = ?, updated_at = ? WHERE id = ?",
            (parent_batch_id, now_iso(), batch_id),
        )
        conn.commit()
        AuditLogger(self.db).log(
            module="workspace",
            action="link_parent_batch",
            operator=operator,
            batch_id=batch_id,
            detail={"parent_batch_id": parent_batch_id, "overlap": int(overlap["c"] or 0)},
        )

    def clear_parent_batch(self, batch_id: str, *, operator: str = "") -> None:
        conn = self.db.connect()
        conn.execute(
            "UPDATE batches SET parent_batch_id = NULL, updated_at = ? WHERE id = ?",
            (now_iso(), batch_id),
        )
        conn.commit()
        AuditLogger(self.db).log(
            module="workspace",
            action="clear_parent_batch",
            operator=operator,
            batch_id=batch_id,
        )

    def close(self) -> None:
        self.projects.close()
        self.db.close()

    def __enter__(self) -> "WorkspaceService":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
