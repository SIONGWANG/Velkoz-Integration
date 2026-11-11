"""翻译与预审服务：配置、扫描、翻译、写入确认、检查、报告与导入。"""
from __future__ import annotations

import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Iterable

from modules.audit_log.service import AuditLogger
from modules.common.path_guard import normalize_path
from modules.common.timeutil import now_iso, today_str
from modules.config import app_config
from modules.database.connection import Database, open_database
from modules.database.repositories import precheck_repo
from modules.format_checker.id_rules import IdRule

from .cost import compute_cost, summarize_results, usage_row
from .errors import PrecheckError
from .models import (
    CheckResult,
    CheckRunResult,
    PairItem,
    PairingOptions,
    ProviderProfile,
    STATUS_EMPTY_EN,
    STATUS_EMPTY_ZH,
    STATUS_INVALID,
    STATUS_MISSING_EN,
    STATUS_NO_ZH,
    STATUS_TRANSLATED,
    TranslationRequest,
    TranslationResult,
    TranslationRunResult,
)
from .pairing import PairScanResult, derive_en_path, scan_pairs
from .prompts import PromptTemplate, all_prompts, build_messages, default_prompt, prompt_version
from .providers.base import ChatProvider
from .providers.registry import (
    PROVIDER_PRESETS,
    build_provider,
    default_profiles,
    validate_profile,
)
from .report import export_reports
from .report_import import build_import
from .safety import atomic_write_text, ensure_within_root, read_text_file, write_translation, word_count
from .secrets import SecretsError, SecretsStore

PROFILES_KEY = "precheck.profiles"
ACTIVE_PROFILE_KEY = "precheck.active_profile"
PROMPT_KEY = "precheck.prompt.{name}"
STAGING_DIR = "precheck_staging"

ProgressCallback = Callable[[int, int, str], None]
CancelCallback = Callable[[], bool]
ProviderFactory = Callable[[ProviderProfile, str], ChatProvider]


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4()}"


def _clean_translation(text: str) -> str:
    body = (text or "").strip()
    if body.startswith("```"):
        lines = body.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        body = "\n".join(lines).strip()
    for prefix in ("Translation:", "translation:", "英文：", "英文:", "英文译文：", "英文译文:"):
        if body.startswith(prefix):
            body = body[len(prefix):].lstrip()
            break
    pairs = {'"': '"', "'": "'", "“": "”", "‘": "’"}
    if len(body) >= 2 and body[0] in pairs and body[-1] == pairs[body[0]]:
        body = body[1:-1].strip()
    if not body:
        raise PrecheckError("PC-109", "模型返回的译文为空")
    return body


def _parse_check_json(text: str) -> tuple[bool | None, list[str], str, bool]:
    """解析语法/一致性 JSON；返回 (passed, issues, suggestion, parsed)。"""
    body = (text or "").strip()
    if body.startswith("```"):
        lines = body.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        body = "\n".join(lines).strip()
    start = body.find("{")
    end = body.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None, [], body[:2000], False
    try:
        data = json.loads(body[start : end + 1])
    except ValueError:
        return None, [], body[:2000], False
    if not isinstance(data, dict):
        return None, [], body[:2000], False
    passed_raw = data.get("passed")
    passed = bool(passed_raw) if passed_raw is not None else None
    issues: list[str] = []
    for item in data.get("issues") or []:
        if isinstance(item, str):
            issues.append(item)
        elif isinstance(item, dict):
            issues.append(str(item.get("message") or item.get("issue") or json.dumps(item, ensure_ascii=False)))
        else:
            issues.append(str(item))
    suggestion = str(data.get("suggestion") or "").strip()
    return passed, issues, suggestion, True


class PrecheckService:
    def __init__(
        self,
        db_path: str | Path | None = None,
        data_dir: str | Path | None = None,
        *,
        provider_factory: ProviderFactory | None = None,
        backoff_base: float = 0.6,
    ):
        resolved_data = Path(data_dir) if data_dir else app_config.get_data_dir()
        resolved_db = (
            Path(db_path)
            if db_path
            else app_config.get_db_path(resolved_data)
        )
        self.db: Database = open_database(resolved_db)
        self.data_dir = resolved_data
        self.secrets = SecretsStore(self.data_dir)
        self.report_root = self.data_dir / "reports" / "precheck"
        self.backup_root = str(self.data_dir / "backups")
        self.audit = AuditLogger(self.db)
        self.provider_factory = provider_factory or build_provider
        self.backoff_base = max(0.0, float(backoff_base))
        self._seed_profiles()

    # ── 配置 ─────────────────────────────────────────────
    def _seed_profiles(self) -> None:
        if not precheck_repo.get_setting(self.db, PROFILES_KEY):
            data = [profile.to_dict() for profile in default_profiles()]
            precheck_repo.set_setting(self.db, PROFILES_KEY, json.dumps(data, ensure_ascii=False))

    def _profile_rows(self) -> list[dict[str, Any]]:
        raw = precheck_repo.get_setting(self.db, PROFILES_KEY)
        try:
            rows = json.loads(raw) if raw else []
        except ValueError:
            rows = []
        if not isinstance(rows, list):
            rows = []
        by_id = {str(item.get("id")): item for item in rows if isinstance(item, dict)}
        changed = False
        for profile in default_profiles():
            if profile.id not in by_id:
                by_id[profile.id] = profile.to_dict()
                changed = True
        merged = list(by_id.values())
        if changed:
            precheck_repo.set_setting(
                self.db, PROFILES_KEY, json.dumps(merged, ensure_ascii=False)
            )
        return merged

    def list_profiles(self) -> list[ProviderProfile]:
        return [ProviderProfile.from_dict(row) for row in self._profile_rows()]

    def get_profile(self, profile_id: str) -> ProviderProfile | None:
        for row in self._profile_rows():
            if str(row.get("id")) == profile_id:
                return ProviderProfile.from_dict(row)
        return None

    def save_profile(self, profile: ProviderProfile) -> ProviderProfile:
        errors = validate_profile(profile)
        if errors:
            raise ValueError("；".join(errors))
        if not profile.id:
            profile.id = f"pf_{uuid.uuid4().hex[:8]}"
        rows = self._profile_rows()
        index = next(
            (i for i, row in enumerate(rows) if str(row.get("id")) == profile.id), None
        )
        if index is None:
            rows.append(profile.to_dict())
        else:
            rows[index] = profile.to_dict()
        precheck_repo.set_setting(
            self.db, PROFILES_KEY, json.dumps(rows, ensure_ascii=False)
        )
        return profile

    def delete_profile(self, profile_id: str) -> None:
        profile = self.get_profile(profile_id)
        if profile is None:
            raise ValueError("配置不存在")
        if profile.builtin:
            raise ValueError("内置配置不能删除；可修改后另存为新配置")
        rows = [row for row in self._profile_rows() if str(row.get("id")) != profile_id]
        precheck_repo.set_setting(
            self.db, PROFILES_KEY, json.dumps(rows, ensure_ascii=False)
        )
        try:
            self.secrets.delete_key(profile_id)
        except SecretsError:
            pass
        if self.active_profile_id() == profile_id:
            precheck_repo.delete_setting(self.db, ACTIVE_PROFILE_KEY)

    def active_profile_id(self) -> str:
        stored = precheck_repo.get_setting(self.db, ACTIVE_PROFILE_KEY) or ""
        profiles = self.list_profiles()
        ids = {profile.id for profile in profiles}
        if stored in ids:
            return stored
        return profiles[0].id if profiles else ""

    def set_active_profile(self, profile_id: str) -> None:
        if self.get_profile(profile_id) is None:
            raise ValueError("配置不存在")
        precheck_repo.set_setting(self.db, ACTIVE_PROFILE_KEY, profile_id)

    # ── API Key ──────────────────────────────────────────
    def save_api_key(self, profile_id: str, api_key: str) -> None:
        if self.get_profile(profile_id) is None:
            raise ValueError("配置不存在")
        self.secrets.set_key(profile_id, api_key)

    def has_api_key(self, profile_id: str) -> bool:
        try:
            return self.secrets.has_key(profile_id)
        except SecretsError:
            return False

    def api_key_preview(self, profile_id: str) -> str:
        try:
            return self.secrets.preview(profile_id)
        except SecretsError:
            return ""

    def clear_api_key(self, profile_id: str) -> None:
        self.secrets.delete_key(profile_id)

    def _api_key(self, profile_id: str, api_key: str | None = None) -> str:
        if api_key is not None:
            return api_key
        try:
            key = self.secrets.get_key(profile_id)
        except SecretsError as exc:
            raise PrecheckError("PC-101", str(exc)) from exc
        if not key:
            raise PrecheckError("PC-101", "未配置 API Key")
        return key

    def test_connection(
        self, profile_id: str, api_key: str | None = None
    ) -> dict[str, Any]:
        profile = self.get_profile(profile_id)
        if profile is None:
            raise ValueError("配置不存在")
        provider = self.provider_factory(profile, self._api_key(profile_id, api_key))
        response = provider.test_connection()
        return {
            "model": response.model or profile.model,
            "endpoint": response.endpoint,
            "latency_ms": response.latency_ms,
            "reply": response.text[:120],
            "token_input": response.prompt_tokens,
            "token_output": response.completion_tokens,
        }

    # ── 提示词 ───────────────────────────────────────────
    def prompt(self, name: str) -> PromptTemplate:
        try:
            builtin = default_prompt(name)
        except KeyError as exc:
            raise ValueError(str(exc)) from exc
        raw = precheck_repo.get_setting(self.db, PROMPT_KEY.format(name=name))
        if not raw:
            return builtin
        try:
            data = json.loads(raw)
        except ValueError:
            return builtin
        if not isinstance(data, dict):
            return builtin
        return PromptTemplate(
            name=name,
            system=str(data.get("system") or builtin.system),
            user=str(data.get("user") or builtin.user),
            base_version=builtin.base_version,
            source="custom",
        )

    def prompts(self) -> list[PromptTemplate]:
        return [self.prompt(name) for name in ("translate", "grammar", "consistency")]

    def save_prompt(self, name: str, system: str, user: str) -> PromptTemplate:
        builtin = default_prompt(name)
        if not (system or "").strip() or not (user or "").strip():
            raise ValueError("提示词内容不能为空")
        payload = {"system": system, "user": user, "saved_at": now_iso()}
        precheck_repo.set_setting(
            self.db, PROMPT_KEY.format(name=name), json.dumps(payload, ensure_ascii=False)
        )
        return self.prompt(name)

    def reset_prompt(self, name: str) -> PromptTemplate:
        precheck_repo.delete_setting(self.db, PROMPT_KEY.format(name=name))
        return self.prompt(name)

    # ── 扫码配对 ─────────────────────────────────────────
    def scan(
        self,
        root_path: str,
        options: PairingOptions | None = None,
        *,
        task_paths: Iterable[str] | None = None,
        progress: ProgressCallback | None = None,
        should_cancel: CancelCallback | None = None,
    ) -> PairScanResult:
        return scan_pairs(
            root_path,
            options,
            task_paths=set(task_paths) if task_paths else None,
            progress=progress,
            should_cancel=should_cancel,
        )

    def default_options(self, *, recursive: bool = False) -> PairingOptions:
        return PairingOptions(recursive=recursive, id_rule=IdRule())

    # ── 翻译（只调 API，不写源文件） ─────────────────────
    def translate(
        self,
        request: TranslationRequest,
        items: Iterable[PairItem] | None = None,
        *,
        prompt_name: str = "translate",
        api_key: str | None = None,
        progress: ProgressCallback | None = None,
        should_cancel: CancelCallback | None = None,
    ) -> TranslationRunResult:
        profile_id = request.profile_id or self.active_profile_id()
        profile = self.get_profile(profile_id)
        if profile is None:
            raise PrecheckError("PC-102", "未选择模型配置")
        key = self._api_key(profile_id, api_key)
        provider = self.provider_factory(profile, key)
        prompt = self.prompt(prompt_name)

        if items is None:
            scan_result = self.scan(
                request.root_path,
                self.default_options(recursive=request.recursive),
                task_paths=request.task_paths or None,
            )
            source_items = list(scan_result.items)
        else:
            source_items = list(items)
        if request.task_paths:
            wanted = {os.path.normcase(normalize_path(p)) for p in request.task_paths}
            source_items = [
                item
                for item in source_items
                if os.path.normcase(normalize_path(item.folder_path)) in wanted
            ]
        if request.max_items:
            source_items = source_items[: int(request.max_items)]

        started_at = now_iso()
        job_id = precheck_repo.create_job(
            self.db,
            {
                "provider": profile.provider,
                "model": profile.model,
                "status": "running",
                "prompt_version": prompt.version,
                "started_at": started_at,
                "operator": request.operator,
                "root_path": normalize_path(request.root_path),
                "profile_id": profile.id,
                "batch_id": request.batch_id,
                "summary": {
                    "currency": profile.currency,
                    "concurrency": profile.concurrency,
                    "only_missing": request.only_missing,
                },
            },
        )

        results: list[TranslationResult] = []
        pending: list[PairItem] = []
        for item in source_items:
            if item.status == STATUS_NO_ZH:
                results.append(
                    TranslationResult(
                        item=item,
                        status="skipped",
                        error_code="PC-002",
                        error_message="缺少中文 TXT",
                    )
                )
            elif item.status in {STATUS_EMPTY_ZH, STATUS_INVALID}:
                results.append(
                    TranslationResult(
                        item=item,
                        status="skipped",
                        error_code=item.error_code or "PC-005",
                        error_message=item.error_message or "中文 TXT 不可用",
                    )
                )
            elif request.only_missing and item.status == STATUS_TRANSLATED:
                results.append(
                    TranslationResult(
                        item=item,
                        status="skipped",
                        error_message="已有英文，按“仅翻译缺失”跳过",
                    )
                )
            else:
                pending.append(item)

        total = len(results) + len(pending)
        cancelled = False
        endpoint = ""
        completed = len(results)
        if progress:
            progress(completed, max(total, 1), f"准备翻译 {len(pending)} 条")

        max_workers = max(1, min(int(profile.concurrency or 1), max(1, len(pending))))
        if pending:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {}
                for item in pending:
                    if should_cancel and should_cancel():
                        cancelled = True
                        break
                    futures[pool.submit(self._translate_one, provider, profile, prompt, item)] = item
                for future in as_completed(futures):
                    result = future.result()
                    results.append(result)
                    completed += 1
                    if result.token_input or result.token_output:
                        endpoint = endpoint or ""
                    if progress:
                        progress(
                            completed,
                            max(total, 1),
                            f"已处理 {completed}/{total}：{result.item.task_id}",
                        )
                    if should_cancel and should_cancel():
                        cancelled = True
                        for other in futures:
                            other.cancel()
                        break
        results.sort(key=lambda item: item.item.folder_path.casefold())
        if cancelled:
            status = "cancelled"
        elif any(item.status == "failed" for item in results):
            status = "partial"
        else:
            status = "done"
        finished_at = now_iso()
        summary = summarize_results(results)

        staged: list[dict[str, Any]] = []
        for result in results:
            row_id = _new_id("tri")
            precheck_repo.insert_item(
                self.db,
                {
                    "id": row_id,
                    "job_id": job_id,
                    "task_id": result.item.task_id,
                    "folder_path": result.item.folder_path,
                    "zh_txt_path": result.item.zh_txt_path,
                    "en_txt_path": result.item.en_txt_path
                    or (
                        derive_en_path(result.item.zh_txt_path, self.default_options())
                        if result.item.zh_txt_path
                        else ""
                    ),
                    "status": result.status,
                    "zh_length": result.item.zh_length,
                    "en_length": word_count(result.english)
                    if result.english
                    else result.item.en_length,
                    "model": profile.model,
                    "translated_at": finished_at if result.status == "translated" else "",
                    "error_code": result.error_code,
                    "error_message": result.error_message,
                    "token_input": result.token_input,
                    "token_output": result.token_output,
                    "cost": result.cost,
                },
            )
            staged.append(
                {
                    "item_id": row_id,
                    "task_id": result.item.task_id,
                    "folder_path": result.item.folder_path,
                    "zh_txt_path": result.item.zh_txt_path,
                    "en_txt_path": result.item.en_txt_path,
                    "zh_text": result.item.zh_text,
                    "english": result.english,
                    "status": result.status,
                    "error_code": result.error_code,
                    "error_message": result.error_message,
                }
            )

        self._save_staging(job_id, request, profile, prompt, staged)
        precheck_repo.update_job(
            self.db,
            job_id,
            status=status,
            finished_at=finished_at,
            summary={
                "currency": profile.currency,
                "concurrency": profile.concurrency,
                "only_missing": request.only_missing,
                "prompt": prompt.version,
                "counts": summary,
                "cancelled": cancelled,
            },
        )
        if request.batch_id:
            self._sync_batch_translation_status(request.batch_id, status, summary)
        precheck_repo.insert_usage(
            self.db,
            usage_row(
                job_id,
                profile=profile,
                results=results,
                endpoint=endpoint,
                note="翻译",
            ),
        )
        self.audit.log(
            "precheck",
            "translate",
            operator=request.operator,
            status="ok" if status == "done" else status,
            target_path=normalize_path(request.root_path),
            detail={
                "job_id": job_id,
                "provider": profile.provider,
                "model": profile.model,
                "counts": summary,
            },
            run_id=job_id,
            batch_id=request.batch_id or None,
        )
        return TranslationRunResult(
            job_id=job_id,
            results=results,
            started_at=started_at,
            finished_at=finished_at,
            status=status,
            token_input=summary["token_input"],
            token_output=summary["token_output"],
            cost=summary["cost"],
            currency=profile.currency,
            cancelled=cancelled,
            summary=summary,
        )

    def _sync_batch_translation_status(
        self, batch_id: str, status: str, summary: dict[str, Any]
    ) -> None:
        """把最近一次翻译结果同步到批次索引；真实明细仍以 translation_jobs/items 为准。"""
        failed = int(summary.get("failed", 0) or 0)
        translated = int(summary.get("translated", 0) or 0)
        if status in {"cancelled", "partial"} or failed > 0:
            value = "partial" if translated > 0 else "failed"
        elif status in {"failed", "fail"}:
            value = "failed"
        elif translated > 0:
            value = "completed"
        else:
            value = "in_progress"
        self.db.connect().execute(
            "UPDATE batches SET translation_status = ?, updated_at = ? WHERE id = ?",
            (value, now_iso(), batch_id),
        )
        self.db.connect().commit()

    def _translate_one(
        self,
        provider: ChatProvider,
        profile: ProviderProfile,
        prompt: PromptTemplate,
        item: PairItem,
    ) -> TranslationResult:
        started = time.perf_counter()
        result = TranslationResult(item=item)
        zh_text = item.zh_text
        if not zh_text and item.zh_txt_path:
            try:
                zh_text, _ = read_text_file(item.zh_txt_path)
                item.zh_text = zh_text
                item.zh_length = len("".join(zh_text.split()))
            except PrecheckError as exc:
                result.status = "failed"
                result.error_code = exc.code
                result.error_message = exc.message
                return result
        messages = build_messages(prompt, {"zh_text": zh_text})
        try:
            response, attempts = self._call_with_retries(
                provider, messages, profile, max_tokens=None
            )
            result.english = _clean_translation(response.text)
            result.token_input = response.prompt_tokens
            result.token_output = response.completion_tokens
            result.cost = compute_cost(profile, result.token_input, result.token_output)
            result.attempts = attempts
            result.status = "translated"
        except PrecheckError as exc:
            result.status = "failed"
            result.error_code = exc.code
            result.error_message = exc.message
        except Exception as exc:  # 未预期异常也不能中断整批
            result.status = "failed"
            result.error_code = "PC-108"
            result.error_message = f"翻译失败：{type(exc).__name__}"
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        return result

    def _call_with_retries(
        self,
        provider: ChatProvider,
        messages: list[dict[str, str]],
        profile: ProviderProfile,
        *,
        max_tokens: int | None = None,
    ) -> tuple[Any, int]:
        attempts = 0
        last: PrecheckError | None = None
        while attempts <= int(profile.max_retries or 0):
            attempts += 1
            try:
                response = provider.chat(
                    messages,
                    model=profile.model,
                    temperature=profile.temperature,
                    max_tokens=max_tokens,
                )
                return response, attempts
            except PrecheckError as exc:
                last = exc
                if not exc.retryable or attempts > int(profile.max_retries or 0):
                    break
                delay = (
                    exc.retry_after
                    if exc.retry_after is not None
                    else self.backoff_base * (2 ** (attempts - 1))
                )
                if delay > 0:
                    time.sleep(min(float(delay), 5.0))
        raise last or PrecheckError("PC-110", "重试次数用尽")

    # ── 译文暂存（管理数据目录，不写源目录） ─────────────
    def _staging_path(self, job_id: str) -> Path:
        return self.data_dir / "reports" / STAGING_DIR / f"{job_id}.json"

    def _save_staging(
        self,
        job_id: str,
        request: TranslationRequest,
        profile: ProviderProfile,
        prompt: PromptTemplate,
        items: list[dict[str, Any]],
    ) -> None:
        path = self._staging_path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "job_id": job_id,
            "root_path": normalize_path(request.root_path),
            "profile_id": profile.id,
            "provider": profile.provider,
            "model": profile.model,
            "prompt_version": prompt.version,
            "created_at": now_iso(),
            "items": items,
        }
        atomic_write_text(
            str(path), json.dumps(payload, ensure_ascii=False, indent=2), newline="\n"
        )

    def _load_staging(self, job_id: str) -> dict[str, Any] | None:
        path = self._staging_path(job_id)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _update_staging_item(self, job_id: str, item_id: str, **fields: Any) -> None:
        payload = self._load_staging(job_id)
        if not payload:
            return
        for item in payload.get("items") or []:
            if str(item.get("item_id")) == item_id:
                item.update(fields)
                break
        path = self._staging_path(job_id)
        atomic_write_text(
            str(path), json.dumps(payload, ensure_ascii=False, indent=2), newline="\n"
        )

    def delete_staging(self, job_id: str) -> bool:
        path = self._staging_path(job_id)
        if path.is_file():
            try:
                path.unlink()
                return True
            except OSError:
                return False
        return False

    # ── 用户确认后写英文 TXT ─────────────────────────────
    def write_results(
        self,
        job_id: str,
        *,
        task_paths: Iterable[str] | None = None,
        overwrite: bool = False,
        operator: str = "",
    ) -> dict[str, Any]:
        job = precheck_repo.get_job(self.db, job_id)
        if job is None:
            raise ValueError("任务不存在")
        root = job.get("root_path") or ""
        if not root or not os.path.isdir(root):
            raise PrecheckError("PC-000", "任务根目录不存在，无法写入")
        staging = self._load_staging(job_id)
        options = self.default_options()
        wanted = (
            {os.path.normcase(normalize_path(p)) for p in task_paths}
            if task_paths
            else None
        )
        rows: list[dict[str, Any]] = []
        if staging and staging.get("items"):
            entries = [
                item
                for item in staging["items"]
                if item.get("status") == "translated" and (item.get("english") or "").strip()
            ]
        else:
            entries = []
            for row in precheck_repo.list_items(self.db, job_id, statuses=["translated"]):
                entries.append(
                    {
                        "item_id": row["id"],
                        "task_id": row["task_id"],
                        "folder_path": row["folder_path"],
                        "zh_txt_path": row.get("zh_txt_path") or "",
                        "en_txt_path": row.get("en_txt_path") or "",
                        "english": "",
                        "status": row["status"],
                    }
                )
        for entry in entries:
            folder = entry.get("folder_path") or ""
            if wanted is not None and os.path.normcase(normalize_path(folder)) not in wanted:
                continue
            zh_path = entry.get("zh_txt_path") or ""
            en_path = entry.get("en_txt_path") or (
                derive_en_path(zh_path, options) if zh_path else ""
            )
            english = entry.get("english") or ""
            row = {
                "task_id": entry.get("task_id", ""),
                "folder_path": folder,
                "en_txt_path": en_path,
                "written": False,
                "backup_path": "",
                "error_code": "",
                "error_message": "",
            }
            if not english.strip():
                row["error_code"] = "PC-109"
                row["error_message"] = "译文暂存不存在，请重新翻译后再写入"
                rows.append(row)
                continue
            try:
                outcome = write_translation(
                    zh_path,
                    en_path,
                    english,
                    source_root=root,
                    backup_root=self.backup_root,
                    job_id=job_id,
                    overwrite=overwrite,
                )
                written_at = now_iso()
                row.update(
                    {
                        "written": True,
                        "backup_path": outcome["backup_path"],
                        "written_at": written_at,
                    }
                )
                item_id = entry.get("item_id") or ""
                if item_id:
                    precheck_repo.update_item(
                        self.db,
                        item_id,
                        written_at=written_at,
                        backup_path=outcome["backup_path"],
                        error_code="",
                        error_message="",
                    )
                    self._update_staging_item(
                        job_id, item_id, written_at=written_at
                    )
            except PrecheckError as exc:
                row["error_code"] = exc.code
                row["error_message"] = exc.message
            rows.append(row)

        written = sum(1 for row in rows if row["written"])
        failed = len(rows) - written
        summary = dict(job.get("summary") or {})
        summary["write"] = {
            "at": now_iso(),
            "written": written,
            "failed": failed,
            "overwrite": bool(overwrite),
        }
        precheck_repo.update_job(self.db, job_id, summary=summary)
        self.audit.log(
            "precheck",
            "write_english",
            operator=operator or job.get("operator") or "",
            status="ok" if failed == 0 else "partial",
            target_path=root,
            detail={"job_id": job_id, "written": written, "failed": failed},
            run_id=job_id,
        )
        return {
            "job_id": job_id,
            "written": written,
            "failed": failed,
            "rows": rows,
            "backup_root": str(Path(self.backup_root) / "precheck" / job_id),
        }

    # ── 语法 / 一致性检查 ────────────────────────────────
    def check(
        self,
        job_id: str,
        kind: str,
        *,
        profile_id: str = "",
        task_paths: Iterable[str] | None = None,
        api_key: str | None = None,
        progress: ProgressCallback | None = None,
        should_cancel: CancelCallback | None = None,
    ) -> CheckRunResult:
        if kind not in ("grammar", "consistency"):
            raise ValueError("未知检查类型")
        job = precheck_repo.get_job(self.db, job_id)
        if job is None:
            raise ValueError("任务不存在")
        profile_id = profile_id or job.get("profile_id") or self.active_profile_id()
        profile = self.get_profile(profile_id)
        if profile is None:
            raise PrecheckError("PC-102", "未选择模型配置")
        key = self._api_key(profile_id, api_key)
        provider = self.provider_factory(profile, key)
        prompt = self.prompt(kind)

        staging = self._load_staging(job_id)
        staged = {
            str(item.get("item_id")): item
            for item in (staging or {}).get("items", [])
            if item.get("item_id")
        }
        wanted = (
            {os.path.normcase(normalize_path(p)) for p in task_paths}
            if task_paths
            else None
        )
        rows = precheck_repo.list_items(self.db, job_id)
        candidates = []
        for row in rows:
            if wanted is not None and os.path.normcase(normalize_path(row["folder_path"])) not in wanted:
                continue
            entry = staged.get(str(row["id"]), {})
            en_text = (entry.get("english") or "").strip()
            if not en_text and row.get("en_txt_path") and os.path.isfile(row["en_txt_path"]):
                try:
                    en_text, _ = read_text_file(row["en_txt_path"])
                except PrecheckError:
                    en_text = ""
            zh_text = (entry.get("zh_text") or "").strip()
            if not zh_text and row.get("zh_txt_path") and os.path.isfile(row["zh_txt_path"]):
                try:
                    zh_text, _ = read_text_file(row["zh_txt_path"])
                except PrecheckError:
                    zh_text = ""
            if not en_text:
                continue
            if kind == "consistency" and not zh_text:
                continue
            candidates.append((row, en_text, zh_text))

        total = len(candidates)
        results: list[CheckResult] = []
        cancelled = False
        if progress:
            progress(0, max(total, 1), f"准备{ '语法' if kind == 'grammar' else '一致性' }检查 {total} 条")
        for index, (row, en_text, zh_text) in enumerate(candidates, start=1):
            if should_cancel and should_cancel():
                cancelled = True
                break
            result = self._check_one(provider, profile, prompt, kind, row, en_text, zh_text)
            results.append(result)
            precheck_repo.update_item(
                self.db,
                row["id"],
                grammar_checked=1 if kind == "grammar" else row.get("grammar_checked", 0),
                grammar_passed=(
                    int(bool(result.passed))
                    if kind == "grammar"
                    else row.get("grammar_passed", 0)
                ),
                consistency_checked=(
                    1 if kind == "consistency" else row.get("consistency_checked", 0)
                ),
                consistency_passed=(
                    int(bool(result.passed))
                    if kind == "consistency"
                    else row.get("consistency_passed", 0)
                ),
                suggestion=result.suggestion or row.get("suggestion") or "",
            )
            if progress:
                progress(index, max(total, 1), f"已检查 {index}/{total}：{row['task_id']}")

        token_in = sum(item.token_input for item in results)
        token_out = sum(item.token_output for item in results)
        cost_total = round(sum(item.cost for item in results), 8)
        status = "cancelled" if cancelled else ("done" if not any(item.error_code for item in results) else "partial")
        precheck_repo.insert_usage(
            self.db,
            {
                "job_id": job_id,
                "usage_date": today_str(),
                "usage_time": now_iso()[11:19],
                "api_type": profile.provider,
                "endpoint": "",
                "total": total,
                "success": sum(1 for item in results if item.passed is True),
                "failed": sum(1 for item in results if item.passed is False),
                "token_in": token_in,
                "token_out": token_out,
                "token_total": token_in + token_out,
                "cost": cost_total,
                "currency": profile.currency,
                "note": "语法检查" if kind == "grammar" else "一致性检查",
            },
        )
        summary = dict(job.get("summary") or {})
        checks = dict(summary.get("checks") or {})
        checks[kind] = {
            "at": now_iso(),
            "total": total,
            "passed": sum(1 for item in results if item.passed is True),
            "failed": sum(1 for item in results if item.passed is False),
            "errors": sum(1 for item in results if item.error_code),
            "prompt": prompt.version,
        }
        summary["checks"] = checks
        precheck_repo.update_job(self.db, job_id, summary=summary)
        self.audit.log(
            "precheck",
            f"check_{kind}",
            operator=job.get("operator") or "",
            status="ok" if status == "done" else status,
            target_path=job.get("root_path") or "",
            detail={"job_id": job_id, "total": total, "passed": checks[kind]["passed"]},
            run_id=job_id,
        )
        return CheckRunResult(
            job_id=job_id,
            kind=kind,
            results=results,
            token_input=token_in,
            token_output=token_out,
            cost=cost_total,
            status=status,
            cancelled=cancelled,
        )

    def _check_one(
        self,
        provider: ChatProvider,
        profile: ProviderProfile,
        prompt: PromptTemplate,
        kind: str,
        row: dict[str, Any],
        en_text: str,
        zh_text: str,
    ) -> CheckResult:
        started = time.perf_counter()
        result = CheckResult(
            task_id=row["task_id"],
            folder_path=row["folder_path"],
            kind=kind,
            original=en_text,
        )
        messages = build_messages(
            prompt, {"en_text": en_text, "zh_text": zh_text}
        )
        try:
            response, _ = self._call_with_retries(provider, messages, profile)
            passed, issues, suggestion, parsed = _parse_check_json(response.text)
            result.passed = passed
            result.issues = issues
            result.suggestion = suggestion
            result.raw = response.text[:4000]
            result.token_input = response.prompt_tokens
            result.token_output = response.completion_tokens
            result.cost = compute_cost(
                profile, result.token_input, result.token_output
            )
            if not parsed:
                result.error_code = "PC-303"
                result.error_message = "检查结果不是合法 JSON，请查看原始返回"
        except PrecheckError as exc:
            result.error_code = exc.code
            result.error_message = exc.message
        except Exception as exc:
            result.error_code = "PC-303"
            result.error_message = f"检查失败：{type(exc).__name__}"
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        return result

    def apply_suggestion(
        self,
        item_id: str,
        text: str,
        *,
        overwrite: bool = True,
        operator: str = "",
    ) -> dict[str, Any]:
        item = precheck_repo.get_item(self.db, item_id)
        if item is None:
            raise ValueError("条目不存在")
        job = precheck_repo.get_job(self.db, item["job_id"])
        if job is None:
            raise ValueError("任务不存在")
        root = job.get("root_path") or ""
        zh_path = item.get("zh_txt_path") or ""
        en_path = item.get("en_txt_path") or (
            derive_en_path(zh_path, self.default_options()) if zh_path else ""
        )
        if not en_path:
            raise PrecheckError("PC-202", "无法确定英文 TXT 路径")
        outcome = write_translation(
            zh_path,
            en_path,
            text,
            source_root=root,
            backup_root=self.backup_root,
            job_id=item["job_id"],
            overwrite=overwrite,
        )
        written_at = now_iso()
        precheck_repo.update_item(
            self.db,
            item_id,
            suggestion=text,
            manual_fixed=1,
            written_at=written_at,
            backup_path=outcome["backup_path"],
            error_code="",
            error_message="",
        )
        self._update_staging_item(item["job_id"], item_id, english=text, written_at=written_at)
        self.audit.log(
            "precheck",
            "apply_suggestion",
            operator=operator,
            target_path=en_path,
            detail={"job_id": item["job_id"], "item_id": item_id},
            run_id=item["job_id"],
        )
        return {
            "item_id": item_id,
            "path": outcome["path"],
            "backup_path": outcome["backup_path"],
            "written_at": written_at,
        }

    # ── 历史 / 报告 / 导入 ───────────────────────────────
    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        return precheck_repo.list_jobs(self.db, limit)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return precheck_repo.get_job(self.db, job_id)

    def job_items(self, job_id: str, *, include_text: bool = True) -> list[dict[str, Any]]:
        items = precheck_repo.list_items(self.db, job_id)
        if not include_text:
            return items
        staging = self._load_staging(job_id) or {}
        staged = {
            str(entry.get("item_id")): entry
            for entry in staging.get("items") or []
            if entry.get("item_id")
        }
        for item in items:
            item["zh_text"] = self._read_optional(item.get("zh_txt_path"))
            item["en_text"] = self._read_optional(item.get("en_txt_path"))
            entry = staged.get(str(item.get("id"))) or {}
            if not item["zh_text"]:
                item["zh_text"] = str(entry.get("zh_text") or "")
            if not item["en_text"]:
                item["en_text"] = str(entry.get("english") or "")
        return items

    def _read_optional(self, path: str | None) -> str:
        if not path or not os.path.isfile(path):
            return ""
        try:
            text, _ = read_text_file(path)
            return text
        except PrecheckError:
            return ""

    def job_usage(self, job_id: str) -> list[dict[str, Any]]:
        return precheck_repo.list_usage(self.db, job_id=job_id, limit=500)

    def list_usage(self, limit: int = 200) -> list[dict[str, Any]]:
        return precheck_repo.list_usage(self.db, limit=limit)

    def usage_summary(self) -> dict[str, Any]:
        return precheck_repo.usage_summary(self.db)

    def delete_job(self, job_id: str) -> None:
        precheck_repo.delete_job(self.db, job_id)
        self.delete_staging(job_id)

    def export_report(
        self,
        job_id: str,
        out_dir: str | None = None,
        *,
        formats: tuple[str, ...] = ("xlsx", "csv"),
        include_text: bool = True,
    ) -> dict[str, str]:
        job = precheck_repo.get_job(self.db, job_id)
        if job is None:
            raise ValueError("任务不存在")
        items = self.job_items(job_id, include_text=include_text)
        usage = self.job_usage(job_id)
        target = out_dir or str(self.report_root / today_str())
        summary = job.get("summary") or {}
        counts = summary.get("counts") or {}
        return export_reports(
            job,
            items,
            target,
            source_root=job.get("root_path") or "",
            usage_rows=usage,
            summary={**counts, "cost": sum(float(row.get("cost") or 0) for row in usage)},
            formats=formats,
            batch_name=os.path.basename(job.get("root_path") or ""),
        )

    def import_external_report(
        self, path: str, *, operator: str = ""
    ) -> dict[str, Any]:
        result = build_import(path)
        job_id = precheck_repo.create_job(
            self.db,
            {
                "provider": "external_report",
                "model": "",
                "status": "running",
                "started_at": now_iso(),
                "operator": operator,
                "source_report": path,
                "root_path": os.path.dirname(path),
                "note": "只读导入外部历史报告",
            },
        )
        imported = 0
        skipped = 0
        for item in result.items:
            row_hash = item.get("report_row_hash") or ""
            if row_hash and precheck_repo.item_hash_exists(self.db, row_hash):
                skipped += 1
                continue
            item["job_id"] = job_id
            precheck_repo.insert_item(self.db, item)
            imported += 1

        usage_inserted = 0
        usage_skipped = 0
        usage_rows: list[dict[str, Any]] = []
        if result.usage_row:
            usage_rows.append(result.usage_row)
        usage_rows.extend(result.usages)
        for row in usage_rows:
            row["job_id"] = job_id
            if precheck_repo.insert_usage(self.db, row):
                usage_inserted += 1
            else:
                usage_skipped += 1

        status = "done" if result.rows_ok else ("partial" if result.items else "failed")
        summary = {
            "import": {
                "kind": result.kind,
                "rows_total": result.rows_total,
                "rows_ok": result.rows_ok,
                "rows_error": len(result.errors),
                "imported": imported,
                "skipped": skipped,
                "usage_inserted": usage_inserted,
                "usage_skipped": usage_skipped,
                "errors": result.errors[:50],
                "unknown_columns": result.unknown_columns,
            }
        }
        precheck_repo.update_job(
            self.db,
            job_id,
            status=status,
            finished_at=now_iso(),
            summary=summary,
        )
        self.audit.log(
            "precheck",
            "import_report",
            operator=operator,
            status="ok" if status == "done" else status,
            source_path=path,
            detail={
                "job_id": job_id,
                "kind": result.kind,
                "rows_total": result.rows_total,
                "imported": imported,
                "skipped": skipped,
            },
            run_id=job_id,
        )
        return {
            "job_id": job_id,
            "kind": result.kind,
            "rows_total": result.rows_total,
            "rows_ok": result.rows_ok,
            "rows_error": len(result.errors),
            "imported": imported,
            "skipped": skipped,
            "usage_inserted": usage_inserted,
            "usage_skipped": usage_skipped,
            "errors": result.errors,
            "unknown_columns": result.unknown_columns,
        }

    # ── 收尾 ─────────────────────────────────────────────
    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "PrecheckService":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
