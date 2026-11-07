"""统一结果对象，避免新模块用异常做普通流程控制。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Result:
    """轻量结果对象。

    ok=True 时 value 有效；ok=False 时 error 为可读错误，code 为稳定错误码。
    """

    ok: bool
    value: Any = None
    code: str = ""
    error: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def unwrap(self) -> Any:
        if not self.ok:
            raise ValueError(f"{self.code}: {self.error}")
        return self.value


def ok(value: Any = None, **details: Any) -> Result:
    return Result(ok=True, value=value, details=details)


def fail(code: str, error: str, **details: Any) -> Result:
    return Result(ok=False, code=code, error=error, details=details)
