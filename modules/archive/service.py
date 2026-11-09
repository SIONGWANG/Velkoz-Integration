"""合格数据归档服务（v1.25.6）。

安全边界：
- 源图片/TXT/CSV 只读；从不移动、删除、改名或写回源目录；
- 目标已存在时不覆盖：相同文件跳过，不同文件标记冲突；
- 复制后按大小/哈希校验，并写入 archive_items；
- 中断后保留目标已复制内容，下次运行从未完成的数据组继续；
- 归档报告只写管理数据目录下的 reports/archive，不写源目录；
- 不调用旧的归集/删除逻辑。
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable

from modules.audit_log.service import AuditLogger
from modules.common.path_guard import is_reparse_point, normalize_path, safe_join
from modules.common.timeutil import now_iso
from modules.config import app_config
from modules.database.connection import Database, open_database

from .models import (
    DEFAULT_QUALIFIED_RESULTS,
    DEFAULT_QUALIFIED_STATUSES,
    FILTER_ALL,
    FILTER_IMPORTED,
    FILTER_MANUAL,
    FILTER_QUALIFIED,
    FILTER_STATUS,
    ITEM_CONFLICT,
    ITEM_FAILED,
    ITEM_PENDING,
    ITEM_SKIPPED,
    ITEM_STATUS_LABELS,
    ITEM_VERIFIED,
    JOB_COMPLETED,
    JOB_FAILED,
    JOB_PAUSED,
    JOB_PREVIEW,
    JOB_RUNNING,
    JOB_STATUS_LABELS,
    ArchivePreview,
    ArchiveRunResult,
)
from .planner import (
    build_preview,
    parse_identity,
    scan_group_files,
)

ProgressCallback = Callable[[int, int, str], None]
CancelCallback = Callable[[], bool]

_CHUNK = 1024 * 1024


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4()}"


def split_values(value: str | Iterable[str] | None, defaults: Iterable[str]) -> list[str]:
    """把逗号/中文逗号/列表格式的筛选值规范化。"""
    if value is None:
        items: list[str] = []
    elif isinstance(value, str):
        items = [part.strip() for part in value.replace("，", ",").split(",")]
    else:
        items = [str(part).strip() for part in value]
    result = [item for item in items if item]
    return result or [str(item) for item in defaults]


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(root: str, relatives: Iterable[str], with_hash: bool) -> str:
    digest = hashlib.sha256()
    for relative in sorted(relatives, key=lambda text: text.casefold()):
        path = os.path.join(root, relative)
        try:
            size = os.stat(path).st_size
        except OSError:
            size = -1
        line = f"{relative}|{size}"
        if with_hash:
            try:
                line += "|" + file_sha256(path)
            except OSError:
                line += "|unreadable"
        digest.update((line + "\n").encode("utf-8", errors="replace"))
    return digest.hexdigest()


def _same_file(source: str, dest: str, verify_hash: bool) -> bool:
    try:
        source_stat = os.stat(source)
        dest_stat = os.stat(dest)
    except OSError:
        return False
    if source_stat.st_size != dest_stat.st_size:
        return False
    if not verify_hash:
        return True
    try:
        return file_sha256(source) == file_sha256(dest)
    except OSError:
        return False


def _copy_exclusive(source: str, dest: str) -> None:
    """以独占方式创建目标文件；目标已存在时抛 FileExistsError，绝不覆盖。"""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = None
    try:
        fd = os.open(dest, flags, 0o666)
        with open(source, "rb") as reader, os.fdopen(fd, "wb") as writer:
            fd = None
            while True:
                chunk = reader.read(_CHUNK)
                if not chunk:
                    break
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
    finally:
        if fd is not None:
            os.close(fd)


def _dest_extras(dest_root: str, expected: set[str]) -> list[str]:
    extras: list[str] = []
    if not os.path.isdir(dest_root):
        return extras
    for current, dirs, names in os.walk(dest_root, followlinks=False):
        dirs[:] = [
            name
            for name in dirs
            if not is_reparse_point(os.path.join(current, name))
        ]
        for name in names:
            relative = os.path.relpath(os.path.join(current, name), dest_root)
            if os.path.normcase(relative) not in expected:
                extras.append(relative)
    return extras


def _status_count(rows: Iterable[dict[str, Any]], status: str) -> int:
    return sum(1 for row in rows if row.get("status") == status)


class ArchiveService:
    def __init__(
        self,
        db_path: str | Path | None = None,
        reports_dir: str | Path | None = None,
    ):
        self.db_path = Path(db_path) if db_path else app_config.get_db_path()
        self.db: Database = open_database(self.db_path)
        self.audit = AuditLogger(self.db)
        if reports_dir:
            self.reports_dir = Path(normalize_path(reports_dir))
        elif db_path:
            self.reports_dir = (self.db_path.resolve().parent.parent / "reports" / "archive")
        else:
            self.reports_dir = app_config.get_reports_dir() / "archive"

    def close(self) -> None:
        self.db.close()

    # ── 读取 ─────────────────────────────────────────────
    def list_batches(self, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.db.connect().execute(
            """
            SELECT b.*,
                   (SELECT COUNT(*) FROM data_groups g WHERE g.batch_id = b.id) AS group_count
            FROM batches b
            ORDER BY COALESCE(b.submit_date, b.created_at) DESC, b.created_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        row = self.db.connect().execute(
            "SELECT * FROM batches WHERE id = ?",
            (batch_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_groups(self, batch_id: str) -> list[dict[str, Any]]:
        rows = self.db.connect().execute(
            "SELECT * FROM data_groups WHERE batch_id = ? ORDER BY normalized_path COLLATE NOCASE",
            (batch_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def select_groups(
        self,
        batch_id: str,
        filter_mode: str = FILTER_ALL,
        filter_value: str | Iterable[str] | None = None,
        group_ids: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        """按合格条件选择数据组；只读管理数据库。"""
        conn = self.db.connect()
        sql = "SELECT g.* FROM data_groups g WHERE g.batch_id = ?"
        params: list[Any] = [batch_id]
        if filter_mode == FILTER_QUALIFIED:
            statuses = [str(item) for item in DEFAULT_QUALIFIED_STATUSES]
            results = [str(item) for item in DEFAULT_QUALIFIED_RESULTS]
            status_placeholders = ",".join("?" for _ in statuses)
            result_placeholders = ",".join("?" for _ in results)
            sql += (
                f" AND (g.status IN ({status_placeholders})"
                " OR EXISTS (SELECT 1 FROM imported_records ir"
                " WHERE ir.data_group_id = g.id AND ir.result IN ("
                + result_placeholders
                + ")))"
            )
            params.extend(statuses)
            params.extend(results)
        elif filter_mode == FILTER_STATUS:
            values = split_values(filter_value, DEFAULT_QUALIFIED_STATUSES)
            placeholders = ",".join("?" for _ in values)
            sql += f" AND g.status IN ({placeholders})"
            params.extend(values)
        elif filter_mode == FILTER_IMPORTED:
            values = split_values(filter_value, DEFAULT_QUALIFIED_RESULTS)
            placeholders = ",".join("?" for _ in values)
            sql += (
                " AND EXISTS (SELECT 1 FROM imported_records ir"
                " WHERE ir.data_group_id = g.id AND ir.result IN ("
                + placeholders
                + "))"
            )
            params.extend(values)
        elif filter_mode == FILTER_MANUAL:
            ids = [str(item) for item in (group_ids or []) if str(item).strip()]
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            sql += f" AND g.id IN ({placeholders})"
            params.extend(ids)
        elif filter_mode != FILTER_ALL:
            raise ValueError(f"不支持的筛选方式：{filter_mode}")
        sql += " ORDER BY g.normalized_path COLLATE NOCASE"
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def preview(
        self,
        batch_id: str,
        output_root: str,
        *,
        start_layer: str,
        name_template: str,
        operator: str = "",
        filter_mode: str = FILTER_ALL,
        filter_value: str | Iterable[str] | None = None,
        group_ids: Iterable[str] | None = None,
        custom_prefix: str = "",
    ) -> ArchivePreview:
        batch = self.get_batch(batch_id)
        if not batch:
            raise ValueError("批次不存在")
        identity = parse_identity(batch.get("identity_json"))
        groups = self.select_groups(batch_id, filter_mode, filter_value, group_ids)
        value_text = (
            filter_value
            if isinstance(filter_value, str)
            else ",".join(str(item) for item in (filter_value or []))
        )
        return build_preview(
            batch=batch,
            identity=identity,
            groups=groups,
            output_root=output_root,
            start_layer=start_layer,
            name_template=name_template,
            operator=operator,
            filter_mode=filter_mode,
            filter_value=value_text,
            custom_prefix=custom_prefix,
        )

    # ── 任务 ─────────────────────────────────────────────
    def create_job(
        self,
        preview: ArchivePreview,
        *,
        operator: str = "",
        note: str = "",
        verify_hash: bool = True,
    ) -> str:
        if preview.errors:
            raise ValueError("预览存在错误，无法创建归档任务：" + "；".join(preview.errors))
        if not preview.groups:
            raise ValueError("没有可归档的数据组")
        conn = self.db.connect()
        job_id = _new_id("archive")
        now = now_iso()
        conn.execute(
            """
            INSERT INTO archive_jobs(
                id, batch_id, source_root, output_root, start_layer, name_template,
                custom_prefix, filter_mode, filter_value, status, total_items,
                total_files, bytes_total, verify_hash, operator, note,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                preview.batch_id,
                preview.source_root,
                preview.output_root,
                preview.start_layer,
                preview.name_template,
                preview.custom_prefix,
                preview.filter_mode,
                preview.filter_value,
                JOB_PREVIEW,
                1 if verify_hash else 0,
                preview.total_items,
                preview.total_files,
                preview.total_bytes,
                operator,
                note,
                now,
                now,
            ),
        )
        for group in preview.groups:
            status = ITEM_FAILED if group.error else ITEM_PENDING
            conn.execute(
                """
                INSERT INTO archive_items(
                    id, job_id, data_group_id, task_id, source_path, dest_path,
                    status, file_count, bytes, error_message, created_at, updated_at,
                    copied_bytes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    _new_id("arcitem"),
                    job_id,
                    group.data_group_id or None,
                    group.task_id,
                    group.source_path,
                    group.dest_path,
                    status,
                    group.file_count,
                    group.bytes_total,
                    group.error,
                    now,
                    now,
                ),
            )
        conn.commit()
        self._sync_job_counts(job_id, JOB_PREVIEW)
        self.audit.log(
            module="archive",
            action="create_archive_job",
            operator=operator,
            batch_id=preview.batch_id,
            target_path=preview.output_root,
            detail={
                "job_id": job_id,
                "items": preview.total_items,
                "files": preview.total_files,
                "bytes": preview.total_bytes,
                "start_layer": preview.start_layer,
                "template": preview.name_template,
            },
        )
        return job_id

    def list_jobs(self, limit: int = 50, batch_id: str = "") -> list[dict[str, Any]]:
        conn = self.db.connect()
        sql = (
            "SELECT j.*, b.name AS batch_name, b.display_id AS batch_display_id"
            " FROM archive_jobs j LEFT JOIN batches b ON b.id = j.batch_id"
        )
        params: list[Any] = []
        if batch_id:
            sql += " WHERE j.batch_id = ?"
            params.append(batch_id)
        sql += " ORDER BY COALESCE(j.created_at, '') DESC LIMIT ?"
        params.append(int(limit))
        return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.db.connect().execute(
            """
            SELECT j.*, b.name AS batch_name, b.display_id AS batch_display_id
            FROM archive_jobs j LEFT JOIN batches b ON b.id = j.batch_id
            WHERE j.id = ?
            """,
            (job_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_items(self, job_id: str) -> list[dict[str, Any]]:
        rows = self.db.connect().execute(
            "SELECT * FROM archive_items WHERE job_id = ? ORDER BY source_path COLLATE NOCASE",
            (job_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    # ── 复制 ─────────────────────────────────────────────
    def run_job(
        self,
        job_id: str,
        *,
        progress: ProgressCallback | None = None,
        should_cancel: CancelCallback | None = None,
        verify_hash: bool | None = None,
    ) -> ArchiveRunResult:
        job = self.get_job(job_id)
        if not job:
            raise ValueError("归档任务不存在")
        verify = bool(job.get("verify_hash")) if verify_hash is None else bool(verify_hash)
        items = self.list_items(job_id)
        if not items:
            raise ValueError("归档任务没有条目")
        conn = self.db.connect()
        started_at = job.get("started_at") or now_iso()
        conn.execute(
            "UPDATE archive_jobs SET status = ?, started_at = ?, finished_at = NULL, updated_at = ? WHERE id = ?",
            (JOB_RUNNING, started_at, now_iso(), job_id),
        )
        conn.commit()

        result = ArchiveRunResult(job_id=job_id, status=JOB_RUNNING, total_items=len(items))
        cancelled = False
        total = len(items)
        for index, item in enumerate(items, start=1):
            if should_cancel and should_cancel():
                cancelled = True
                break
            if item.get("status") == ITEM_VERIFIED:
                if progress:
                    progress(index, total, f"已校验，跳过：{item.get('task_id') or ''}")
                continue
            try:
                outcome = self._copy_item(item, verify_hash=verify, should_cancel=should_cancel)
            except Exception as exc:  # 单项异常不拖垮整个任务
                outcome = {
                    "status": ITEM_FAILED,
                    "copied_bytes": 0,
                    "source_fingerprint": "",
                    "dest_fingerprint": "",
                    "error_message": f"{type(exc).__name__}: {exc}",
                    "cancelled": False,
                }
            if outcome.get("cancelled"):
                cancelled = True
            self._save_item_outcome(item, outcome)
            self._sync_job_counts(job_id, JOB_RUNNING)
            if progress:
                progress(
                    index,
                    total,
                    f"{ITEM_STATUS_LABELS.get(outcome['status'], outcome['status'])}："
                    f"{item.get('task_id') or item.get('source_path')}",
                )
            if cancelled:
                break

        fresh_items = self.list_items(job_id)
        conflicts = _status_count(fresh_items, ITEM_CONFLICT)
        failures = _status_count(fresh_items, ITEM_FAILED)
        if cancelled:
            final_status = JOB_PAUSED
        elif failures or conflicts:
            final_status = JOB_FAILED
        else:
            final_status = JOB_COMPLETED
        report_path = ""
        conn.execute(
            """
            UPDATE archive_jobs
            SET status = ?, finished_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                final_status,
                None if cancelled else now_iso(),
                now_iso(),
                job_id,
            ),
        )
        conn.commit()
        self._sync_job_counts(job_id, final_status)
        report_path = self._write_report(job_id, final_status)
        if report_path:
            conn.execute(
                "UPDATE archive_jobs SET report_path = ?, updated_at = ? WHERE id = ?",
                (report_path, now_iso(), job_id),
            )
            conn.commit()

        summary = self._result_from_job(job_id, final_status, cancelled)
        self.audit.log(
            module="archive",
            action="run_archive_job",
            operator=str(job.get("operator") or ""),
            status="ok" if final_status == JOB_COMPLETED else "warning",
            batch_id=job.get("batch_id"),
            target_path=job.get("output_root"),
            detail={
                "job_id": job_id,
                "status": final_status,
                "items": summary.total_items,
                "verified": summary.verified_items,
                "skipped": summary.skipped_items,
                "conflicts": summary.conflict_items,
                "failed": summary.failed_items,
                "bytes_copied": summary.bytes_copied,
                "report": report_path,
            },
        )
        return summary

    def _copy_item(
        self,
        item: dict[str, Any],
        *,
        verify_hash: bool,
        should_cancel: CancelCallback | None,
    ) -> dict[str, Any]:
        source_root = str(item.get("source_path") or "")
        dest_root = str(item.get("dest_path") or "")
        duplicate = self.db.connect().execute(
            "SELECT task_id FROM archive_items WHERE job_id = ? AND id <> ? AND "
            "LOWER(dest_path) = LOWER(?) AND COALESCE(error_message, '') = '' LIMIT 1",
            (item.get("job_id"), item.get("id"), dest_root),
        ).fetchone()
        if duplicate is not None:
            return {
                "status": ITEM_FAILED,
                "copied_bytes": 0,
                "source_fingerprint": "",
                "dest_fingerprint": "",
                "error_message": (
                    "目标目录与另一数据组重复，未复制："
                    f"{duplicate['task_id'] or '未知任务'}"
                ),
                "cancelled": False,
            }
        files, notes = scan_group_files(source_root)
        if not files:
            return {
                "status": ITEM_FAILED,
                "copied_bytes": 0,
                "source_fingerprint": "",
                "dest_fingerprint": "",
                "error_message": "；".join(notes) or "源目录中没有可复制文件",
                "cancelled": False,
            }
        expected = [file.relative_path for file in files]
        expected_set = {os.path.normcase(path) for path in expected}
        conflicts: list[str] = []
        errors: list[str] = list(notes)
        copied_bytes = 0
        copied_any = False
        cancelled = False
        try:
            os.makedirs(dest_root, exist_ok=True)
        except OSError as exc:
            return {
                "status": ITEM_FAILED,
                "copied_bytes": 0,
                "source_fingerprint": "",
                "dest_fingerprint": "",
                "error_message": f"无法创建目标目录：{exc}",
                "cancelled": False,
            }

        for source_file in files:
            if should_cancel and should_cancel():
                cancelled = True
                break
            try:
                dest_file = safe_join(dest_root, *Path(source_file.relative_path).parts)
            except ValueError as exc:
                errors.append(f"{source_file.relative_path}: 目标路径无效（{exc}）")
                continue
            if os.path.exists(dest_file) or is_reparse_point(dest_file):
                if is_reparse_point(dest_file):
                    conflicts.append(source_file.relative_path)
                elif _same_file(source_file.source_path, dest_file, verify_hash):
                    continue
                else:
                    conflicts.append(source_file.relative_path)
                continue
            try:
                os.makedirs(os.path.dirname(dest_file), exist_ok=True)
            except OSError as exc:
                errors.append(f"{source_file.relative_path}: 无法创建目录（{type(exc).__name__}）")
                continue
            try:
                _copy_exclusive(source_file.source_path, dest_file)
                copied_any = True
                copied_bytes += source_file.size
            except FileExistsError:
                if not _same_file(source_file.source_path, dest_file, verify_hash):
                    conflicts.append(source_file.relative_path)
                continue
            except OSError as exc:
                errors.append(f"{source_file.relative_path}: 复制失败（{type(exc).__name__}）")
                continue
            try:
                if os.stat(dest_file).st_size != source_file.size or (
                    verify_hash and file_sha256(dest_file) != file_sha256(source_file.source_path)
                ):
                    errors.append(f"{source_file.relative_path}: 复制后大小/哈希不一致")
                    try:
                        os.remove(dest_file)
                    except OSError:
                        pass
                    copied_bytes -= source_file.size
            except OSError:
                errors.append(f"{source_file.relative_path}: 无法校验目标文件")
                continue

        if cancelled:
            return {
                "status": ITEM_PENDING,
                "copied_bytes": copied_bytes,
                "source_fingerprint": "",
                "dest_fingerprint": "",
                "error_message": "复制已暂停（源文件未改动，可继续）",
                "cancelled": True,
            }

        extras = _dest_extras(dest_root, expected_set)
        if extras:
            conflicts.extend(extras)

        source_fp = _fingerprint(source_root, expected, verify_hash)
        dest_fp = ""
        all_present = all(
            os.path.isfile(os.path.join(dest_root, relative)) for relative in expected
        )
        if all_present:
            dest_fp = _fingerprint(dest_root, expected, verify_hash)
        if all_present and source_fp != dest_fp and not conflicts:
            errors.append("复制后校验不一致：目标与源文件指纹不同")

        if errors:
            status = ITEM_FAILED
        elif conflicts:
            status = ITEM_CONFLICT
        elif all_present:
            status = ITEM_VERIFIED if copied_any else ITEM_SKIPPED
        else:
            status = ITEM_FAILED
            errors.append("目标文件不完整")
        messages = errors + [f"目标已存在且内容不同，未覆盖：{path}" for path in conflicts]
        return {
            "status": status,
            "copied_bytes": copied_bytes,
            "source_fingerprint": source_fp,
            "dest_fingerprint": dest_fp,
            "error_message": "；".join(dict.fromkeys(messages))[:2000],
            "cancelled": False,
        }

    def _save_item_outcome(self, item: dict[str, Any], outcome: dict[str, Any]) -> None:
        conn = self.db.connect()
        previous = int(item.get("copied_bytes") or 0)
        conn.execute(
            """
            UPDATE archive_items
            SET status = ?, copied_bytes = ?, source_fingerprint = ?, dest_fingerprint = ?,
                error_message = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                outcome.get("status") or ITEM_FAILED,
                previous + int(outcome.get("copied_bytes") or 0),
                outcome.get("source_fingerprint") or None,
                outcome.get("dest_fingerprint") or None,
                outcome.get("error_message") or None,
                now_iso(),
                item["id"],
            ),
        )
        conn.commit()

    def _sync_job_counts(self, job_id: str, status: str) -> None:
        conn = self.db.connect()
        row = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(status IN ('copied', 'verified')), 0) AS copied,
                   COALESCE(SUM(status = 'skipped'), 0) AS skipped,
                   COALESCE(SUM(status = 'conflict'), 0) AS conflicts,
                   COALESCE(SUM(status = 'failed'), 0) AS failed,
                   COALESCE(SUM(status = 'verified'), 0) AS verified,
                   COALESCE(SUM(file_count), 0) AS files,
                   COALESCE(SUM(bytes), 0) AS bytes,
                   COALESCE(SUM(copied_bytes), 0) AS copied_bytes
            FROM archive_items WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
        conn.execute(
            """
            UPDATE archive_jobs
            SET status = ?, total_items = ?, copied_items = ?, skipped_items = ?,
                conflict_items = ?, failed_items = ?, verified_items = ?,
                total_files = ?, bytes_total = ?, bytes_copied = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                int(row["total"] or 0),
                int(row["copied"] or 0),
                int(row["skipped"] or 0),
                int(row["conflicts"] or 0),
                int(row["failed"] or 0),
                int(row["verified"] or 0),
                int(row["files"] or 0),
                int(row["bytes"] or 0),
                int(row["copied_bytes"] or 0),
                now_iso(),
                job_id,
            ),
        )
        conn.commit()

    def _result_from_job(self, job_id: str, status: str, cancelled: bool) -> ArchiveRunResult:
        job = self.get_job(job_id) or {}
        return ArchiveRunResult(
            job_id=job_id,
            status=status,
            total_items=int(job.get("total_items") or 0),
            copied_items=int(job.get("copied_items") or 0),
            skipped_items=int(job.get("skipped_items") or 0),
            conflict_items=int(job.get("conflict_items") or 0),
            failed_items=int(job.get("failed_items") or 0),
            verified_items=int(job.get("verified_items") or 0),
            bytes_copied=int(job.get("bytes_copied") or 0),
            cancelled=cancelled,
            report_path=str(job.get("report_path") or ""),
        )

    # ── 报告 ─────────────────────────────────────────────
    def _write_report(self, job_id: str, status: str) -> str:
        """报告写入失败不应阻断任务收尾；返回空路径并保留任务状态。"""
        try:
            return self._write_report_impl(job_id, status)
        except OSError:
            return ""

    def _write_report_impl(self, job_id: str, status: str) -> str:
        job = self.get_job(job_id)
        if not job:
            return ""
        items = self.list_items(job_id)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        now = now_iso()
        data = {
            "job": job,
            "status": status,
            "status_label": JOB_STATUS_LABELS.get(status, status),
            "items": items,
            "generated_at": now,
            "source_read_only": True,
            "overwrite_existing": False,
        }
        json_path = self.reports_dir / f"archive_{job_id}.json"
        md_path = self.reports_dir / f"archive_{job_id}.md"
        json_text = json.dumps(data, ensure_ascii=False, indent=2)
        json_tmp = json_path.with_suffix(".json.tmp")
        json_tmp.write_text(json_text, encoding="utf-8")
        os.replace(json_tmp, json_path)

        lines = [
            "# 合格数据归档报告",
            "",
            f"- 任务：`{job_id}`",
            f"- 状态：{JOB_STATUS_LABELS.get(status, status)}",
            f"- 批次：{job.get('batch_display_id') or ''} {job.get('batch_name') or ''}".rstrip(),
            f"- 源根目录：`{job.get('source_root') or ''}`",
            f"- 输出根目录：`{job.get('output_root') or ''}`",
            f"- 起始层级：{job.get('start_layer') or ''}",
            f"- 命名模板：`{job.get('name_template') or ''}`",
            f"- 筛选：{job.get('filter_mode') or ''} {job.get('filter_value') or ''}".rstrip(),
            f"- 操作员：{job.get('operator') or ''}",
            f"- 生成时间：{now}",
            "",
            "## 汇总",
            "",
            "| 项目 | 数量 |",
            "|---|---:|",
            f"| 数据组 | {job.get('total_items', 0)} |",
            f"| 已复制并校验 | {job.get('verified_items', 0)} |",
            f"| 已存在跳过 | {job.get('skipped_items', 0)} |",
            f"| 目标冲突 | {job.get('conflict_items', 0)} |",
            f"| 失败 | {job.get('failed_items', 0)} |",
            f"| 文件数 | {job.get('total_files', 0)} |",
            f"| 总字节 | {job.get('bytes_total', 0)} |",
            f"| 本次复制字节 | {job.get('bytes_copied', 0)} |",
            "",
            "> 源图片/TXT/CSV 未被移动、删除或修改；目标已有文件未被覆盖。",
            "",
            "## 明细",
            "",
            "| 任务ID | 状态 | 文件 | 字节 | 目标路径 | 说明 |",
            "|---|---|---:|---:|---|---|",
        ]
        for item in items:
            note = (item.get("error_message") or "").replace("|", "\\|").replace("\n", " ")
            target = (item.get("dest_path") or "").replace("|", "\\|")
            lines.append(
                f"| {item.get('task_id') or ''} | "
                f"{ITEM_STATUS_LABELS.get(item.get('status') or '', item.get('status') or '')} | "
                f"{item.get('file_count', 0)} | {item.get('bytes', 0)} | `{target}` | {note} |"
            )
        md_text = "\n".join(lines).rstrip() + "\n"
        md_tmp = md_path.with_suffix(".md.tmp")
        md_tmp.write_text(md_text, encoding="utf-8")
        os.replace(md_tmp, md_path)
        return str(md_path)

    def report_text(self, job_id: str) -> str:
        job = self.get_job(job_id)
        if not job or not job.get("report_path"):
            return ""
        try:
            return Path(job["report_path"]).read_text(encoding="utf-8")
        except OSError:
            return ""
