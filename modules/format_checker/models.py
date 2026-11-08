"""格式检查领域模型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


DEFAULT_IMAGE_EXTENSIONS = ("jpg", "jpeg", "png")
DEFAULT_IGNORE_NAMES = (
    "Thumbs.db",
    "desktop.ini",
    ".DS_Store",
    "~$*",
    "*.tmp",
)


@dataclass(frozen=True)
class IdRule:
    mode: str = "digits"  # digits / prefix_digits / regex
    length: int = 8
    prefix: str = "JD"
    regex: str = ""
    case_sensitive: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "length": self.length,
            "prefix": self.prefix,
            "regex": self.regex,
            "case_sensitive": self.case_sensitive,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "IdRule":
        data = data or {}
        return cls(
            mode=str(data.get("mode", "digits")),
            length=int(data.get("length", 8) or 8),
            prefix=str(data.get("prefix", "JD")),
            regex=str(data.get("regex", "")),
            case_sensitive=bool(data.get("case_sensitive", False)),
        )


@dataclass(frozen=True)
class FileRule:
    suffix: str = ""
    extensions: tuple[str, ...] = DEFAULT_IMAGE_EXTENSIONS
    required: bool = False
    role: str = "primary"

    def to_dict(self) -> dict[str, Any]:
        return {
            "suffix": self.suffix,
            "extensions": list(self.extensions),
            "required": self.required,
            "role": self.role,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FileRule":
        return cls(
            suffix=str(data.get("suffix", "")),
            extensions=tuple(str(item).lower().lstrip(".") for item in (data.get("extensions") or DEFAULT_IMAGE_EXTENSIONS)),
            required=bool(data.get("required", False)),
            role=str(data.get("role", "other")),
        )


@dataclass(frozen=True)
class MultiRule:
    suffix_prefix: str = "_"
    start: int = 1
    max_count: int | None = 4
    contiguous: bool = True
    min_count: int = 1
    extensions: tuple[str, ...] = DEFAULT_IMAGE_EXTENSIONS
    allow_result: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "suffix_prefix": self.suffix_prefix,
            "start": self.start,
            "max_count": self.max_count,
            "contiguous": self.contiguous,
            "min_count": self.min_count,
            "extensions": list(self.extensions),
            "allow_result": self.allow_result,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "MultiRule | None":
        if not data:
            return None
        max_count = data.get("max_count", 4)
        return cls(
            suffix_prefix=str(data.get("suffix_prefix", "_")),
            start=int(data.get("start", 1) if data.get("start") is not None else 1),
            max_count=None if max_count is None else int(max_count),
            contiguous=bool(data.get("contiguous", True)),
            min_count=int(data.get("min_count", 1) if data.get("min_count") is not None else 1),
            extensions=tuple(str(item).lower().lstrip(".") for item in (data.get("extensions") or DEFAULT_IMAGE_EXTENSIONS)),
            allow_result=bool(data.get("allow_result", False)),
        )


@dataclass(frozen=True)
class ScanRule:
    recursive: bool = False
    ignore_names: tuple[str, ...] = DEFAULT_IGNORE_NAMES
    max_tasks: int = 200000
    follow_symlinks: bool = False
    skip_reparse: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "recursive": self.recursive,
            "ignore_names": list(self.ignore_names),
            "max_tasks": self.max_tasks,
            "follow_symlinks": self.follow_symlinks,
            "skip_reparse": self.skip_reparse,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ScanRule":
        data = data or {}
        return cls(
            recursive=bool(data.get("recursive", False)),
            ignore_names=tuple(str(item) for item in (data.get("ignore_names") or DEFAULT_IGNORE_NAMES)),
            max_tasks=int(data.get("max_tasks", 200000) or 200000),
            follow_symlinks=bool(data.get("follow_symlinks", False)),
            skip_reparse=bool(data.get("skip_reparse", True)),
        )


@dataclass
class Template:
    id: str
    name: str
    version: int = 1
    schema_version: int = 1
    scope: str = "global"
    scope_id: str = ""
    builtin: bool = False
    description: str = ""
    id_rule: IdRule = field(default_factory=IdRule)
    modes: tuple[str, ...] = ("single", "multi")
    single: tuple[FileRule, ...] = ()
    multi: MultiRule | None = None
    extra_files: str = "warn"
    warn_as_fail: bool = False
    scan: ScanRule = field(default_factory=ScanRule)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "scope": self.scope,
            "scope_id": self.scope_id,
            "builtin": self.builtin,
            "description": self.description,
            "id_rule": self.id_rule.to_dict(),
            "modes": list(self.modes),
            "single": [item.to_dict() for item in self.single],
            "multi": self.multi.to_dict() if self.multi else None,
            "extra_files": self.extra_files,
            "warn_as_fail": self.warn_as_fail,
            "scan": self.scan.to_dict(),
        }

    def to_json(self) -> str:
        import json

        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Template":
        single = tuple(FileRule.from_dict(item) for item in (data.get("single") or []))
        modes = tuple(str(item) for item in (data.get("modes") or ["single"]))
        return cls(
            id=str(data.get("id", "")).strip(),
            name=str(data.get("name", "")).strip(),
            version=int(data.get("version", 1) or 1),
            schema_version=int(data.get("schema_version", 1) or 1),
            scope=str(data.get("scope", "global")),
            scope_id=str(data.get("scope_id", "")),
            builtin=bool(data.get("builtin", False)),
            description=str(data.get("description", "")),
            id_rule=IdRule.from_dict(data.get("id_rule")),
            modes=modes,
            single=single,
            multi=MultiRule.from_dict(data.get("multi")),
            extra_files=str(data.get("extra_files", "warn")),
            warn_as_fail=bool(data.get("warn_as_fail", False)),
            scan=ScanRule.from_dict(data.get("scan")),
        )


@dataclass
class Issue:
    code: str
    severity: str
    task_id: str = ""
    task_path: str = ""
    file_path: str | None = None
    expected: str | None = None
    actual: str | None = None
    message: str = ""
    suggestion: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "task_id": self.task_id,
            "task_path": self.task_path,
            "file_path": self.file_path,
            "expected": self.expected,
            "actual": self.actual,
            "message": self.message,
            "suggestion": self.suggestion,
        }


@dataclass
class TaskResult:
    task_id: str
    task_path: str
    detected_mode: str | None
    result: str  # pass / fail / warn / skipped
    image_count: int = 0
    txt_count: int = 0
    file_count: int = 0
    issues: list[Issue] = field(default_factory=list)
    fingerprint: str = ""

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    def error_count(self) -> int:
        return sum(1 for item in self.issues if item.severity == "error")

    def warn_count(self) -> int:
        return sum(1 for item in self.issues if item.severity == "warn")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_path": self.task_path,
            "detected_mode": self.detected_mode,
            "result": self.result,
            "image_count": self.image_count,
            "txt_count": self.txt_count,
            "file_count": self.file_count,
            "issue_count": self.issue_count,
            "fingerprint": self.fingerprint,
            "issues": [item.to_dict() for item in self.issues],
        }


@dataclass
class RunSummary:
    run_id: str = ""
    root_path: str = ""
    batch_id: str = ""
    operator: str = ""
    template_id: str = ""
    template_name: str = ""
    template_version: int = 0
    started_at: str = ""
    finished_at: str = ""
    scanned_tasks: int = 0
    passed_tasks: int = 0
    failed_tasks: int = 0
    warning_tasks: int = 0
    skipped_tasks: int = 0
    issues_by_code: dict[str, int] = field(default_factory=dict)
    integrity_status: str = "ok"
    status: str = "completed"
    duration_ms: int = 0
    report_path: str = ""
    previous_run_id: str = ""
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "root_path": self.root_path,
            "batch_id": self.batch_id,
            "operator": self.operator,
            "template_id": self.template_id,
            "template_name": self.template_name,
            "template_version": self.template_version,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "scanned_tasks": self.scanned_tasks,
            "passed_tasks": self.passed_tasks,
            "failed_tasks": self.failed_tasks,
            "warning_tasks": self.warning_tasks,
            "skipped_tasks": self.skipped_tasks,
            "issues_by_code": dict(self.issues_by_code),
            "integrity_status": self.integrity_status,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "report_path": self.report_path,
            "previous_run_id": self.previous_run_id,
            "truncated": self.truncated,
        }


@dataclass
class FormatCheckRequest:
    root_path: str
    template_id: str = ""
    template_version_id: str = ""
    batch_id: str | None = None
    recursive: bool | None = None
    only_anomalies: bool = False
    previous_run_id: str | None = None
    max_tasks: int | None = None
    operator: str = ""
    write_report: bool = True
    report_dir: str | None = None


@dataclass
class FormatCheckResult:
    summary: RunSummary
    tasks: list[TaskResult] = field(default_factory=list)
    run_issues: list[Issue] = field(default_factory=list)
    report_paths: dict[str, str] = field(default_factory=dict)
    integrity_changes: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary.to_dict(),
            "tasks": [item.to_dict() for item in self.tasks],
            "run_issues": [item.to_dict() for item in self.run_issues],
            "report_paths": dict(self.report_paths),
            "integrity_changes": list(self.integrity_changes),
        }
