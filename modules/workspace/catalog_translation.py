"""批次翻译对接本机 8765 HTTP API（v1.25.11 P5）。

边界：
- 只处理 `03-申请终验`（final_acceptance）批次；
- 预览只调用只读扫描接口，不修改任何源文件；
- 用户确认后才调用 `/api/translate`，并在调用前把现有英文 TXT 备份到管理数据目录；
- 英文 TXT 的实际写入由 8765 工具完成，本平台不直接覆盖。
"""
from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from modules.common.path_guard import normalize_path
from modules.common.timeutil import now_iso
from modules.config import app_config
from modules.database.connection import Database
from modules.database.repositories.precheck_repo import get_setting, set_setting
from modules.precheck.secrets import SecretsError, SecretsStore, mask_key, redact
from modules.workspace.catalog_service import (
    TRANSLATION_FAILED,
    TRANSLATION_NOT_TRANSLATED,
    TRANSLATION_TRANSLATED,
    CatalogService,
)

SETTINGS_KEY = "catalog_translation_8765"
SECRET_ID = "catalog_translation_8765"
DEFAULT_BASE_URL = "http://localhost:8765"
DEFAULT_API_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.7-plus"
DEFAULT_PROVIDER = "dashscope"
DEFAULT_CH_SUFFIX = "_CH.txt"
DEFAULT_EN_SUFFIX = "_EN.txt"
PROVIDERS = {
    "dashscope": "阿里云百炼 / 千问",
    "ark": "豆包 Ark",
    "custom_openai": "自定义 OpenAI 兼容接口",
}
MODES = {"skip": "跳过已有英文（缺才翻）", "all": "全部重新翻译"}


class TranslationError(Exception):
    """翻译对接错误；message 不包含 API Key。"""


@dataclass
class TranslationSettings:
    base_url: str = DEFAULT_BASE_URL
    api_base_url: str = DEFAULT_API_BASE_URL
    model: str = DEFAULT_MODEL
    provider: str = DEFAULT_PROVIDER
    id_digits: int = 8
    ch_suffix: str = DEFAULT_CH_SUFFIX
    en_suffix: str = DEFAULT_EN_SUFFIX
    mode: str = "skip"
    concurrency: int = 15
    thinking: str = "low"
    prompt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "api_base_url": self.api_base_url,
            "model": self.model,
            "provider": self.provider,
            "id_digits": self.id_digits,
            "ch_suffix": self.ch_suffix,
            "en_suffix": self.en_suffix,
            "mode": self.mode,
            "concurrency": self.concurrency,
            "thinking": self.thinking,
            "prompt": self.prompt,
        }


@dataclass
class FolderPlan:
    number: str
    relative_path: str
    has_ch: bool = False
    has_en: bool = False
    action: str = "translate"
    message: str = ""

    @property
    def sends(self) -> bool:
        return self.action in {"translate", "retranslate"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "relative_path": self.relative_path,
            "has_ch": self.has_ch,
            "has_en": self.has_en,
            "action": self.action,
            "message": self.message,
        }


@dataclass
class TranslationPreview:
    batch_id: str
    batch_name: str = ""
    batch_path: str = ""
    folders: list[FolderPlan] = field(default_factory=list)
    message: str = ""

    @property
    def total(self) -> int:
        return len(self.folders)

    @property
    def to_translate(self) -> int:
        return sum(1 for item in self.folders if item.sends)

    @property
    def missing_ch(self) -> int:
        return sum(1 for item in self.folders if item.action == "missing_ch")

    @property
    def skipped(self) -> int:
        return sum(1 for item in self.folders if item.action == "skip")

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "batch_name": self.batch_name,
            "batch_path": self.batch_path,
            "total": self.total,
            "to_translate": self.to_translate,
            "missing_ch": self.missing_ch,
            "skipped": self.skipped,
            "message": self.message,
            "folders": [item.to_dict() for item in self.folders],
        }


@dataclass
class TranslationItem:
    number: str
    action: str = ""
    status: str = ""
    message: str = ""
    en_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "action": self.action,
            "status": self.status,
            "message": self.message,
            "en_path": self.en_path,
        }


@dataclass
class TranslationResult:
    batch_id: str
    batch_name: str = ""
    total: int = 0
    translated: int = 0
    skipped: int = 0
    failed: int = 0
    status: str = TRANSLATION_NOT_TRANSLATED
    message: str = ""
    backup_dir: str = ""
    run_id: str = ""
    items: list[TranslationItem] = field(default_factory=list)
    tokens: int = 0
    cost: float = 0.0

    @property
    def applied(self) -> bool:
        return self.translated > 0 and self.failed == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "batch_name": self.batch_name,
            "total": self.total,
            "translated": self.translated,
            "skipped": self.skipped,
            "failed": self.failed,
            "status": self.status,
            "message": self.message,
            "backup_dir": self.backup_dir,
            "run_id": self.run_id,
            "tokens": self.tokens,
            "cost": self.cost,
            "items": [item.to_dict() for item in self.items],
        }


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def load_settings(db: Database) -> TranslationSettings:
    raw = get_setting(db, SETTINGS_KEY, "") or ""
    data: dict[str, Any] = {}
    if raw.strip():
        try:
            value = json.loads(raw)
            if isinstance(value, dict):
                data = value
        except ValueError:
            data = {}
    return TranslationSettings(
        base_url=str(data.get("base_url") or DEFAULT_BASE_URL).rstrip("/"),
        api_base_url=str(data.get("api_base_url") or DEFAULT_API_BASE_URL),
        model=str(data.get("model") or DEFAULT_MODEL),
        provider=str(data.get("provider") or DEFAULT_PROVIDER),
        id_digits=int(data.get("id_digits") or 8),
        ch_suffix=str(data.get("ch_suffix") or DEFAULT_CH_SUFFIX),
        en_suffix=str(data.get("en_suffix") or DEFAULT_EN_SUFFIX),
        mode=str(data.get("mode") or "skip"),
        concurrency=int(data.get("concurrency") or 15),
        thinking=str(data.get("thinking") or "low"),
        prompt=str(data.get("prompt") or ""),
    )


def save_settings(db: Database, settings: TranslationSettings) -> None:
    set_setting(
        db,
        SETTINGS_KEY,
        json.dumps(settings.to_dict(), ensure_ascii=False),
    )


def secrets_store() -> SecretsStore:
    return SecretsStore(app_config.get_data_dir())


def has_api_key() -> bool:
    try:
        return secrets_store().has_key(SECRET_ID)
    except SecretsError:
        return False


def api_key_preview() -> str:
    try:
        store = secrets_store()
        return mask_key(store.get_key(SECRET_ID)) if store.has_key(SECRET_ID) else ""
    except SecretsError:
        return ""


def save_api_key(key: str) -> None:
    secrets_store().set_key(SECRET_ID, key)


def delete_api_key() -> None:
    store = secrets_store()
    if store.has_key(SECRET_ID):
        store.delete_key(SECRET_ID)


def load_api_key() -> str:
    store = secrets_store()
    return store.get_key(SECRET_ID) if store.has_key(SECRET_ID) else ""


def _request(
    base_url: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 30,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    data = None
    method = "GET"
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        method = "POST"
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            detail = ""
        raise TranslationError(f"8765 返回 HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise TranslationError(
            f"无法连接 8765 工具（{base_url}）：{exc.reason}"
        ) from exc
    except TimeoutError as exc:
        raise TranslationError("8765 工具响应超时") from exc
    try:
        result = json.loads(raw.decode("utf-8", "replace"))
    except ValueError as exc:
        raise TranslationError("8765 返回内容不是有效 JSON") from exc
    if not isinstance(result, dict):
        raise TranslationError("8765 返回格式不正确")
    return result


def health(base_url: str) -> dict[str, Any]:
    return _request(base_url, "/health", timeout=5)


def scan_folders(
    base_url: str,
    batch_path: str,
    settings: TranslationSettings,
    *,
    timeout: float = 60,
) -> list[dict[str, Any]]:
    payload = {
        "path": batch_path,
        "source_type": "folder",
        "id_digits": int(settings.id_digits),
        "id_regex": rf"\d{{{int(settings.id_digits)}}}",
        "ch_suffix": settings.ch_suffix,
        "en_suffix": settings.en_suffix,
    }
    data = _request(base_url, "/api/scan", payload, timeout=timeout)
    if not data.get("success"):
        raise TranslationError(str(data.get("error") or "扫描失败"))
    folders = data.get("folders")
    if folders is None:
        folders = []
    if not isinstance(folders, list):
        raise TranslationError("8765 扫描结果格式不正确")
    return [item for item in folders if isinstance(item, dict)]


def _plan_folder(folder: dict[str, Any], mode: str) -> FolderPlan:
    name = str(folder.get("name") or folder.get("path") or "")
    plan = FolderPlan(
        number=name,
        relative_path=str(folder.get("path") or name),
        has_ch=bool(folder.get("has_ch")),
        has_en=bool(folder.get("has_en")),
    )
    if not plan.has_ch:
        plan.action = "missing_ch"
        plan.message = "缺少中文 TXT"
    elif plan.has_en and mode == "skip":
        plan.action = "skip"
        plan.message = "已有英文 TXT，跳过"
    else:
        plan.action = "retranslate" if plan.has_en else "translate"
        plan.message = "重新翻译" if plan.has_en else "待翻译"
    return plan


def preview_batch(db: Database, batch_id: str, *, settings: TranslationSettings | None = None) -> TranslationPreview:
    service = CatalogService(db)
    batch = service.batch(batch_id)
    if not batch:
        raise TranslationError("批次不存在")
    settings = settings or load_settings(db)
    folders = scan_folders(
        settings.base_url,
        str(batch.get("batch_path") or ""),
        settings,
    )
    plans = [_plan_folder(item, settings.mode) for item in folders]
    preview = TranslationPreview(
        batch_id=batch_id,
        batch_name=str(batch.get("name") or ""),
        batch_path=str(batch.get("batch_path") or ""),
        folders=plans,
    )
    if not plans:
        preview.message = "批次下没有可翻译的编号文件夹"
    elif not any(item.has_ch for item in plans):
        preview.message = "所有编号文件夹都缺少中文 TXT，无法翻译"
    return preview


def _selected_plans(preview: TranslationPreview, numbers: Iterable[str] | None) -> list[FolderPlan]:
    if not numbers:
        return list(preview.folders)
    wanted = {str(item) for item in numbers}
    return [item for item in preview.folders if item.number in wanted]


def _backup_dir(batch_id: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = app_config.get_data_dir() / "backups" / "translation" / batch_id / stamp
    target.mkdir(parents=True, exist_ok=True)
    return str(target)


def backup_english_files(
    batch_path: str,
    plans: Iterable[FolderPlan],
    en_suffix: str,
    *,
    batch_id: str,
) -> str:
    """把即将被覆盖的英文 TXT 复制到管理数据目录；失败则抛错并阻止翻译。"""
    targets: list[tuple[str, str]] = []
    root = normalize_path(batch_path)
    for plan in plans:
        if not plan.has_en:
            continue
        folder = normalize_path(os.path.join(root, plan.relative_path))
        if not os.path.isdir(folder):
            continue
        for entry in os.scandir(folder):
            if not entry.is_file(follow_symlinks=False):
                continue
            if not entry.name.casefold().endswith(en_suffix.casefold()):
                continue
            targets.append((entry.path, os.path.join(plan.relative_path, entry.name)))
    if not targets:
        return ""
    destination = _backup_dir(batch_id)
    for source, relative in targets:
        out = os.path.join(destination, relative)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copy2(source, out)
    return destination


def translate_batch(
    db: Database,
    batch_id: str,
    numbers: Iterable[str] | None = None,
    *,
    operator: str = "",
    timeout: float = 1800,
) -> TranslationResult:
    """确认后翻译一个批次。调用前自动备份已有英文 TXT。"""
    service = CatalogService(db)
    batch = service.batch(batch_id)
    if not batch:
        raise TranslationError("批次不存在")
    if str(batch.get("source_type") or "") != "final_acceptance":
        raise TranslationError("只允许翻译 03-申请终验 批次")
    settings = load_settings(db)
    key = load_api_key()
    if not key:
        raise TranslationError("还没有保存 8765 使用的 API Key")
    preview = preview_batch(db, batch_id, settings=settings)
    plans = _selected_plans(preview, numbers)
    sends = [item for item in plans if item.sends]
    result = TranslationResult(
        batch_id=batch_id,
        batch_name=preview.batch_name,
        total=len(plans),
        skipped=sum(1 for item in plans if item.action == "skip"),
    )
    if not sends:
        result.message = "没有需要翻译的编号（缺少中文 TXT 或已跳过）"
        for item in plans:
            result.items.append(
                TranslationItem(
                    number=item.number,
                    action=item.action,
                    status="skipped",
                    message=item.message,
                )
            )
        return result

    try:
        result.backup_dir = backup_english_files(
            preview.batch_path, sends, settings.en_suffix, batch_id=batch_id
        )
    except OSError as exc:
        raise TranslationError(f"英文 TXT 备份失败，已停止翻译：{exc}") from exc
    run_id = _new_id("ctr")
    started_at = now_iso()
    job_id = f"velkoz_{datetime.now():%Y%m%d_%H%M%S}_{batch_id[:8]}"
    folders = [
        {"path": item.relative_path, "name": item.number}
        for item in sends
    ]
    payload = {
        "api_key": key,
        "endpoint": settings.model,
        "model": settings.model,
        "provider": settings.provider,
        "base_url": settings.api_base_url,
        "job_id": job_id,
        "root_dir": preview.batch_path,
        "folders": folders,
        "source_type": "folder",
        "id_digits": int(settings.id_digits),
        "id_regex": rf"\d{{{int(settings.id_digits)}}}",
        "ch_suffix": settings.ch_suffix,
        "en_suffix": settings.en_suffix,
        "reasoning_effort": settings.thinking,
        "concurrency": int(settings.concurrency),
        "prompt": settings.prompt,
        "options": {"mode": "all" if any(item.action == "retranslate" for item in sends) else "skip"},
    }
    try:
        data = _request(settings.base_url, "/api/translate", payload, timeout=timeout)
    except TranslationError as exc:
        message = redact(str(exc), key)
        result.failed = len(sends)
        result.status = TRANSLATION_FAILED
        result.message = message
        for item in sends:
            result.items.append(
                TranslationItem(
                    number=item.number,
                    action=item.action,
                    status="failed",
                    message=message,
                )
            )
        _save_result(db, batch, result, run_id, operator, started_at)
        return result

    response_results = data.get("results")
    if not isinstance(response_results, list):
        response_results = []
    by_folder: dict[str, dict[str, Any]] = {}
    for item in response_results:
        if not isinstance(item, dict):
            continue
        folder_key = str(item.get("folder") or "").replace("\\", "/").strip("/")
        name = folder_key.split("/")[-1] if folder_key else ""
        if name:
            by_folder.setdefault(name, item)
        file_name = str(item.get("file") or "")
        for plan in sends:
            if file_name.startswith(plan.number):
                by_folder.setdefault(plan.number, item)
    for plan in sends:
        response = by_folder.get(plan.number)
        if response is None:
            result.failed += 1
            result.items.append(
                TranslationItem(
                    number=plan.number,
                    action=plan.action,
                    status="failed",
                    message="8765 没有返回该编号结果",
                )
            )
            continue
        status = str(response.get("status") or "")
        tokens = int(response.get("tokens") or 0)
        cost = float(response.get("cost") or 0)
        result.tokens += tokens
        result.cost += cost
        en_path = os.path.join(
            preview.batch_path, plan.relative_path, f"{plan.number}{settings.en_suffix}"
        )
        if status == "success":
            result.translated += 1
            result.items.append(
                TranslationItem(
                    number=plan.number,
                    action=plan.action,
                    status="success",
                    message=str(response.get("reason") or ""),
                    en_path=en_path,
                )
            )
        else:
            result.failed += 1
            result.items.append(
                TranslationItem(
                    number=plan.number,
                    action=plan.action,
                    status="failed",
                    message=str(response.get("reason") or response.get("error") or "翻译失败"),
                    en_path=en_path,
                )
            )
    if result.failed:
        result.status = TRANSLATION_FAILED
        result.message = (
            f"翻译完成：成功 {result.translated}，失败 {result.failed}"
            + (f"；备份目录：{result.backup_dir}" if result.backup_dir else "")
        )
    elif result.translated:
        result.status = TRANSLATION_TRANSLATED
        result.message = (
            f"翻译完成：成功 {result.translated}"
            + (f"，跳过 {result.skipped}" if result.skipped else "")
            + (f"；备份目录：{result.backup_dir}" if result.backup_dir else "")
        )
    else:
        result.message = str(data.get("message") or "没有需要翻译的编号")
    _save_result(db, batch, result, run_id, operator, started_at)
    return result


def _save_result(
    db: Database,
    batch: dict[str, Any],
    result: TranslationResult,
    run_id: str,
    operator: str,
    started_at: str,
) -> None:
    conn = db.connect()
    now = now_iso()
    old_status = str(batch.get("translation_status") or TRANSLATION_NOT_TRANSLATED)
    settings = load_settings(db)
    conn.execute(
        """
        INSERT INTO catalog_translation_runs(
            id, batch_id, endpoint, model, total, translated, skipped, failed,
            status, message, backup_dir, operator, started_at, finished_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            result.batch_id,
            settings.base_url,
            settings.model,
            result.total,
            result.translated,
            result.skipped,
            result.failed,
            result.status,
            result.message,
            result.backup_dir,
            operator,
            started_at,
            now,
        ),
    )
    for item in result.items:
        conn.execute(
            """
            INSERT INTO catalog_translation_items(
                id, run_id, batch_id, number, action, status, message, en_path, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _new_id("cti"),
                run_id,
                result.batch_id,
                item.number,
                item.action,
                item.status,
                item.message,
                item.en_path,
                now,
            ),
        )
    if result.status != TRANSLATION_NOT_TRANSLATED:
        conn.execute(
            "UPDATE catalog_batches SET translation_status = ?, updated_at = ? WHERE id = ?",
            (result.status, now, result.batch_id),
        )
        conn.execute(
            """
            INSERT INTO catalog_status_log(
                id, batch_id, kind, from_status, to_status, operator, reason, created_at
            ) VALUES (?, ?, 'translation', ?, ?, ?, ?, ?)
            """,
            (
                _new_id("clog"),
                result.batch_id,
                old_status,
                result.status,
                operator,
                result.message,
                now,
            ),
        )
    conn.commit()
    result.run_id = run_id


def latest_run(db: Database, batch_id: str) -> dict[str, Any] | None:
    row = db.connect().execute(
        "SELECT * FROM catalog_translation_runs WHERE batch_id = ? ORDER BY finished_at DESC LIMIT 1",
        (batch_id,),
    ).fetchone()
    return dict(row) if row else None


def run_items(db: Database, run_id: str) -> list[dict[str, Any]]:
    rows = db.connect().execute(
        "SELECT * FROM catalog_translation_items WHERE run_id = ? ORDER BY number",
        (run_id,),
    ).fetchall()
    return [dict(row) for row in rows]
