"""合格数据导出到“库目录”（v1.25.10）。

规则：
1. 只复制：不移动、不删除、不修改源图片/TXT/CSV。
2. 目标结构 = 库根目录 + 批次相对业务根目录的层级，例如
   ``库根/产品背景替换/醒醒文化提交/1107/批次A``。
3. 已有层级目录直接使用；不存在则创建。
4. 同名数据组默认不覆盖，避免误删库中已有数据；确需刷新时显式覆盖。
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from typing import Any, Callable

from modules.common.path_guard import is_relative_to, normalize_path
from modules.database.connection import Database
from modules.database.repositories.precheck_repo import get_setting, set_setting

LIBRARY_ROOT_KEY = "library_root"

QUALIFIED_RESULTS = ("合格", "修改后合格")
IGNORED_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}

ProgressCallback = Callable[[int, int, str], None]


# ── 配置 ─────────────────────────────────────────────────


def get_library_root(db: Database) -> str:
    """读取库根目录；未配置返回空字符串。"""
    try:
        return (get_setting(db, LIBRARY_ROOT_KEY, "") or "").strip()
    except Exception:
        return ""


def save_library_root(db: Database, path: str) -> str:
    """保存库根目录；返回规范化后的路径。"""
    value = normalize_path(path) if path and path.strip() else ""
    set_setting(db, LIBRARY_ROOT_KEY, value)
    return value


def _source_roots_from_db(db: Database) -> list[str]:
    try:
        rows = db.connect().execute(
            "SELECT pending_path, final_path FROM project_roots"
        ).fetchall()
    except Exception:
        return []
    return [
        str(value)
        for row in rows
        for value in (row["pending_path"], row["final_path"])
        if value
    ]


def validate_library_root(
    path: str,
    *,
    source_roots: tuple[str, ...] | list[str] = (),
    db: Database | None = None,
) -> tuple[bool, str]:
    """校验库根目录：不能为空、不能是文件、不能落在源业务目录内。"""
    if not path or not str(path).strip():
        return False, "未填写库根目录"
    try:
        target = normalize_path(path)
    except (OSError, ValueError) as exc:
        return False, f"路径无效：{exc}"
    if os.path.exists(target) and not os.path.isdir(target):
        return False, "库根目录已存在且不是文件夹"
    roots = [*source_roots]
    if db is not None:
        roots.extend(_source_roots_from_db(db))
    for source in roots:
        if source and is_relative_to(target, source):
            return False, f"库根目录不能放在源数据目录内：{source}"
    return True, target


# ── 目录结构 ─────────────────────────────────────────────


def build_library_target(
    library_root: str,
    *,
    batch_path: str,
    source_root_path: str = "",
    fallback_name: str = "",
) -> str:
    """计算批次在库根目录下的目标文件夹。

    优先按“批次目录相对业务根目录”的层级镜像；找不到业务根目录时，
    退化为 ``库根/批次文件夹名``。只做路径计算，不创建目录。
    """
    if not library_root or not str(library_root).strip():
        raise ValueError("未配置库根目录")
    if not batch_path or not str(batch_path).strip():
        raise ValueError("批次目录为空")

    root = normalize_path(library_root)
    batch = normalize_path(batch_path)
    candidates = [source_root_path, os.path.dirname(batch)]
    for base in candidates:
        if not base or not str(base).strip():
            continue
        base_n = normalize_path(base)
        if not is_relative_to(batch, base_n):
            continue
        rel = os.path.relpath(batch, base_n)
        if rel in ("", ".", "..") or rel.startswith(".."):
            continue
        return os.path.join(root, rel)
    return os.path.join(root, fallback_name or os.path.basename(batch))


def resolve_source_root(db: Database, batch_path: str) -> str:
    """从项目根目录配置中找出批次所属的业务根目录（最长匹配）。"""
    if not batch_path:
        return ""
    try:
        rows = db.connect().execute(
            "SELECT pending_path, final_path FROM project_roots"
        ).fetchall()
    except Exception:
        return ""
    matches: list[str] = []
    for row in rows:
        for value in (row["pending_path"], row["final_path"]):
            if value and is_relative_to(batch_path, value):
                matches.append(str(value))
    if not matches:
        return ""
    return max(matches, key=len)


# ── 合格数据 ─────────────────────────────────────────────


def _latest_results(db: Database, batch_id: str) -> dict[str, dict[str, Any]]:
    rows = db.connect().execute(
        """
        SELECT g.id AS data_group_id, g.task_id, g.normalized_path, g.user_name,
               g.image_count, g.file_count, ir.result, ir.record_time
        FROM data_groups g
        JOIN imported_records ir ON ir.data_group_id = g.id
        WHERE g.batch_id = ?
          AND ir.id = (
              SELECT ir2.id FROM imported_records ir2
              WHERE ir2.data_group_id = g.id
              ORDER BY COALESCE(ir2.record_time, ir2.created_at) DESC, ir2.id DESC
              LIMIT 1
          )
        """,
        (batch_id,),
    ).fetchall()
    return {row["data_group_id"]: dict(row) for row in rows}


def qualified_groups(db: Database, batch_id: str) -> list[dict[str, Any]]:
    """按批次取“合格/修改后合格”的数据组（每组只取最新一条记录）。"""
    latest = _latest_results(db, batch_id)
    result = [
        item
        for item in latest.values()
        if str(item.get("result") or "") in QUALIFIED_RESULTS
    ]
    result.sort(key=lambda item: (item.get("task_id") or "", item.get("normalized_path") or ""))
    return result


def group_file_names(path: str) -> set[str]:
    """忽略系统垃圾文件后的直接文件/文件夹名集合（小写）。"""
    try:
        return {name.lower() for name in os.listdir(path)} - IGNORED_NAMES
    except OSError:
        return set()


@dataclass
class LibraryExportResult:
    target_dir: str = ""
    total: int = 0
    copied: int = 0
    skipped: int = 0
    missing: int = 0
    conflicts: int = 0
    errors: list[str] = field(default_factory=list)
    files: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors and self.conflicts == 0

    def message(self) -> str:
        parts = [f"已导出 {self.copied} 组"]
        if self.skipped:
            parts.append(f"已存在跳过 {self.skipped} 组")
        if self.conflicts:
            parts.append(f"内容不同未覆盖 {self.conflicts} 组")
        if self.missing:
            parts.append(f"源目录不存在 {self.missing} 组")
        if self.errors:
            parts.append(f"失败 {len(self.errors)} 组")
        text = "，".join(parts)
        return f"{text}。\n目标：{self.target_dir}"


def export_qualified_groups(
    groups: list[dict[str, Any]],
    target_dir: str,
    *,
    overwrite: bool = False,
    progress: ProgressCallback | None = None,
) -> LibraryExportResult:
    """把合格数据组复制到目标目录；默认不覆盖已有同名数据组。"""
    result = LibraryExportResult(target_dir=target_dir, total=len(groups))
    if not target_dir:
        result.errors.append("目标目录为空")
        return result
    try:
        os.makedirs(target_dir, exist_ok=True)
    except OSError as exc:
        result.errors.append(f"创建目标目录失败：{exc}")
        return result

    total = len(groups)
    for index, item in enumerate(groups):
        task_id = str(item.get("task_id") or item.get("data_group_id") or "")
        src = str(item.get("normalized_path") or "")
        if progress is not None:
            progress(index, total, f"正在导出：{task_id}")
        if not src or not os.path.isdir(src):
            result.missing += 1
            continue

        dst = os.path.join(target_dir, task_id or os.path.basename(src))
        src_names = group_file_names(src)
        if os.path.exists(dst):
            if overwrite:
                try:
                    shutil.rmtree(dst)
                except OSError as exc:
                    result.errors.append(f"{task_id} 覆盖失败：{exc}")
                    continue
            elif group_file_names(dst) == src_names:
                result.skipped += 1
                continue
            else:
                result.conflicts += 1
                continue

        try:
            shutil.copytree(
                src,
                dst,
                ignore=shutil.ignore_patterns("Thumbs.db", "desktop.ini", ".DS_Store"),
            )
        except OSError as exc:
            result.errors.append(f"{task_id} 复制失败：{exc}")
            continue

        if group_file_names(dst) != src_names:
            result.errors.append(f"{task_id} 文件校验不一致")
            continue
        result.copied += 1
        result.files += len(src_names)

    if progress is not None:
        progress(total, total, "导出完成")
    return result
