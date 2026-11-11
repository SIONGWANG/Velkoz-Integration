"""其他验收员记录导入服务：只读发现、类型识别、增量去重与导入日志。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Iterable

from modules.audit_log.service import AuditLogger
from modules.common.path_guard import normalize_path
from modules.common.timeutil import now_iso
from modules.config import app_config
from modules.database.connection import Database, open_database
from modules.database.repositories import record_import_repo

from .discovery import DiscoveryResult, discover_files
from .errors import RecordImportError, catalog_rows
from .models import (
    FILE_READY,
    FILE_UNRECOGNIZED,
    IMPORT_COMPLETED,
    IMPORT_FAILED,
    IMPORT_PARTIAL,
    LINK_MATCHED,
    LINK_NOTE_ONLY,
    LINK_UNMATCHED,
    TYPE_OTHER_STATS,
    TYPE_UNCLASSIFIED,
    DiscoveredRecordFile,
    ParsedRecordRow,
    RecordImportResult,
)
from .parser import file_sha256, inspect_table, parse_rows

ProgressCallback = Callable[[int, int, str], None]
CancelCallback = Callable[[], bool]


def _new_result(source_path: str, source_type: str = TYPE_UNCLASSIFIED) -> RecordImportResult:
    return RecordImportResult(
        source_path=normalize_path(source_path),
        source_type=source_type,
    )


def _operator_summary(values: Iterable[str]) -> str:
    seen: list[str] = []
    for value in values:
        text = (value or "").strip()
        if text and text not in seen:
            seen.append(text)
    return "、".join(seen[:5])


class RecordImportService:
    def __init__(self, db_path: str | Path | None = None):
        resolved = (
            Path(db_path)
            if db_path
            else app_config.get_db_path()
        )
        self.db: Database = open_database(resolved)
        self.audit = AuditLogger(self.db)

    # ── 发现 ─────────────────────────────────────────────
    def discover(
        self,
        root_path: str,
        *,
        recursive: bool = False,
        progress: ProgressCallback | None = None,
        should_cancel: CancelCallback | None = None,
        max_files: int = 5000,
    ) -> DiscoveryResult:
        return discover_files(
            root_path,
            recursive=recursive,
            max_files=max_files,
            progress=progress,
            should_cancel=should_cancel,
        )

    # ── 导入 ─────────────────────────────────────────────
    def import_file(
        self,
        path: str,
        *,
        operator: str = "",
        batch_id: str = "",
        dry_run: bool = False,
    ) -> RecordImportResult:
        source = normalize_path(path)
        if not os.path.isfile(source):
            raise RecordImportError("RI-002", f"文件不存在：{source}")
        try:
            stat = os.stat(source)
        except OSError as exc:
            raise RecordImportError("RI-002", f"无法读取文件信息：{exc}") from exc
        explicit_batch = self._validate_batch(batch_id)
        warnings: list[str] = []
        if batch_id and not explicit_batch:
            warnings.append(f"批次不存在，已忽略：{batch_id}")

        try:
            info = inspect_table(source)
        except RecordImportError as exc:
            result = _new_result(source)
            result.status = IMPORT_FAILED
            result.note = f"{exc.code} {exc.message}"
            result.warnings = warnings
            if not dry_run:
                self._log_failure(source, stat, result, operator)
                self._audit(source, result, operator, dry_run)
            return result
        except Exception as exc:  # 未预期解析异常
            result = _new_result(source)
            result.status = IMPORT_FAILED
            result.note = f"RI-010 {type(exc).__name__}"
            result.warnings = warnings
            if not dry_run:
                self._log_failure(source, stat, result, operator)
                self._audit(source, result, operator, dry_run)
            return result

        source_type = str(info.get("source_type") or TYPE_UNCLASSIFIED)
        result = _new_result(source, source_type)
        result.warnings = warnings
        headers = list(info.get("headers") or [])
        rows = list(info.get("rows") or [])
        result.rows_total = len(rows)
        result.operator_detected = str(info.get("operator") or operator or "")

        if source_type == TYPE_UNCLASSIFIED:
            result.status = IMPORT_FAILED
            result.note = "RI-004 表结构无法识别，未导入"
            if not dry_run:
                self._log_failure(source, stat, result, operator)
                self._audit(source, result, operator, dry_run)
            return result
        if source_type == TYPE_OTHER_STATS:
            result.status = IMPORT_COMPLETED
            result.note = "RI-014 统计/汇总表仅识别，不导入逐条记录"
            if not dry_run:
                import_id = self._log_start(source, stat, result, operator)
                result.import_id = import_id
                record_import_repo.update_import(
                    self.db,
                    import_id,
                    status=result.status,
                    rows_total=result.rows_total,
                    note=result.note,
                    imported_at=now_iso(),
                )
                self._audit(source, result, operator, dry_run)
            return result

        try:
            parsed, errors = parse_rows(source, headers, rows)
        except RecordImportError as exc:
            result.status = IMPORT_FAILED
            result.note = f"{exc.code} {exc.message}"
            result.rows_error = 1
            if not dry_run:
                self._log_failure(source, stat, result, operator)
                self._audit(source, result, operator, dry_run)
            return result

        result.rows_error = len(errors)
        if errors:
            result.warnings.extend(errors[:20])

        import_id = ""
        if not dry_run:
            import_id = self._log_start(source, stat, result, operator)
            result.import_id = import_id

        linked_batches: list[str] = []
        for row in parsed:
            self._link_group(row, explicit_batch, warnings, linked_batches)
        result.rows = parsed
        result.operator_detected = _operator_summary(
            [row.operator for row in parsed] + ([operator] if operator else [])
        )
        result.batch_id = explicit_batch or (linked_batches[0] if linked_batches else "")

        for row in parsed:
            if dry_run:
                exists = record_import_repo.record_exists(self.db, row.fingerprint)
            else:
                exists = not record_import_repo.insert_record(
                    self.db,
                    {
                        "import_id": import_id,
                        "data_group_id": row.data_group_id or None,
                        "task_id": row.task_id,
                        "operator": row.operator or operator,
                        "result": row.result,
                        "l1": row.l1,
                        "l2": row.l2,
                        "note": row.note,
                        "tags_raw": row.tags_raw,
                        "evidence_raw": row.evidence_raw,
                        "record_time": row.record_time,
                        "source_row": row.source_row,
                        "source_row_fingerprint": row.fingerprint,
                        "link_status": row.link_status,
                    },
                )
            if exists:
                result.rows_skipped += 1
            else:
                result.rows_new += 1

        if result.rows_error and not result.rows_new and not result.rows_skipped:
            result.status = IMPORT_FAILED
        elif result.rows_error or (result.rows_skipped and result.rows_new):
            result.status = IMPORT_PARTIAL
        else:
            result.status = IMPORT_COMPLETED
        notes = []
        if result.rows_skipped:
            notes.append(f"RI-009 重复行已跳过 {result.rows_skipped} 条")
        if any(row.link_status == LINK_UNMATCHED and row.task_id for row in parsed):
            notes.append("RI-012 部分任务未匹配数据组，已保留为未关联")
        if any(row.link_status == LINK_NOTE_ONLY for row in parsed):
            notes.append("RI-013 纯备注记录已保留")
        result.note = "；".join(notes) or "导入完成"

        if not dry_run:
            record_import_repo.update_import(
                self.db,
                import_id,
                rows_total=result.rows_total,
                rows_new=result.rows_new,
                rows_skipped=result.rows_skipped,
                rows_error=result.rows_error,
                operator_detected=result.operator_detected,
                batch_id=result.batch_id or None,
                status=result.status,
                note=result.note,
                imported_at=now_iso(),
            )
            if result.batch_id:
                self._sync_batch_acceptance(result.batch_id)
        self._audit(source, result, operator, dry_run)
        return result

    def _sync_batch_acceptance(self, batch_id: str) -> None:
        """用已导入验收记录刷新批次统计；没有记录时不伪造完成。"""
        conn = self.db.connect()
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT COALESCE(ir.task_id, ir.data_group_id)) AS accepted_tasks,
                   COALESCE(SUM(CASE WHEN ir.result IN ('合格', '修改后合格') THEN 1 ELSE 0 END), 0) AS qualified,
                   COALESCE(SUM(CASE WHEN ir.result IN ('不合格', '作废') THEN 1 ELSE 0 END), 0) AS rejected
            FROM imported_records ir
            LEFT JOIN record_imports ri ON ri.id = ir.import_id
            LEFT JOIN data_groups g ON g.id = ir.data_group_id
            WHERE COALESCE(ri.batch_id, g.batch_id) = ?
            """,
            (batch_id,),
        ).fetchone()
        batch = conn.execute("SELECT data_count FROM batches WHERE id = ?", (batch_id,)).fetchone()
        if row is None or batch is None:
            return
        accepted = int(row["accepted_tasks"] or 0)
        qualified = int(row["qualified"] or 0)
        rejected = int(row["rejected"] or 0)
        data_count = int(batch["data_count"] or 0)
        remaining = max(0, data_count - accepted) if data_count > 0 else 0
        if accepted <= 0:
            status = "not_started"
        elif data_count > 0 and remaining <= 0:
            status = "completed"
        else:
            status = "in_progress"
        conn.execute(
            """
            UPDATE batches
            SET qualified_count = ?, rejected_count = ?, remaining_count = ?,
                acceptance_status = ?, updated_at = ?
            WHERE id = ?
            """,
            (qualified, rejected, remaining, status, now_iso(), batch_id),
        )
        conn.commit()

    def import_files(
        self,
        paths: Iterable[str],
        *,
        operator: str = "",
        batch_id: str = "",
        dry_run: bool = False,
        progress: ProgressCallback | None = None,
    ) -> list[RecordImportResult]:
        targets = [path for path in paths if path]
        results: list[RecordImportResult] = []
        total = len(targets)
        for index, path in enumerate(targets, start=1):
            if progress:
                progress(index - 1, total, f"导入 {index}/{total}：{os.path.basename(path)}")
            results.append(
                self.import_file(
                    path, operator=operator, batch_id=batch_id, dry_run=dry_run
                )
            )
        if progress:
            progress(total, max(total, 1), "导入完成")
        return results

    # ── 关联 ─────────────────────────────────────────────
    def _link_group(
        self,
        row: ParsedRecordRow,
        explicit_batch: str,
        warnings: list[str],
        linked_batches: list[str],
    ) -> None:
        if not row.task_id:
            row.link_status = LINK_NOTE_ONLY if (row.note or row.tags_raw) else LINK_UNMATCHED
            return
        groups = record_import_repo.find_data_groups(
            self.db, row.task_id, explicit_batch or None
        )
        if not groups:
            row.link_status = LINK_UNMATCHED
            return
        group = groups[0]
        row.data_group_id = str(group.get("id") or "")
        row.batch_id = str(group.get("batch_id") or "") or explicit_batch
        row.link_status = LINK_MATCHED
        if row.batch_id and row.batch_id not in linked_batches:
            linked_batches.append(row.batch_id)
        if len(groups) > 1:
            warnings.append(f"任务 {row.task_id} 匹配多个数据组，已取第一个")

    def _validate_batch(self, batch_id: str) -> str:
        if not batch_id:
            return ""
        for item in record_import_repo.list_batches(self.db, 1000):
            if item["id"] == batch_id:
                return batch_id
        return ""

    # ── 日志 ─────────────────────────────────────────────
    def _base_import_payload(
        self, source: str, stat: os.stat_result, result: RecordImportResult, operator: str
    ) -> dict[str, Any]:
        try:
            digest = file_sha256(source)
        except RecordImportError:
            digest = ""
        return {
            "source_path": source,
            "source_type": result.source_type,
            "file_size": int(stat.st_size),
            "file_mtime_ns": int(stat.st_mtime_ns),
            "file_hash": digest,
            "rows_total": result.rows_total,
            "rows_new": result.rows_new,
            "rows_skipped": result.rows_skipped,
            "rows_error": result.rows_error,
            "operator_detected": result.operator_detected,
            "batch_id": result.batch_id or None,
            "status": result.status,
            "imported_at": now_iso(),
            "imported_by": operator,
            "note": result.note,
        }

    def _log_start(
        self, source: str, stat: os.stat_result, result: RecordImportResult, operator: str
    ) -> str:
        return record_import_repo.create_import(
            self.db, self._base_import_payload(source, stat, result, operator)
        )

    def _log_failure(
        self,
        source: str,
        stat: os.stat_result,
        result: RecordImportResult,
        operator: str = "",
    ) -> None:
        record_import_repo.create_import(
            self.db, self._base_import_payload(source, stat, result, operator)
        )

    def _audit(
        self,
        source: str,
        result: RecordImportResult,
        operator: str,
        dry_run: bool,
    ) -> None:
        if dry_run:
            return
        self.audit.log(
            "record_import",
            "import_file",
            operator=operator,
            status="ok" if result.status == IMPORT_COMPLETED else result.status,
            source_path=source,
            detail={
                "import_id": result.import_id,
                "source_type": result.source_type,
                "rows_total": result.rows_total,
                "rows_new": result.rows_new,
                "rows_skipped": result.rows_skipped,
                "rows_error": result.rows_error,
            },
            run_id=result.import_id or None,
        )

    # ── 查询 ─────────────────────────────────────────────
    def list_imports(self, limit: int = 200) -> list[dict[str, Any]]:
        return record_import_repo.list_imports(self.db, limit)

    def get_import(self, import_id: str) -> dict[str, Any] | None:
        return record_import_repo.get_import(self.db, import_id)

    def list_records(self, import_id: str | None = None, limit: int = 5000) -> list[dict[str, Any]]:
        return record_import_repo.list_records(self.db, import_id, limit=limit)

    def recent_records(self, limit: int = 500) -> list[dict[str, Any]]:
        return record_import_repo.recent_records(self.db, limit)

    def list_batches(self, limit: int = 200) -> list[dict[str, Any]]:
        return record_import_repo.list_batches(self.db, limit)

    def summary(self) -> dict[str, Any]:
        return record_import_repo.import_summary(self.db)

    @staticmethod
    def error_catalog() -> list[list[str]]:
        return catalog_rows()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "RecordImportService":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
