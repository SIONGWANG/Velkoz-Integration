"""其他验收员记录导入领域模型（v1.25.4）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 文件类型
TYPE_LEGACY_CSV = "legacy_csv"
TYPE_EXPORT_CSV = "export_csv"
TYPE_OTHER_STATS = "other_stats"
TYPE_BACKUP = "backup"
TYPE_UNCLASSIFIED = "unclassified"

SOURCE_TYPE_LABELS = {
    TYPE_LEGACY_CSV: "工作记录 CSV",
    TYPE_EXPORT_CSV: "正式导出 CSV",
    TYPE_OTHER_STATS: "统计/汇总表",
    TYPE_BACKUP: "备份表",
    TYPE_UNCLASSIFIED: "无法识别",
}

# 发现状态
FILE_READY = "ready"
FILE_UNRECOGNIZED = "unrecognized"
FILE_UNSUPPORTED = "unsupported"
FILE_ERROR = "error"

FILE_STATUS_LABELS = {
    FILE_READY: "可导入",
    FILE_UNRECOGNIZED: "结构无法识别",
    FILE_UNSUPPORTED: "格式不支持",
    FILE_ERROR: "读取失败",
}

# 导入状态（数据库 record_imports.status）
IMPORT_COMPLETED = "completed"
IMPORT_PARTIAL = "partial"
IMPORT_FAILED = "failed"
IMPORT_STATUS_LABELS = {
    IMPORT_COMPLETED: "已导入",
    IMPORT_PARTIAL: "部分导入",
    IMPORT_FAILED: "导入失败",
}

# 记录关联状态
LINK_MATCHED = "matched"
LINK_UNMATCHED = "unmatched"
LINK_NOTE_ONLY = "note_only"
LINK_LABELS = {
    LINK_MATCHED: "已关联任务",
    LINK_UNMATCHED: "未关联任务",
    LINK_NOTE_ONLY: "纯备注记录",
}

DEFAULT_IGNORE_NAMES = ("Thumbs.db", "desktop.ini", ".DS_Store", "~$*")
SUPPORTED_EXTENSIONS = ("csv", "xlsx", "xls")
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_FILES = 5000


@dataclass
class DiscoveredRecordFile:
    path: str
    name: str
    ext: str
    size: int = 0
    mtime_ns: int = 0
    source_type: str = TYPE_UNCLASSIFIED
    status: str = FILE_UNRECOGNIZED
    operator: str = ""
    rows_total: int = 0
    columns: list[str] = field(default_factory=list)
    encoding: str = ""
    note: str = ""
    error_code: str = ""

    @property
    def type_label(self) -> str:
        return SOURCE_TYPE_LABELS.get(self.source_type, self.source_type)

    @property
    def status_label(self) -> str:
        return FILE_STATUS_LABELS.get(self.status, self.status)

    @property
    def importable(self) -> bool:
        return self.status == FILE_READY and self.source_type != TYPE_UNCLASSIFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "name": self.name,
            "ext": self.ext,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "source_type": self.source_type,
            "type_label": self.type_label,
            "status": self.status,
            "status_label": self.status_label,
            "operator": self.operator,
            "rows_total": self.rows_total,
            "columns": list(self.columns),
            "encoding": self.encoding,
            "note": self.note,
            "error_code": self.error_code,
            "importable": self.importable,
        }


@dataclass
class ParsedRecordRow:
    """从外部表解析出的一行，保留原文，不做业务改写。"""

    source_row: int
    task_id: str = ""
    operator: str = ""
    result: str = ""
    l1: str = ""
    l2: str = ""
    note: str = ""
    tags_raw: str = ""
    evidence_raw: str = ""
    record_time: str = ""
    path_raw: str = ""
    fingerprint: str = ""
    data_group_id: str = ""
    batch_id: str = ""
    link_status: str = LINK_UNMATCHED
    error_code: str = ""
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_row": self.source_row,
            "task_id": self.task_id,
            "operator": self.operator,
            "result": self.result,
            "l1": self.l1,
            "l2": self.l2,
            "note": self.note,
            "tags_raw": self.tags_raw,
            "evidence_raw": self.evidence_raw,
            "record_time": self.record_time,
            "path_raw": self.path_raw,
            "fingerprint": self.fingerprint,
            "data_group_id": self.data_group_id,
            "batch_id": self.batch_id,
            "link_status": self.link_status,
            "link_label": LINK_LABELS.get(self.link_status, self.link_status),
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


@dataclass
class RecordImportResult:
    import_id: str = ""
    source_path: str = ""
    source_type: str = TYPE_UNCLASSIFIED
    status: str = IMPORT_FAILED
    rows_total: int = 0
    rows_new: int = 0
    rows_skipped: int = 0
    rows_error: int = 0
    operator_detected: str = ""
    batch_id: str = ""
    note: str = ""
    rows: list[ParsedRecordRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def status_label(self) -> str:
        return IMPORT_STATUS_LABELS.get(self.status, self.status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "import_id": self.import_id,
            "source_path": self.source_path,
            "source_type": self.source_type,
            "status": self.status,
            "status_label": self.status_label,
            "rows_total": self.rows_total,
            "rows_new": self.rows_new,
            "rows_skipped": self.rows_skipped,
            "rows_error": self.rows_error,
            "operator_detected": self.operator_detected,
            "batch_id": self.batch_id,
            "note": self.note,
            "warnings": list(self.warnings),
            "rows": [row.to_dict() for row in self.rows],
        }
