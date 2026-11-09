"""其他验收员记录导入模块（v1.25.4）。

公开 API：
- discover_files：只读发现 CSV/Excel 并识别类型；
- RecordImportService：导入、去重、批次关联、导入日志与明细查询。

约束：
- 不修改、删除、移动、重命名任何原文件；
- 只写管理数据库；
- 未定义标签与备注原样保留；
- 无法匹配的任务保留为未关联。
"""
from __future__ import annotations

from .discovery import DiscoveryResult, discover_files, summary
from .errors import ERROR_CATALOG, RecordImportError, catalog_rows
from .models import (
    DiscoveredRecordFile,
    ParsedRecordRow,
    RecordImportResult,
    SOURCE_TYPE_LABELS,
)
from .parser import classify_table, inspect_table, parse_rows, read_table
from .service import RecordImportService

__all__ = [
    "RecordImportService",
    "RecordImportError",
    "DiscoveryResult",
    "DiscoveredRecordFile",
    "ParsedRecordRow",
    "RecordImportResult",
    "SOURCE_TYPE_LABELS",
    "discover_files",
    "summary",
    "classify_table",
    "inspect_table",
    "parse_rows",
    "read_table",
    "ERROR_CATALOG",
    "catalog_rows",
]
