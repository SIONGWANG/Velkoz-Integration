"""业务项目根目录配置（v1.25.8）。

配置只写管理数据库 `project_roots`；目录不存在时只报告错误，
不会创建、删除或修改任何源目录。
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from modules.common.path_guard import normalize_path
from modules.common.timeutil import now_iso
from modules.config import app_config
from modules.database.connection import Database, open_database

from .models import (
    DEFAULT_FINAL_DIR,
    DEFAULT_PENDING_DIR,
    FORBIDDEN_SCAN_DIR_NAMES,
    PathDetection,
    ProjectDetection,
    ProjectRoot,
)


def _new_id(prefix: str = "proj") -> str:
    return f"{prefix}_{uuid.uuid4()}"


def _valid_path_text(path: str) -> bool:
    text = (path or "").strip()
    return text.startswith("\\\\") or os.path.isabs(text)


class ProjectRootService:
    def __init__(self, db_path: str | Path | None = None):
        self.db: Database = open_database(db_path)

    # ── 检测 ─────────────────────────────────────────────
    @staticmethod
    def detect_path(path: str, *, label: str = "目录") -> PathDetection:
        text = (path or "").strip()
        detection = PathDetection(path=text)
        if not text:
            detection.error = f"未配置{label}"
            return detection
        if not _valid_path_text(text):
            detection.error = f"{label}必须是绝对路径或局域网共享路径：{text}"
            return detection
        normalized = normalize_path(text)
        detection.path = normalized
        base = os.path.basename(os.path.normpath(normalized))
        if base in FORBIDDEN_SCAN_DIR_NAMES:
            detection.error = (
                f"{label}不能是“{base}”。本版本只接入 02-待验数据 与 03-申请终验，"
                "不扫描试做情况或素材仓库。"
            )
            return detection
        detection.is_business_subdir = base in {DEFAULT_PENDING_DIR, DEFAULT_FINAL_DIR}
        if not os.path.exists(normalized):
            detection.error = (
                f"{label}不存在或无法访问：{normalized}。"
                "请检查网络共享是否在线、路径是否正确、账号是否有权限。"
            )
            return detection
        detection.exists = True
        if not os.path.isdir(normalized):
            detection.error = f"{label}不是目录：{normalized}"
            return detection
        detection.is_dir = True
        try:
            with os.scandir(normalized) as iterator:
                next(iterator, None)
            detection.readable = True
        except OSError as exc:
            detection.error = f"{label}无法读取：{normalized}（{exc}）。请检查共享权限或网络状态。"
        return detection

    @classmethod
    def detect_parent(cls, parent_path: str) -> ProjectDetection:
        text = (parent_path or "").strip()
        result = ProjectDetection(parent_path=text)
        if not text:
            result.errors.append("请选择或填写项目根目录")
            return result
        parent = normalize_path(text)
        result.parent_path = parent
        base = os.path.basename(os.path.normpath(parent))
        if base in FORBIDDEN_SCAN_DIR_NAMES:
            result.errors.append(f"项目根目录不能是“{base}”。")
            return result
        if not os.path.isdir(parent):
            result.errors.append(
                f"项目根目录不存在或无法访问：{parent}。请检查网络共享、路径和权限。"
            )
            return result
        try:
            children = {
                entry.name for entry in os.scandir(parent) if entry.is_dir(follow_symlinks=False)
            }
        except OSError as exc:
            result.errors.append(f"项目根目录无法读取：{parent}（{exc}）")
            return result
        found: list[str] = []
        for name, default in ((DEFAULT_PENDING_DIR, "pending"), (DEFAULT_FINAL_DIR, "final")):
            path = os.path.join(parent, name)
            detection = cls.detect_path(path, label=SOURCE_LABEL_FOR(default))
            if name in children:
                found.append(name)
            if default == "pending":
                result.pending = detection
            else:
                result.final = detection
        if not found:
            result.errors.append(
                f"未在项目根目录下找到 {DEFAULT_PENDING_DIR} 或 {DEFAULT_FINAL_DIR}。"
                "请选择正确项目根目录，或在下方分别填写两个业务目录。"
            )
        for name in found:
            if name == DEFAULT_PENDING_DIR and result.pending and not result.pending.ok:
                result.warnings.append(result.pending.error)
            if name == DEFAULT_FINAL_DIR and result.final and not result.final.ok:
                result.warnings.append(result.final.error)
        if DEFAULT_PENDING_DIR not in children and result.pending:
            result.warnings.append(f"未发现 {DEFAULT_PENDING_DIR}；如该项目暂无待验数据可留空。")
        if DEFAULT_FINAL_DIR not in children and result.final:
            result.warnings.append(f"未发现 {DEFAULT_FINAL_DIR}；如该项目暂无申请终验可留空。")
        return result

    @staticmethod
    def validate_paths(pending_path: str, final_path: str) -> list[str]:
        errors: list[str] = []
        for path, label in ((pending_path, "待验数据目录"), (final_path, "申请终验目录")):
            text = (path or "").strip()
            if not text:
                continue
            if not _valid_path_text(text):
                errors.append(f"{label}必须是绝对路径或局域网共享路径：{text}")
                continue
            base = os.path.basename(os.path.normpath(normalize_path(text)))
            if base in FORBIDDEN_SCAN_DIR_NAMES:
                errors.append(
                    f"{label}不能是“{base}”。本版本不扫描试做情况或素材仓库。"
                )
            elif base not in {DEFAULT_PENDING_DIR, DEFAULT_FINAL_DIR}:
                # 允许自定义名称，但项目父目录（包含 02/03 子目录）不行
                try:
                    normalized = normalize_path(text)
                    if os.path.isdir(normalized):
                        children = {
                            entry.name
                            for entry in os.scandir(normalized)
                            if entry.is_dir(follow_symlinks=False)
                        }
                        if DEFAULT_PENDING_DIR in children or DEFAULT_FINAL_DIR in children:
                            errors.append(
                                f"{label}看起来是项目父目录，请选择其中的 "
                                f"{DEFAULT_PENDING_DIR} 或 {DEFAULT_FINAL_DIR}。"
                            )
                except OSError:
                    pass
        if not (pending_path or "").strip() and not (final_path or "").strip():
            errors.append("待验数据目录和申请终验目录至少配置一个。")
        return errors

    # ── CRUD ─────────────────────────────────────────────
    def list(self, *, include_disabled: bool = True) -> list[ProjectRoot]:
        sql = "SELECT * FROM project_roots"
        if not include_disabled:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY enabled DESC, project_name COLLATE NOCASE, created_at"
        rows = self.db.connect().execute(sql).fetchall()
        return [ProjectRoot.from_row(row) for row in rows]

    def get(self, project_id: str) -> ProjectRoot | None:
        row = self.db.connect().execute(
            "SELECT * FROM project_roots WHERE id = ?", (project_id,)
        ).fetchone()
        return ProjectRoot.from_row(row) if row else None

    def create(
        self,
        project_name: str,
        *,
        parent_path: str = "",
        pending_path: str = "",
        final_path: str = "",
        enabled: bool = True,
        note: str = "",
    ) -> ProjectRoot:
        name = (project_name or "").strip()
        if not name:
            raise ValueError("请填写项目名称")
        parent = normalize_path(parent_path) if (parent_path or "").strip() else ""
        pending = normalize_path(pending_path) if (pending_path or "").strip() else ""
        final = normalize_path(final_path) if (final_path or "").strip() else ""
        if parent and not pending:
            pending = os.path.join(parent, DEFAULT_PENDING_DIR)
        if parent and not final:
            final = os.path.join(parent, DEFAULT_FINAL_DIR)
        errors = self.validate_paths(pending, final)
        if errors:
            raise ValueError("；".join(errors))
        now = now_iso()
        project_id = _new_id()
        conn = self.db.connect()
        conn.execute(
            """
            INSERT INTO project_roots(
                id, project_name, parent_path, pending_path, final_path, enabled,
                note, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (project_id, name, parent, pending, final, 1 if enabled else 0, note, now, now),
        )
        conn.commit()
        return self.get(project_id)  # type: ignore[return-value]

    def update(
        self,
        project_id: str,
        *,
        project_name: str | None = None,
        parent_path: str | None = None,
        pending_path: str | None = None,
        final_path: str | None = None,
        enabled: bool | None = None,
        note: str | None = None,
    ) -> ProjectRoot:
        current = self.get(project_id)
        if current is None:
            raise ValueError("项目目录配置不存在")
        name = current.project_name if project_name is None else project_name.strip()
        if not name:
            raise ValueError("请填写项目名称")
        parent = current.parent_path if parent_path is None else (
            normalize_path(parent_path) if parent_path.strip() else ""
        )
        pending = current.pending_path if pending_path is None else (
            normalize_path(pending_path) if pending_path.strip() else ""
        )
        final = current.final_path if final_path is None else (
            normalize_path(final_path) if final_path.strip() else ""
        )
        if parent_path is not None and parent and not pending:
            pending = os.path.join(parent, DEFAULT_PENDING_DIR)
        if parent_path is not None and parent and not final:
            final = os.path.join(parent, DEFAULT_FINAL_DIR)
        errors = self.validate_paths(pending, final)
        if errors:
            raise ValueError("；".join(errors))
        enabled_value = current.enabled if enabled is None else bool(enabled)
        note_value = current.note if note is None else note
        conn = self.db.connect()
        conn.execute(
            """
            UPDATE project_roots
            SET project_name = ?, parent_path = ?, pending_path = ?, final_path = ?,
                enabled = ?, note = ?, updated_at = ?
            WHERE id = ?
            """,
            (name, parent, pending, final, 1 if enabled_value else 0, note_value, now_iso(), project_id),
        )
        conn.commit()
        return self.get(project_id)  # type: ignore[return-value]

    def set_enabled(self, project_id: str, enabled: bool) -> None:
        conn = self.db.connect()
        conn.execute(
            "UPDATE project_roots SET enabled = ?, updated_at = ? WHERE id = ?",
            (1 if enabled else 0, now_iso(), project_id),
        )
        conn.commit()

    def delete(self, project_id: str) -> None:
        """只删除配置；历史批次、检查、翻译与验收记录全部保留。"""
        conn = self.db.connect()
        conn.execute("DELETE FROM project_roots WHERE id = ?", (project_id,))
        conn.commit()

    def mark_scanned(self, project_id: str, source_type: str) -> None:
        now = now_iso()
        column = {
            "pending_acceptance": "pending_scan_at",
            "final_acceptance": "final_scan_at",
        }.get(source_type)
        conn = self.db.connect()
        if column:
            conn.execute(
                f"UPDATE project_roots SET last_scan_at = ?, {column} = ?, updated_at = ? WHERE id = ?",
                (now, now, now, project_id),
            )
        else:
            conn.execute(
                "UPDATE project_roots SET last_scan_at = ?, updated_at = ? WHERE id = ?",
                (now, now, project_id),
            )
        conn.commit()

    def close(self) -> None:
        self.db.close()


def SOURCE_LABEL_FOR(kind: str) -> str:
    return "待验数据目录" if kind == "pending" else "申请终验目录"
