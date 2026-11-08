"""格式检查服务：模板管理、运行编排、落库、报告、历史与差异。"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Callable

from modules.common.path_guard import normalize_path
from modules.common.timeutil import today_str
from modules.config import app_config
from modules.database.connection import Database, open_database
from modules.database.repositories import format_repo

from .checker import check_path, diff_task_results, preview_path
from .models import FormatCheckRequest, FormatCheckResult, Template
from .report import export_reports, guard_report_dir
from .templates import builtin_template, validate_template

ProgressCallback = Callable[[int, int, str], None]
CancelCallback = Callable[[], bool]


def _default_report_dir() -> str:
    return str(app_config.get_reports_dir() / "format_check" / today_str())


def _json_loads(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


class FormatCheckerService:
    def __init__(
        self,
        db_path: str | Path | None = None,
        report_dir: str | Path | None = None,
    ):
        self.db: Database = open_database(db_path)
        self.report_dir = Path(report_dir) if report_dir else None
        format_repo.seed_builtin_template(self.db, builtin_template())

    # ── 模板 ─────────────────────────────────────────────

    def list_templates(self) -> list[dict[str, Any]]:
        return format_repo.list_templates(self.db)

    def get_template(self, template_id: str) -> Template | None:
        loaded = format_repo.load_active_template(self.db, template_id)
        return loaded[0] if loaded else None

    def get_template_with_version(self, template_id: str) -> tuple[Template, str, int] | None:
        return format_repo.load_active_template(self.db, template_id)

    def default_template(self) -> Template:
        template = self.get_template("builtin_default")
        if template is None:
            template = builtin_template()
            format_repo.seed_builtin_template(self.db, template)
        return template

    def save_template(
        self,
        template: Template,
        *,
        operator: str = "",
        note: str = "",
    ) -> str:
        if not template.id:
            template.id = f"tpl_{uuid.uuid4().hex[:8]}"
        errors = validate_template(template)
        if errors:
            raise ValueError("；".join(errors))
        return format_repo.save_template(
            self.db, template, created_by=operator, note=note
        )

    def delete_template(self, template_id: str) -> None:
        format_repo.delete_template(self.db, template_id)

    def template_versions(self, template_id: str) -> list[dict[str, Any]]:
        return format_repo.template_versions(self.db, template_id)

    # ── 检查 ─────────────────────────────────────────────

    def _resolve_template(
        self,
        template_id: str = "",
        template_version_id: str = "",
    ) -> tuple[Template, str]:
        if template_version_id:
            loaded = format_repo.load_template_version(self.db, template_version_id)
            if loaded is None:
                raise ValueError("模板版本不存在")
            return loaded[0], loaded[1]
        wanted = template_id or "builtin_default"
        loaded = format_repo.load_active_template(self.db, wanted)
        if loaded is None and wanted == "builtin_default":
            format_repo.seed_builtin_template(self.db, builtin_template())
            loaded = format_repo.load_active_template(self.db, wanted)
        if loaded is None:
            raise ValueError(f"模板不存在：{wanted}")
        return loaded[0], loaded[1]

    def preview(
        self,
        root_path: str,
        *,
        template_id: str = "",
        template_version_id: str = "",
        recursive: bool | None = None,
        max_tasks: int | None = None,
    ) -> FormatCheckResult:
        template, _ = self._resolve_template(template_id, template_version_id)
        return preview_path(
            root_path,
            template,
            recursive=recursive,
            max_tasks=max_tasks,
        )

    def run_check(
        self,
        request: FormatCheckRequest,
        *,
        progress: ProgressCallback | None = None,
        should_cancel: CancelCallback | None = None,
        diff_with_previous: bool = False,
        batch_name: str = "",
    ) -> FormatCheckResult:
        template, version_id = self._resolve_template(
            request.template_id, request.template_version_id
        )
        root = normalize_path(request.root_path)
        if not os.path.exists(root) or not os.path.isdir(root):
            raise ValueError(f"检查根目录不存在或不是目录：{root}")

        report_dir = request.report_dir or self._report_dir()
        if request.write_report:
            guard_report_dir(report_dir, root)

        only_paths: set[str] | None = None
        if request.only_anomalies:
            if not request.previous_run_id:
                raise ValueError("“仅异常”需要指定上一次检查记录")
            results = ["fail", "warn"]
            only_paths = set(
                format_repo.run_task_paths(self.db, request.previous_run_id, results)
            )

        result = check_path(
            root,
            template,
            recursive=request.recursive,
            max_tasks=request.max_tasks,
            only_paths=only_paths,
            operator=request.operator,
            batch_id=request.batch_id,
            previous_run_id=request.previous_run_id,
            progress=progress,
            should_cancel=should_cancel,
        )
        result.summary.template_id = template.id
        result.summary.template_name = template.name
        result.summary.template_version = template.version

        run_id = format_repo.save_check_result(
            self.db,
            result,
            template=template,
            template_version_id=version_id,
            batch_id=request.batch_id,
            operator=request.operator,
            previous_run_id=request.previous_run_id,
        )

        diff = None
        if diff_with_previous and request.previous_run_id:
            try:
                diff = self.diff_runs(request.previous_run_id, run_id)
            except ValueError:
                diff = None

        report_paths: dict[str, str] = {}
        if request.write_report:
            report_paths = export_reports(
                result,
                report_dir,
                template,
                source_root=root,
                diff=diff,
                batch_name=batch_name or request.batch_id or "",
            )
            result.report_paths = report_paths
            primary = (
                report_paths.get("xlsx")
                or report_paths.get("tasks_csv")
                or ""
            )
            if primary:
                format_repo.update_run_report(self.db, run_id, primary)
                result.summary.report_path = primary
        return result

    # ── 历史 / 差异 / 重跑 ────────────────────────────────

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        return format_repo.list_format_runs(self.db, limit)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return format_repo.get_format_run(self.db, run_id)

    def run_tasks(self, run_id: str) -> list[dict[str, Any]]:
        return format_repo.run_task_results(self.db, run_id)

    def run_issues(self, run_id: str) -> list[dict[str, Any]]:
        return format_repo.run_issues(self.db, run_id)

    def diff_runs(self, old_run_id: str, new_run_id: str) -> dict[str, Any]:
        old = format_repo.get_format_run(self.db, old_run_id)
        new = format_repo.get_format_run(self.db, new_run_id)
        if old is None:
            raise ValueError("旧检查记录不存在")
        if new is None:
            raise ValueError("新检查记录不存在")
        old_snapshot = format_repo.run_task_snapshot(self.db, old_run_id)
        new_snapshot = format_repo.run_task_snapshot(self.db, new_run_id)
        diff = diff_task_results(old_snapshot, new_snapshot)
        diff["old_run_id"] = old_run_id
        diff["new_run_id"] = new_run_id
        diff["old_started_at"] = old.get("started_at")
        diff["new_started_at"] = new.get("started_at")
        return diff

    def rerun(
        self,
        run_id: str,
        *,
        only_anomalies: bool = False,
        operator: str = "",
        write_report: bool = True,
        progress: ProgressCallback | None = None,
        should_cancel: CancelCallback | None = None,
    ) -> FormatCheckResult:
        run = self.get_run(run_id)
        if run is None:
            raise ValueError("检查记录不存在")
        snapshot = _json_loads(run.get("rule_snapshot_json"), {})
        template: Template | None = None
        if isinstance(snapshot, dict) and snapshot:
            try:
                template = Template.from_dict(snapshot)
            except (TypeError, ValueError):
                template = None
        if template is None:
            template, _ = self._resolve_template(run.get("template_id") or "")
        request = FormatCheckRequest(
            root_path=run["root_path"],
            template_id=template.id,
            batch_id=run.get("batch_id"),
            recursive=template.scan.recursive,
            only_anomalies=only_anomalies,
            previous_run_id=run_id,
            operator=operator or run.get("operator") or "",
            write_report=write_report,
            report_dir=str(self.report_dir) if self.report_dir else None,
        )
        return self.run_check(
            request,
            progress=progress,
            should_cancel=should_cancel,
            diff_with_previous=True,
            batch_name=os.path.basename(run["root_path"]),
        )

    def report_directory(self) -> str:
        return self._report_dir()

    def _report_dir(self) -> str:
        return str(self.report_dir) if self.report_dir else _default_report_dir()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "FormatCheckerService":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
