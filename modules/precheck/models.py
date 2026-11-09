"""翻译与预审领域模型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from modules.format_checker.id_rules import IdRule

DEFAULT_IGNORE_NAMES = (
    "Thumbs.db",
    "desktop.ini",
    ".DS_Store",
)

# 配对状态
STATUS_NO_ZH = "no_zh"
STATUS_MISSING_EN = "missing_en"
STATUS_EMPTY_EN = "empty_en"
STATUS_TRANSLATED = "translated"
STATUS_EMPTY_ZH = "empty_zh"
STATUS_INVALID = "invalid"

PAIR_STATUS_LABELS = {
    STATUS_NO_ZH: "缺少中文 TXT",
    STATUS_MISSING_EN: "缺少英文 TXT",
    STATUS_EMPTY_EN: "英文 TXT 为空",
    STATUS_TRANSLATED: "已有英文",
    STATUS_EMPTY_ZH: "中文 TXT 为空",
    STATUS_INVALID: "读取失败",
}


@dataclass(frozen=True)
class PairingOptions:
    """配对扫描选项。只读取目录项与 TXT 内容，不写任何源文件。"""

    recursive: bool = False
    zh_suffixes: tuple[str, ...] = ("_CH", "_ZH")
    en_suffixes: tuple[str, ...] = ("_EN",)
    txt_extension: str = ".txt"
    max_tasks: int = 200000
    load_text: bool = True
    max_text_bytes: int = 1_000_000
    id_rule: IdRule = field(default_factory=IdRule)
    ignore_names: tuple[str, ...] = DEFAULT_IGNORE_NAMES

    def to_dict(self) -> dict[str, Any]:
        return {
            "recursive": self.recursive,
            "zh_suffixes": list(self.zh_suffixes),
            "en_suffixes": list(self.en_suffixes),
            "txt_extension": self.txt_extension,
            "max_tasks": self.max_tasks,
            "load_text": self.load_text,
            "max_text_bytes": self.max_text_bytes,
            "id_rule": self.id_rule.to_dict(),
            "ignore_names": list(self.ignore_names),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "PairingOptions":
        data = data or {}
        return cls(
            recursive=bool(data.get("recursive", False)),
            zh_suffixes=tuple(data.get("zh_suffixes") or ("_CH", "_ZH")),
            en_suffixes=tuple(data.get("en_suffixes") or ("_EN",)),
            txt_extension=str(data.get("txt_extension") or ".txt"),
            max_tasks=int(data.get("max_tasks") or 200000),
            load_text=bool(data.get("load_text", True)),
            max_text_bytes=int(data.get("max_text_bytes") or 1_000_000),
            id_rule=IdRule.from_dict(data.get("id_rule")),
            ignore_names=tuple(data.get("ignore_names") or DEFAULT_IGNORE_NAMES),
        )


@dataclass
class PairItem:
    """一个任务目录的中英文 TXT 配对结果。"""

    task_id: str
    folder_path: str
    zh_txt_path: str = ""
    en_txt_path: str = ""
    zh_text: str = ""
    en_text: str = ""
    zh_encoding: str = ""
    en_encoding: str = ""
    status: str = STATUS_NO_ZH
    zh_length: int = 0
    en_length: int = 0
    error_code: str = ""
    error_message: str = ""

    @property
    def status_label(self) -> str:
        return PAIR_STATUS_LABELS.get(self.status, self.status)

    def to_dict(self, *, include_text: bool = True) -> dict[str, Any]:
        data = {
            "task_id": self.task_id,
            "folder_path": self.folder_path,
            "zh_txt_path": self.zh_txt_path,
            "en_txt_path": self.en_txt_path,
            "zh_encoding": self.zh_encoding,
            "en_encoding": self.en_encoding,
            "status": self.status,
            "status_label": self.status_label,
            "zh_length": self.zh_length,
            "en_length": self.en_length,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }
        if include_text:
            data["zh_text"] = self.zh_text
            data["en_text"] = self.en_text
        return data


@dataclass
class ProviderProfile:
    """模型连接配置。API Key 单独用 DPAPI 保存，不进入该结构。"""

    id: str
    name: str
    provider: str = "ark"  # ark / dashscope / openai_compat
    base_url: str = ""
    model: str = ""
    timeout: float = 60.0
    max_retries: int = 2
    concurrency: int = 4
    temperature: float = 0.2
    input_price: float = 0.0
    output_price: float = 0.0
    unit_tokens: int = 1_000_000
    currency: str = "CNY"
    builtin: bool = False
    extra_headers: dict[str, str] = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "concurrency": self.concurrency,
            "temperature": self.temperature,
            "input_price": self.input_price,
            "output_price": self.output_price,
            "unit_tokens": self.unit_tokens,
            "currency": self.currency,
            "builtin": self.builtin,
            "extra_headers": dict(self.extra_headers),
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ProviderProfile":
        data = data or {}
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or ""),
            provider=str(data.get("provider") or "ark"),
            base_url=str(data.get("base_url") or ""),
            model=str(data.get("model") or ""),
            timeout=float(data.get("timeout") or 60.0),
            max_retries=int(data.get("max_retries") or 0),
            concurrency=max(1, int(data.get("concurrency") or 1)),
            temperature=float(data.get("temperature") or 0.0),
            input_price=float(data.get("input_price") or 0.0),
            output_price=float(data.get("output_price") or 0.0),
            unit_tokens=max(1, int(data.get("unit_tokens") or 1_000_000)),
            currency=str(data.get("currency") or "CNY"),
            builtin=bool(data.get("builtin", False)),
            extra_headers=dict(data.get("extra_headers") or {}),
            note=str(data.get("note") or ""),
        )


@dataclass
class TranslationRequest:
    root_path: str
    profile_id: str = ""
    batch_id: str = ""
    task_paths: tuple[str, ...] = ()
    only_missing: bool = True
    max_items: int | None = None
    operator: str = ""
    recursive: bool = False


@dataclass
class TranslationResult:
    item: PairItem
    status: str = "failed"  # translated / failed / skipped
    english: str = ""
    token_input: int = 0
    token_output: int = 0
    cost: float = 0.0
    attempts: int = 0
    error_code: str = ""
    error_message: str = ""
    duration_ms: int = 0
    written_at: str = ""
    backup_path: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "translated" and bool(self.english.strip())

    def to_dict(self, *, include_text: bool = True) -> dict[str, Any]:
        data = self.item.to_dict(include_text=include_text)
        data.update(
            {
                "result_status": self.status,
                "english": self.english,
                "token_input": self.token_input,
                "token_output": self.token_output,
                "cost": self.cost,
                "attempts": self.attempts,
                "error_code": self.error_code,
                "error_message": self.error_message,
                "duration_ms": self.duration_ms,
                "written_at": self.written_at,
                "backup_path": self.backup_path,
            }
        )
        return data


@dataclass
class CheckResult:
    """语法 / 一致性检查结果（只出建议，不自动写）。"""

    task_id: str
    folder_path: str
    kind: str  # grammar / consistency
    passed: bool | None = None
    issues: list[str] = field(default_factory=list)
    suggestion: str = ""
    original: str = ""
    raw: str = ""
    token_input: int = 0
    token_output: int = 0
    cost: float = 0.0
    error_code: str = ""
    error_message: str = ""
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "folder_path": self.folder_path,
            "kind": self.kind,
            "passed": self.passed,
            "issues": list(self.issues),
            "suggestion": self.suggestion,
            "original": self.original,
            "raw": self.raw,
            "token_input": self.token_input,
            "token_output": self.token_output,
            "cost": self.cost,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "duration_ms": self.duration_ms,
        }


@dataclass
class TranslationRunResult:
    job_id: str
    results: list[TranslationResult] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    status: str = "done"
    token_input: int = 0
    token_output: int = 0
    cost: float = 0.0
    currency: str = "CNY"
    cancelled: bool = False
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def translated(self) -> list[TranslationResult]:
        return [item for item in self.results if item.status == "translated"]

    @property
    def failed(self) -> list[TranslationResult]:
        return [item for item in self.results if item.status == "failed"]

    @property
    def skipped(self) -> list[TranslationResult]:
        return [item for item in self.results if item.status == "skipped"]


@dataclass
class CheckRunResult:
    job_id: str
    kind: str
    results: list[CheckResult] = field(default_factory=list)
    token_input: int = 0
    token_output: int = 0
    cost: float = 0.0
    status: str = "done"
    cancelled: bool = False

    @property
    def passed(self) -> int:
        return sum(1 for item in self.results if item.passed is True)

    @property
    def failed(self) -> int:
        return sum(1 for item in self.results if item.passed is False)

    @property
    def errored(self) -> int:
        return sum(1 for item in self.results if item.error_code)
