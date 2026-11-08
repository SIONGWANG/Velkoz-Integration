"""原生格式检查模块（v1.25.2）。

公开 API：
- check_path / preview_path：只读检查，不写数据库；
- export_reports：生成 Excel/CSV 报告；
- FormatCheckerService：模板、检查记录、历史与差异。
"""
from __future__ import annotations

from .checker import check_path, diff_task_results, preview_path
from .models import (
    FileRule,
    FormatCheckRequest,
    FormatCheckResult,
    IdRule,
    Issue,
    MultiRule,
    RunSummary,
    ScanRule,
    TaskResult,
    Template,
)
from .report import export_reports, write_csv, write_excel
from .service import FormatCheckerService
from .templates import builtin_template, parse_template_json, validate_template

__all__ = [
    "check_path",
    "preview_path",
    "export_reports",
    "write_csv",
    "write_excel",
    "diff_task_results",
    "FormatCheckerService",
    "Template",
    "IdRule",
    "FileRule",
    "MultiRule",
    "ScanRule",
    "Issue",
    "TaskResult",
    "RunSummary",
    "FormatCheckRequest",
    "FormatCheckResult",
    "builtin_template",
    "parse_template_json",
    "validate_template",
]
