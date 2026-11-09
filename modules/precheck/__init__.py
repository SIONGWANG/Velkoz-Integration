"""翻译与预审模块（v1.25.3）。

公开 API：
- scan_pairs：只读识别中文/英文 TXT 配对；
- PrecheckService：模型配置、翻译预览、确认写入、语法/一致性检查、报告与导入；
- 历史外部报告只读导入，不修改外部工具目录与文件。

约束：
- 翻译/检查/预览只调用模型与读取文件，不写源目录；
- 只有用户确认写入时才写英文 TXT，且写前备份、原子写入、失败不覆盖；
- API Key 用 Windows DPAPI 加密保存，界面掩码，日志/报告不含密钥。
"""
from __future__ import annotations

from .cost import compute_cost, format_cost, summarize_results, usage_row
from .errors import PrecheckError, catalog_rows
from .models import (
    CheckResult,
    CheckRunResult,
    PairItem,
    PairingOptions,
    ProviderProfile,
    TranslationRequest,
    TranslationResult,
    TranslationRunResult,
)
from .pairing import PairScanResult, derive_en_path, scan_pairs
from .prompts import DEFAULT_PROMPTS, PromptTemplate, default_prompt, prompt_version
from .providers import PROVIDER_PRESETS, build_provider, default_profiles
from .report import export_reports, write_csv_files, write_excel
from .report_import import ImportResult, build_import
from .safety import atomic_write_text, backup_file, read_text_file, write_translation
from .secrets import SecretsError, SecretsStore, mask_key
from .service import PrecheckService

__all__ = [
    "PrecheckService",
    "PrecheckError",
    "catalog_rows",
    "PairingOptions",
    "PairItem",
    "PairScanResult",
    "scan_pairs",
    "derive_en_path",
    "ProviderProfile",
    "PROVIDER_PRESETS",
    "default_profiles",
    "build_provider",
    "TranslationRequest",
    "TranslationResult",
    "TranslationRunResult",
    "CheckResult",
    "CheckRunResult",
    "PromptTemplate",
    "DEFAULT_PROMPTS",
    "default_prompt",
    "prompt_version",
    "SecretsStore",
    "SecretsError",
    "mask_key",
    "read_text_file",
    "atomic_write_text",
    "backup_file",
    "write_translation",
    "compute_cost",
    "format_cost",
    "summarize_results",
    "usage_row",
    "export_reports",
    "write_excel",
    "write_csv_files",
    "ImportResult",
    "build_import",
]
