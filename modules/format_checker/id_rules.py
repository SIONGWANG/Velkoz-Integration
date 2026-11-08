"""任务 ID 规则编译与匹配。"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .models import IdRule


@dataclass
class IdMatcher:
    rule: IdRule

    def __post_init__(self) -> None:
        flags = 0 if self.rule.case_sensitive else re.IGNORECASE
        if self.rule.mode == "digits":
            length = max(1, int(self.rule.length))
            self._full = re.compile(rf"^[0-9]{{{length}}}$")
            self._leading = re.compile(rf"^(?P<id>[0-9]{{{length}}})(?=\D|$)")
            self._canonical = re.compile(rf"^[0-9]{{{length}}}$")
        elif self.rule.mode == "prefix_digits":
            length = max(1, int(self.rule.length))
            prefix = re.escape(self.rule.prefix or "JD")
            self._full = re.compile(rf"^{prefix}[0-9]{{{length}}}$", flags)
            self._leading = re.compile(
                rf"^(?P<id>{prefix}[0-9]{{{length}}})(?=\D|$)", flags
            )
            self._canonical = re.compile(rf"^{prefix}[0-9]{{{length}}}$", flags)
        elif self.rule.mode == "regex":
            pattern = self.rule.regex
            if not pattern:
                raise ValueError("ID 规则 mode=regex 时必须提供 regex")
            self._full = re.compile(pattern, flags)
            self._leading = re.compile(rf"^(?P<id>{pattern})(?=\D|$)", flags)
            self._canonical = re.compile(pattern, flags)
        else:
            raise ValueError(f"未知 ID 规则模式：{self.rule.mode}")

    def matches(self, name: str) -> bool:
        return bool(self._full.fullmatch(name or ""))

    def is_strict_case(self, name: str) -> bool:
        """名称是否与规范化大小写完全一致。"""
        if self.rule.case_sensitive:
            return True
        if self.rule.mode == "prefix_digits":
            return name.startswith(self.rule.prefix)
        return True

    def extract_leading_id(self, stem: str) -> str | None:
        match = self._leading.match(stem or "")
        return match.group("id") if match else None

    def describe(self) -> str:
        if self.rule.mode == "digits":
            return f"{self.rule.length} 位纯数字"
        if self.rule.mode == "prefix_digits":
            return f"{self.rule.prefix} + {self.rule.length} 位数字"
        return f"正则 {self.rule.regex}"


def has_width_issue(name: str) -> bool:
    """全角字符、中文空格、首尾空格、数字间空格等异常。"""
    if not name:
        return False
    if name != name.strip() or "  " in name or "\u3000" in name:
        return True
    if re.search(r"\d[ \t]+\d", name):
        return True
    for char in name:
        if unicodedata.east_asian_width(char) == "F":
            return True
    return False


def normalize_ext(ext: str) -> str:
    return (ext or "").lower().lstrip(".")


IMAGE_EXTENSIONS = {
    "jpg",
    "jpeg",
    "png",
    "webp",
    "bmp",
    "tif",
    "tiff",
    "gif",
    "psd",
    "avif",
}
