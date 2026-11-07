"""扫描服务：纯扫描与数据库记录编排。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from modules.audit_log.service import AuditLogger
from modules.config import app_config
from modules.database.connection import Database, open_database
from modules.database.repositories.management_repo import (
    get_rule_set,
    list_rule_sets,
    recent_scan_runs,
    save_rule_set,
    save_scan_run,
    seed_builtin_ruleset,
)
from modules.scanning.diagnostics import summarize
from modules.scanning.rules import RuleSet, load_default_ruleset, validate_ruleset
from modules.scanning.scanner import ScanOptions, ScanResult, scan_root


class ScanService:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else app_config.get_db_path()
        self.db = open_database(self.db_path)
        self._seed_default()

    def _seed_default(self) -> None:
        try:
            default = load_default_ruleset()
            errors = validate_ruleset(default)
            if not errors:
                seed_builtin_ruleset(self.db, default)
        except Exception:
            # 种子失败不阻塞扫描预览
            pass

    def list_rule_sets(self) -> list[dict[str, Any]]:
        return list_rule_sets(self.db)

    def get_rule_set(self, rule_set_id: str) -> RuleSet | None:
        return get_rule_set(self.db, rule_set_id)

    def default_ruleset(self) -> RuleSet:
        for item in self.list_rule_sets():
            if item.get("is_default"):
                loaded = get_rule_set(self.db, item["id"])
                if loaded:
                    return loaded
        return load_default_ruleset()

    def save_ruleset(self, ruleset: RuleSet, *, operator: str = "", note: str = "", make_default: bool = False) -> str:
        errors = validate_ruleset(ruleset)
        if errors:
            raise ValueError("；".join(errors))
        version_id = save_rule_set(
            self.db,
            ruleset,
            created_by=operator,
            note=note,
            make_default=make_default,
        )
        AuditLogger(self.db).log(
            module="scanning",
            action="save_ruleset",
            operator=operator,
            target_path=ruleset.id,
            detail={"name": ruleset.name, "version": ruleset.version, "make_default": make_default},
        )
        return version_id

    def preview(
        self,
        root: str,
        ruleset: RuleSet,
        options: ScanOptions | None = None,
        progress: Callable[[int, int, str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> ScanResult:
        return scan_root(root, ruleset, options=options, progress=progress, should_cancel=should_cancel)

    def save_result(self, result: ScanResult, *, operator: str = "", rule_set_id: str | None = None) -> str:
        run_id = save_scan_run(
            self.db,
            result,
            operator=operator,
            rule_set_id=rule_set_id,
        )
        AuditLogger(self.db).log(
            module="scanning",
            action="save_scan_run",
            operator=operator,
            target_path=result.root,
            run_id=run_id,
            detail=summarize(result),
            duration_ms=result.duration_ms,
        )
        return run_id

    def recent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        return recent_scan_runs(self.db, limit)

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "ScanService":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
