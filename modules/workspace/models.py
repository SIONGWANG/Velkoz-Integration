"""业务目录与批次工作台模型（v1.25.8）。

只读源目录；配置与批次索引写入管理数据库。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SOURCE_PENDING = "pending_acceptance"
SOURCE_FINAL = "final_acceptance"
SOURCE_LEGACY = "legacy"
SOURCE_MANUAL = "manual"

SOURCE_LABELS = {
    SOURCE_PENDING: "待验数据",
    SOURCE_FINAL: "申请终验",
    SOURCE_LEGACY: "历史批次",
    SOURCE_MANUAL: "手动扫描",
}

BUSINESS_SOURCES = (SOURCE_PENDING, SOURCE_FINAL)

DEFAULT_PENDING_DIR = "02-待验数据"
DEFAULT_FINAL_DIR = "03-申请终验"

# 只跳过系统垃圾/程序临时目录；不按“返修/合格数据”等业务词盲目排除。
JUNK_DIR_NAMES = (
    "Thumbs.db",
    ".DS_Store",
    "._*",
    "__MACOSX",
    "_00_Evidence_*",
    "_00_Backup_*",
    "_质检记录",
    "~$*",
    "$RECYCLE.BIN",
    "System Volume Information",
    "__pycache__",
    ".git",
    "node_modules",
)

FORBIDDEN_SCAN_DIR_NAMES = {"00-试做情况", "01-素材仓库"}

FORMAT_STATE_NOT_CHECKED = "not_checked"
FORMAT_STATE_RUNNING = "running"
FORMAT_STATE_PASSED = "passed"
FORMAT_STATE_WARNING = "warning"
FORMAT_STATE_FAILED = "failed"
FORMAT_STATE_PARTIAL = "partial"

FORMAT_STATE_LABELS = {
    FORMAT_STATE_NOT_CHECKED: "未检查",
    FORMAT_STATE_RUNNING: "检查中",
    FORMAT_STATE_PASSED: "通过",
    FORMAT_STATE_WARNING: "有警告",
    FORMAT_STATE_FAILED: "检查失败",
    FORMAT_STATE_PARTIAL: "部分完成",
}

TRANSLATION_STATE_NOT_SCANNED = "not_scanned"
TRANSLATION_STATE_PENDING = "pending"
TRANSLATION_STATE_RUNNING = "running"
TRANSLATION_STATE_COMPLETED = "completed"
TRANSLATION_STATE_PARTIAL = "partial"
TRANSLATION_STATE_FAILED = "failed"
TRANSLATION_STATE_MISSING_ZH = "missing_zh"

TRANSLATION_STATE_LABELS = {
    TRANSLATION_STATE_NOT_SCANNED: "未扫描",
    TRANSLATION_STATE_PENDING: "待翻译",
    TRANSLATION_STATE_RUNNING: "翻译中",
    TRANSLATION_STATE_COMPLETED: "全部完成",
    TRANSLATION_STATE_PARTIAL: "部分完成",
    TRANSLATION_STATE_FAILED: "存在失败",
    TRANSLATION_STATE_MISSING_ZH: "缺少中文 TXT",
}

ACCEPTANCE_STATE_PENDING = "pending"
ACCEPTANCE_STATE_IN_PROGRESS = "in_progress"
ACCEPTANCE_STATE_COMPLETED = "completed"
ACCEPTANCE_STATE_PAUSED = "paused"
ACCEPTANCE_STATE_NO_RECORDS = "no_records"

ACCEPTANCE_STATE_LABELS = {
    ACCEPTANCE_STATE_PENDING: "待验收",
    ACCEPTANCE_STATE_IN_PROGRESS: "进行中",
    ACCEPTANCE_STATE_COMPLETED: "已完成",
    ACCEPTANCE_STATE_PAUSED: "已暂停",
    ACCEPTANCE_STATE_NO_RECORDS: "暂无已导入验收记录",
}

FINAL_STATE_FOUND = "found"
FINAL_STATE_PENDING = "pending"
FINAL_STATE_IN_PROGRESS = "in_progress"
FINAL_STATE_COMPLETED = "completed"

FINAL_STATE_LABELS = {
    FINAL_STATE_FOUND: "已发现申请",
    FINAL_STATE_PENDING: "待终验",
    FINAL_STATE_IN_PROGRESS: "终验中",
    FINAL_STATE_COMPLETED: "终验完成",
}


@dataclass
class ProjectRoot:
    id: str = ""
    project_name: str = ""
    parent_path: str = ""
    pending_path: str = ""
    final_path: str = ""
    enabled: bool = True
    note: str = ""
    last_scan_at: str = ""
    pending_scan_at: str = ""
    final_scan_at: str = ""
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: Any) -> "ProjectRoot":
        data = dict(row)
        return cls(
            id=str(data.get("id") or ""),
            project_name=str(data.get("project_name") or ""),
            parent_path=str(data.get("parent_path") or ""),
            pending_path=str(data.get("pending_path") or ""),
            final_path=str(data.get("final_path") or ""),
            enabled=bool(data.get("enabled")),
            note=str(data.get("note") or ""),
            last_scan_at=str(data.get("last_scan_at") or ""),
            pending_scan_at=str(data.get("pending_scan_at") or ""),
            final_scan_at=str(data.get("final_scan_at") or ""),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )

    def path_for(self, source_type: str) -> str:
        if source_type == SOURCE_PENDING:
            return self.pending_path
        if source_type == SOURCE_FINAL:
            return self.final_path
        return ""


@dataclass
class PathDetection:
    path: str
    exists: bool = False
    is_dir: bool = False
    readable: bool = False
    error: str = ""
    is_business_subdir: bool = False

    @property
    def ok(self) -> bool:
        return self.exists and self.is_dir and self.readable

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "exists": self.exists,
            "is_dir": self.is_dir,
            "readable": self.readable,
            "error": self.error,
            "ok": self.ok,
        }


@dataclass
class ProjectDetection:
    parent_path: str = ""
    pending: PathDetection | None = None
    final: PathDetection | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "parent_path": self.parent_path,
            "pending": self.pending.to_dict() if self.pending else None,
            "final": self.final.to_dict() if self.final else None,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }
