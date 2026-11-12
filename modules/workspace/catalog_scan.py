"""验收记录扫描（v1.25.11 P3）。

只读 `_质检记录` 下的 CSV/XLSX，把结果写入管理数据库；
不修改任何源图片、TXT、CSV。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Iterable

from modules.common.timeutil import now_iso
from modules.database.connection import Database
from modules.record_import.parser import parse_rows, read_table
from modules.workspace.catalog_service import (
    MAIN_COMPLETED,
    MAIN_NEW,
    QUEUE_DONE,
    CatalogService,
)

RECORD_DIR_NAME = "_质检记录"
QUALIFIED_RESULTS = {"合格", "修改后合格", "通过"}
REJECTED_RESULTS = {"不合格", "作废", "不通过"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


@dataclass
class FolderPreview:
    name: str
    path: str
    reviewers: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "reviewers": list(self.reviewers),
            "files": list(self.files),
            "warnings": list(self.warnings),
        }


@dataclass
class BatchScanPreview:
    batch_id: str
    batch_name: str
    batch_path: str
    folders: list[FolderPreview] = field(default_factory=list)
    reviewers: list[str] = field(default_factory=list)
    conflict: bool = False
    record_count: int = 0

    @property
    def file_count(self) -> int:
        return sum(len(folder.files) for folder in self.folders)

    @property
    def has_files(self) -> bool:
        return self.file_count > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "batch_name": self.batch_name,
            "batch_path": self.batch_path,
            "folders": [folder.to_dict() for folder in self.folders],
            "reviewers": list(self.reviewers),
            "conflict": self.conflict,
            "record_count": self.record_count,
            "file_count": self.file_count,
            "has_files": self.has_files,
        }


@dataclass
class ScanResult:
    batch_id: str
    batch_name: str = ""
    reviewers: list[str] = field(default_factory=list)
    applied: bool = False
    inserted: int = 0
    qualified: int = 0
    rejected: int = 0
    unknown: int = 0
    covered: int = 0
    remaining: int = 0
    message: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "batch_name": self.batch_name,
            "reviewers": list(self.reviewers),
            "applied": self.applied,
            "inserted": self.inserted,
            "qualified": self.qualified,
            "rejected": self.rejected,
            "unknown": self.unknown,
            "covered": self.covered,
            "remaining": self.remaining,
            "message": self.message,
            "warnings": list(self.warnings),
        }


def _normalize_number(value: str) -> str:
    text = (value or "").strip()
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def _result_kind(value: str) -> tuple[str, int]:
    text = (value or "").strip()
    if text in QUALIFIED_RESULTS:
        return "qualified", 1
    if text in REJECTED_RESULTS:
        return "rejected", 0
    return "unknown", 0


def _resolve_evidence(value: str, batch_path: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if os.path.isabs(text):
        return os.path.normpath(text)
    return os.path.normpath(os.path.join(batch_path, text))


def _candidate_files(folder_path: str) -> tuple[list[str], list[str]]:
    """CSV 优先；没有 CSV 才用 XLSX；忽略 .bak 与备份文件。"""
    csvs: list[str] = []
    xlsx: list[str] = []
    warnings: list[str] = []
    try:
        entries = sorted(os.scandir(folder_path), key=lambda item: item.name)
    except OSError as exc:
        return [], [f"无法读取验收员文件夹：{exc}"]
    for entry in entries:
        if not entry.is_file(follow_symlinks=False):
            continue
        name = entry.name
        lower = name.casefold()
        if lower.endswith(".bak") or name.startswith(".") or name.startswith("~$"):
            continue
        if "备份" in name or "backup" in lower:
            continue
        if lower.endswith(".csv"):
            csvs.append(entry.path)
        elif lower.endswith(".xlsx"):
            xlsx.append(entry.path)
    if csvs:
        if xlsx:
            warnings.append("同时存在 CSV 和 XLSX，按 CSV 为准，忽略 XLSX")
        return csvs, warnings
    return xlsx, warnings


def _reviewer_lookup(service: CatalogService) -> tuple[dict[str, str], dict[str, str]]:
    rows = service.reviewers()
    ids: dict[str, str] = {}
    canonical: dict[str, str] = {}
    for row in rows:
        if not row.get("enabled"):
            continue
        name = str(row["name"])
        ids[name] = str(row["id"])
        canonical[name] = name
        raw = str(row.get("aliases") or "").replace("，", ",").replace("；", ",").replace(";", ",")
        for alias in raw.split(","):
            alias = alias.strip()
            if alias:
                ids.setdefault(alias, str(row["id"]))
                canonical[alias] = name
    return ids, canonical


def _match_reviewers(folder_name: str, canonical: dict[str, str]) -> list[str]:
    matched: list[str] = []
    for token in sorted(canonical, key=len, reverse=True):
        if token and token in folder_name:
            name = canonical[token]
            if name not in matched:
                matched.append(name)
    return matched


def preview_scan(db: Database, batch_id: str) -> BatchScanPreview:
    service = CatalogService(db)
    batch = service.batch(batch_id)
    if not batch:
        raise ValueError("批次不存在")
    _, canonical = _reviewer_lookup(service)
    batch_path = str(batch.get("batch_path") or "")
    record_dir = os.path.join(batch_path, RECORD_DIR_NAME)
    preview = BatchScanPreview(
        batch_id=batch_id,
        batch_name=str(batch.get("name") or ""),
        batch_path=batch_path,
        record_count=service.record_count(batch_id),
    )
    if not os.path.isdir(record_dir):
        return preview
    try:
        entries = sorted(
            (entry for entry in os.scandir(record_dir) if entry.is_dir(follow_symlinks=False)),
            key=lambda item: item.name,
        )
    except OSError:
        entries = []
    for entry in entries:
        folders = _match_reviewers(entry.name, canonical)
        files, warnings = _candidate_files(entry.path)
        folder = FolderPreview(
            name=entry.name,
            path=entry.path,
            reviewers=folders,
            files=files,
            warnings=warnings,
        )
        preview.folders.append(folder)
        for name in folders:
            if name not in preview.reviewers:
                preview.reviewers.append(name)
    preview.reviewers.sort()
    preview.conflict = len(preview.reviewers) > 1
    return preview


def _parse_folder(
    folder: FolderPreview,
    *,
    batch_path: str,
    task_ids: dict[str, str],
    records: dict[str, dict[str, Any]],
    warnings: list[str],
) -> None:
    for path in folder.files:
        try:
            headers, rows, _, _ = read_table(path)
            parsed, errors = parse_rows(path, headers, rows)
        except Exception as exc:
            warnings.append(f"{os.path.basename(path)}：{type(exc).__name__} {exc}")
            continue
        if errors:
            warnings.extend(f"{os.path.basename(path)}：{item}" for item in errors[:5])
        reviewer_name = folder.reviewers[0] if folder.reviewers else ""
        for row in parsed:
            number = _normalize_number(row.task_id)
            if not number:
                warnings.append(f"{os.path.basename(path)} 第 {row.source_row} 行没有编号，已跳过")
                continue
            kind, qualified = _result_kind(row.result)
            if number in records:
                warnings.append(f"编号 {number} 重复，以最后一条为准")
            records[number] = {
                "number": number,
                "group_id": task_ids.get(number, ""),
                "result": row.result.strip(),
                "kind": kind,
                "qualified": qualified,
                "reason": row.note.strip(),
                "tags": row.tags_raw.strip(),
                "evidence": _resolve_evidence(row.evidence_raw, batch_path),
                "record_time": row.record_time.strip(),
                "reviewer_name": reviewer_name,
                "source_file": path,
                "source_row": int(row.source_row),
                "fingerprint": row.fingerprint,
            }


def _count_group_files(path: str) -> tuple[int, int, int]:
    images = txt = files = 0
    try:
        entries = os.scandir(path)
    except OSError:
        return 0, 0, 0
    with entries:
        for entry in entries:
            if not entry.is_file(follow_symlinks=False):
                continue
            files += 1
            ext = os.path.splitext(entry.name)[1].casefold()
            if ext in IMAGE_EXTS:
                images += 1
            elif ext == ".txt":
                txt += 1
    return images, txt, files


def apply_scan(db: Database, batch_id: str, reviewers: Iterable[str]) -> ScanResult:
    service = CatalogService(db)
    batch = service.batch(batch_id)
    if not batch:
        raise ValueError("批次不存在")
    preview = preview_scan(db, batch_id)
    selected = [str(name) for name in reviewers if str(name).strip()]
    selected_set = set(selected)
    result = ScanResult(
        batch_id=batch_id,
        batch_name=preview.batch_name,
        reviewers=selected,
    )
    if not selected:
        result.message = "未绑定验收员，主状态保持新提交"
        result.warnings = ["已选择“都不绑定”，没有写入验收记录"]
        service.queue_set_status(batch_id, "scan", "skipped", result.message)
        return result

    folders = [
        folder
        for folder in preview.folders
        if set(folder.reviewers) & selected_set
    ]
    if not folders:
        result.message = "没有找到要扫描的验收员文件夹"
        result.warnings = ["验收员文件夹可能已变化，请刷新目录索引"]
        service.queue_set_status(batch_id, "scan", "skipped", result.message)
        return result

    task_ids = {
        str(row["task_id"]): str(row["id"])
        for row in db.connect().execute(
            "SELECT id, task_id FROM catalog_groups WHERE batch_id = ? AND is_active = 1",
            (batch_id,),
        )
    }
    records: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for folder in folders:
        warnings.extend(folder.warnings)
        _parse_folder(
            folder,
            batch_path=preview.batch_path,
            task_ids=task_ids,
            records=records,
            warnings=warnings,
        )
    if not records:
        result.message = "验收记录文件没有可导入的编号，主状态保持新提交"
        result.warnings = warnings[:50] or ["没有找到可导入的 CSV/XLSX 记录"]
        service.queue_set_status(batch_id, "scan", "skipped", result.message)
        return result

    qualified = sum(1 for item in records.values() if item["kind"] == "qualified")
    rejected = sum(1 for item in records.values() if item["kind"] == "rejected")
    unknown = len(records) - qualified - rejected
    covered = sum(1 for item in records.values() if item["group_id"])
    remaining = max(0, len(task_ids) - covered)
    reviewer_name = "、".join(selected)
    reviewer_ids = {
        str(row["name"]): str(row["id"])
        for row in service.reviewers()
        if row.get("enabled")
    }
    reviewer_id = reviewer_ids.get(selected[0], "") if len(selected) == 1 else ""

    conn = db.connect()
    old_status = str(batch.get("main_status") or MAIN_NEW)
    conn.execute("DELETE FROM catalog_records WHERE batch_id = ?", (batch_id,))
    now = now_iso()
    for item in records.values():
        conn.execute(
            """
            INSERT INTO catalog_records(
                id, batch_id, group_id, number, result, reason, tags, evidence,
                record_time, reviewer_name, source_file, created_at,
                qualified, source_row, fingerprint
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"crec_{os.urandom(12).hex()}",
                batch_id,
                item["group_id"],
                item["number"],
                item["result"],
                item["reason"],
                item["tags"],
                item["evidence"],
                item["record_time"],
                item["reviewer_name"],
                item["source_file"],
                now,
                int(item["qualified"]),
                int(item["source_row"]),
                item["fingerprint"],
            ),
        )
    conn.execute(
        "UPDATE catalog_groups SET status = 'pending', updated_at = ? WHERE batch_id = ?",
        (now, batch_id),
    )
    for item in records.values():
        if not item["group_id"]:
            continue
        conn.execute(
            "UPDATE catalog_groups SET status = ?, updated_at = ? WHERE id = ?",
            (item["kind"], now, item["group_id"]),
        )
    for group in service.groups(batch_id):
        images, txt, files = _count_group_files(str(group.get("path") or ""))
        conn.execute(
            "UPDATE catalog_groups SET image_count = ?, txt_count = ?, file_count = ?, updated_at = ?"
            " WHERE id = ?",
            (images, txt, files, now, group["id"]),
        )
    conn.execute(
        """
        UPDATE catalog_batches
        SET reviewer_id = ?, reviewer_name = ?, qualified_count = ?, rejected_count = ?,
            remaining_count = ?, main_status = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            reviewer_id,
            reviewer_name,
            qualified,
            rejected,
            remaining,
            MAIN_COMPLETED,
            now,
            batch_id,
        ),
    )
    conn.execute(
        """
        INSERT INTO catalog_status_log(
            id, batch_id, kind, from_status, to_status, operator, reason, created_at
        ) VALUES (?, ?, 'main', ?, ?, ?, ?, ?)
        """,
        (
            f"clog_{os.urandom(12).hex()}",
            batch_id,
            old_status,
            MAIN_COMPLETED,
            reviewer_name,
            f"扫描验收记录：合格 {qualified}，不合格 {rejected}",
            now,
        ),
    )
    conn.commit()
    service.queue_set_status(
        batch_id,
        "scan",
        QUEUE_DONE,
        f"扫描完成：合格 {qualified}，不合格 {rejected}",
    )

    result.applied = True
    result.inserted = len(records)
    result.qualified = qualified
    result.rejected = rejected
    result.unknown = unknown
    result.covered = covered
    result.remaining = remaining
    result.message = (
        f"扫描完成：导入 {len(records)} 条，合格 {qualified}，不合格 {rejected}，"
        f"未识别 {unknown}，未覆盖编号 {remaining}"
    )
    result.warnings = warnings[:50]
    return result
