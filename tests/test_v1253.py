"""v1.25.3 测试：TXT 配对、原生翻译、安全写入、检查、费用、报告与导入。

运行：
    .\\python\\python.exe tests\\test_v1253.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.database.connection import open_database  # noqa: E402
from modules.precheck.cost import compute_cost, parse_float  # noqa: E402
from modules.precheck.errors import PrecheckError  # noqa: E402
from modules.precheck.models import (  # noqa: E402
    STATUS_EMPTY_EN,
    STATUS_EMPTY_ZH,
    STATUS_MISSING_EN,
    STATUS_NO_ZH,
    STATUS_TRANSLATED,
    PairingOptions,
    ProviderProfile,
    TranslationRequest,
)
from modules.precheck.pairing import derive_en_path, scan_pairs  # noqa: E402
from modules.precheck.prompts import default_prompt  # noqa: E402
from modules.precheck.providers import build_provider  # noqa: E402
from modules.precheck.providers.ark import ArkProvider  # noqa: E402
from modules.precheck.providers.base import ChatProvider, ChatResponse  # noqa: E402
from modules.precheck.providers.dashscope import DashScopeProvider  # noqa: E402
from modules.precheck.report import guard_report_dir  # noqa: E402
from modules.precheck.report_import import build_import  # noqa: E402
from modules.precheck.safety import read_text_file, write_translation  # noqa: E402
from modules.precheck.secrets import SecretsStore, mask_key  # noqa: E402
from modules.precheck.service import PrecheckService  # noqa: E402


class FakeProvider(ChatProvider):
    def __init__(
        self,
        translate_reply: str = "Hello world",
        check_reply: str = '{"passed": true, "issues": [], "suggestion": ""}',
        fail_times: int = 0,
    ):
        self.translate_reply = translate_reply
        self.check_reply = check_reply
        self.fail_times = fail_times
        self.calls = 0

    def _reply(self) -> str:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise PrecheckError("PC-104", "触发限流", retryable=True)
        return self.check_reply

    def chat(self, messages, *, model="", temperature=None, max_tokens=None):
        system = ""
        if messages:
            system = str(messages[0].get("content") or "")
        if "翻译" in system and "校" not in system and "对照" not in system:
            self.calls += 1
            if self.calls <= self.fail_times:
                raise PrecheckError("PC-104", "触发限流", retryable=True)
            text = self.translate_reply
        else:
            text = self._reply()
        return ChatResponse(
            text=text,
            model="fake",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            endpoint="fake://endpoint",
            latency_ms=1,
        )


def make_task(root: Path, task_id: str, *, zh: str | None = "中文指令", en: str | None = None) -> Path:
    task = root / task_id
    task.mkdir(parents=True)
    (task / f"{task_id}.jpg").write_bytes(b"jpg")
    if zh is not None:
        (task / f"{task_id}_CH.txt").write_text(zh, encoding="utf-8")
    if en is not None:
        (task / f"{task_id}_EN.txt").write_text(en, encoding="utf-8")
    return task


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def fake_factory(provider: FakeProvider):
    return lambda profile, key: provider


class PrecheckCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "root"
        self.root.mkdir()
        self.data = base / "data"
        self.provider = FakeProvider()
        self.service = PrecheckService(
            data_dir=self.data,
            provider_factory=fake_factory(self.provider),
            backoff_base=0.0,
        )
        self.service.save_profile(
            ProviderProfile(
                id="fake",
                name="Fake",
                provider="openai_compat",
                base_url="http://fake.local/v1",
                model="fake-model",
                input_price=1.0,
                output_price=2.0,
                unit_tokens=1_000_000,
                max_retries=2,
                concurrency=2,
            )
        )
        self.service.set_active_profile("fake")

    def tearDown(self) -> None:
        try:
            self.service.close()
        finally:
            self.temp.cleanup()

    def translate(self, **kwargs):
        request = TranslationRequest(
            root_path=str(self.root), profile_id="fake", **kwargs
        )
        return self.service.translate(request, api_key="test-key")


class TestSchema(unittest.TestCase):
    def test_schema_version_is_8_and_tables_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = open_database(Path(tmp) / "management.db")
            try:
                self.assertGreaterEqual(db.schema_version(), 8)
                tables = {
                    row["name"]
                    for row in db.connect().execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                for table in (
                    "translation_jobs",
                    "translation_items",
                    "translation_usage",
                ):
                    self.assertIn(table, tables)
            finally:
                db.close()


class TestPairing(unittest.TestCase):
    def test_pair_statuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_task(root, "10000001", zh="中文一")
            make_task(root, "10000002", zh="中文二", en="")
            make_task(root, "10000003", zh="中文三", en="English")
            make_task(root, "10000004", zh="", en="English")
            make_task(root, "10000005", zh=None)
            result = scan_pairs(str(root))
            status = {item.task_id: item.status for item in result.items}
            self.assertEqual(status["10000001"], STATUS_MISSING_EN)
            self.assertEqual(status["10000002"], STATUS_EMPTY_EN)
            self.assertEqual(status["10000003"], STATUS_TRANSLATED)
            self.assertEqual(status["10000004"], STATUS_EMPTY_ZH)
            self.assertEqual(status["10000005"], STATUS_NO_ZH)
            self.assertFalse((root / "10000001" / "10000001_EN.txt").exists())

    def test_derive_en_path(self):
        options = PairingOptions()
        self.assertEqual(
            os.path.basename(derive_en_path(r"D:\root\12345678\12345678_CH.txt", options)),
            "12345678_EN.txt",
        )
        self.assertEqual(
            os.path.basename(derive_en_path(r"D:\root\12345678\12345678_ZH.txt", options)),
            "12345678_EN.txt",
        )

    def test_recursive_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "batch_1"
            parent.mkdir()
            make_task(parent, "20000001", zh="中文")
            result = scan_pairs(str(root), PairingOptions(recursive=True))
            self.assertEqual(len(result.items), 1)

    def test_scan_without_loading_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_task(root, "20000002", zh="中文")
            make_task(root, "20000003", zh="中文", en="English")
            result = scan_pairs(str(root), PairingOptions(load_text=False))
            status = {item.task_id: item.status for item in result.items}
            self.assertEqual(status["20000002"], STATUS_MISSING_EN)
            self.assertEqual(status["20000003"], STATUS_TRANSLATED)


class TestTranslationAndSafety(PrecheckCase):
    def test_translate_preview_does_not_write_source(self):
        make_task(self.root, "30000001", zh="红色背景")
        before = snapshot(self.root)
        run = self.translate()
        self.assertEqual(run.status, "done")
        self.assertEqual(len(run.translated), 1)
        self.assertEqual(run.results[0].english, "Hello world")
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse((self.root / "30000001" / "30000001_EN.txt").exists())
        staging = self.service._staging_path(run.job_id)
        self.assertTrue(staging.is_file())
        payload = json.loads(staging.read_text(encoding="utf-8"))
        self.assertEqual(payload["items"][0]["english"], "Hello world")
        self.assertEqual(self.service.job_items(run.job_id)[0]["en_text"], "Hello world")
        usage = self.service.job_usage(run.job_id)
        self.assertEqual(len(usage), 1)
        self.assertEqual(usage[0]["token_total"], 15)
        self.assertGreater(usage[0]["cost"], 0)

    def test_only_missing_skips_existing_english(self):
        make_task(self.root, "30000002", zh="中文", en="Existing")
        run = self.translate(only_missing=True)
        self.assertEqual(run.status, "done")
        self.assertEqual(len(run.skipped), 1)
        self.assertEqual(run.results[0].error_message, "已有英文，按“仅翻译缺失”跳过")

    def test_write_backup_and_no_overwrite(self):
        task = make_task(self.root, "30000003", zh="中文", en="Existing")
        before = snapshot(self.root)
        run = self.translate(only_missing=False)
        self.assertEqual(len(run.translated), 1)
        self.assertEqual((task / "30000003_EN.txt").read_text(encoding="utf-8"), "Existing")

        refused = self.service.write_results(run.job_id, overwrite=False)
        self.assertEqual(refused["written"], 0)
        self.assertEqual(refused["failed"], 1)
        self.assertEqual(refused["rows"][0]["error_code"], "PC-201")
        self.assertEqual((task / "30000003_EN.txt").read_text(encoding="utf-8"), "Existing")
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(list((self.data / "backups").rglob("*.txt")), [])

        written = self.service.write_results(run.job_id, overwrite=True)
        self.assertEqual(written["written"], 1)
        self.assertEqual(
            (task / "30000003_EN.txt").read_text(encoding="utf-8"), "Hello world"
        )
        backups = list((self.data / "backups").rglob("30000003_EN.txt"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "Existing")

    def test_write_creates_missing_file_and_preserves_newline(self):
        task = make_task(self.root, "30000004", zh="第一行\r\n第二行\r\n")
        run = self.translate()
        outcome = self.service.write_results(run.job_id)
        self.assertEqual(outcome["written"], 1)
        text = (task / "30000004_EN.txt").read_bytes().decode("utf-8")
        self.assertIn("\r\n", text)

    def test_retry_then_success(self):
        self.provider = FakeProvider(fail_times=2)
        self.service.provider_factory = fake_factory(self.provider)
        make_task(self.root, "30000005", zh="中文")
        run = self.translate()
        self.assertEqual(run.status, "done")
        self.assertEqual(len(run.translated), 1)
        self.assertEqual(run.translated[0].attempts, 3)

    def test_cancel_before_start(self):
        make_task(self.root, "30000006", zh="中文")
        request = TranslationRequest(root_path=str(self.root), profile_id="fake")
        run = self.service.translate(
            request, api_key="test-key", should_cancel=lambda: True
        )
        self.assertTrue(run.cancelled)
        self.assertEqual(run.status, "cancelled")
        self.assertEqual(self.service.job_usage(run.job_id)[0]["total"], 0)

    def test_write_same_source_path_rejected(self):
        task = make_task(self.root, "30000008", zh="中文")
        zh_path = task / "30000008_CH.txt"
        with self.assertRaises(PrecheckError) as ctx:
            write_translation(
                str(zh_path),
                str(zh_path),
                "Hello",
                source_root=str(self.root),
                backup_root=str(self.data / "backups"),
                job_id="manual",
                overwrite=True,
            )
        self.assertEqual(ctx.exception.code, "PC-202")
        self.assertEqual(zh_path.read_text(encoding="utf-8"), "中文")

    def test_write_outside_root_rejected(self):
        make_task(self.root, "30000007", zh="中文")
        outside = Path(self.temp.name) / "outside_EN.txt"
        with self.assertRaises(PrecheckError) as ctx:
            write_translation(
                str(self.root / "30000007" / "30000007_CH.txt"),
                str(outside),
                "Hello",
                source_root=str(self.root),
                backup_root=str(self.data / "backups"),
                job_id="manual",
            )
        self.assertEqual(ctx.exception.code, "PC-202")
        self.assertFalse(outside.exists())


class TestChecks(PrecheckCase):
    def test_grammar_check_and_apply_suggestion(self):
        self.provider = FakeProvider(
            check_reply='{"passed": false, "issues": ["拼写"], "suggestion": "Hello there."}'
        )
        self.service.provider_factory = fake_factory(self.provider)
        task = make_task(self.root, "40000001", zh="中文")
        run = self.translate()
        self.service.write_results(run.job_id)
        check = self.service.check(run.job_id, "grammar", api_key="test-key")
        self.assertEqual(check.status, "done")
        self.assertEqual(check.failed, 1)
        self.assertEqual(check.results[0].issues, ["拼写"])
        self.assertEqual(check.results[0].suggestion, "Hello there.")

        item = self.service.job_items(run.job_id)[0]
        self.assertEqual(item["grammar_checked"], 1)
        self.assertEqual(item["grammar_passed"], 0)
        outcome = self.service.apply_suggestion(item["id"], "Hello there.")
        self.assertTrue(outcome["backup_path"])
        self.assertEqual(
            (task / "40000001_EN.txt").read_text(encoding="utf-8"), "Hello there."
        )

    def test_unparsable_check_is_suggestion_only(self):
        self.provider = FakeProvider(check_reply="not json")
        self.service.provider_factory = fake_factory(self.provider)
        make_task(self.root, "40000002", zh="中文")
        run = self.translate()
        check = self.service.check(run.job_id, "consistency", api_key="test-key")
        self.assertEqual(check.errored, 1)
        self.assertEqual(check.results[0].error_code, "PC-303")
        self.assertFalse((self.root / "40000002" / "40000002_EN.txt").exists())


class TestCostReportImport(PrecheckCase):
    def test_cost_math(self):
        profile = ProviderProfile(
            id="x", name="x", input_price=1.0, output_price=2.0, unit_tokens=1_000_000
        )
        self.assertAlmostEqual(compute_cost(profile, 1_000_000, 1_000_000), 3.0)
        self.assertAlmostEqual(compute_cost(profile, 500_000, 0), 0.5)
        self.assertEqual(parse_float("¥1,234.50"), 1234.5)
        self.assertIsNone(parse_float("abc"))

    def test_export_reports_and_guard(self):
        make_task(self.root, "50000001", zh="中文")
        run = self.translate()
        out = Path(self.temp.name) / "reports"
        paths = self.service.export_report(run.job_id, str(out), formats=("xlsx", "csv"))
        self.assertTrue(Path(paths["xlsx"]).is_file())
        self.assertTrue(Path(paths["translation_csv"]).is_file())
        with self.assertRaises(ValueError):
            guard_report_dir(str(self.root / "reports"), str(self.root))
        with self.assertRaises(ValueError):
            self.service.export_report(
                run.job_id, str(self.root / "reports"), formats=("csv",)
            )

    def test_import_translation_report_idempotent(self):
        imports = Path(self.temp.name) / "imports"
        imports.mkdir()
        report = imports / "translation_report_20260101.csv"
        report.write_text(
            "所在文件夹,任务ID,状态,中文指令,英文指令,Token消耗,预估费用(元),翻译提示词\n"
            r"D:\root\50000002,50000002,成功,中文,English,100,0.01,p1" "\n"
            r"D:\root\50000003,50000003,失败,中文2,,50,0.02,p1" "\n",
            encoding="utf-8-sig",
        )
        before = snapshot(imports)
        first = self.service.import_external_report(str(report))
        self.assertEqual(first["kind"], "translation")
        self.assertEqual(first["imported"], 2)
        self.assertEqual(first["skipped"], 0)
        second = self.service.import_external_report(str(report))
        self.assertEqual(second["imported"], 0)
        self.assertEqual(second["skipped"], 2)
        self.assertEqual(second["usage_inserted"], 0)
        self.assertEqual(second["usage_skipped"], 1)
        self.assertEqual(snapshot(imports), before)

    def test_import_cost_report(self):
        report = Path(self.temp.name) / "cost_report_20260101.csv"
        report.write_text(
            "日期,时间,API类型,endpoint,总任务数,成功数,失败数,"
            "Token消耗(输入),Token消耗(输出),Token消耗(总计),预估费用(元),备注\n"
            "2026-01-01,10:00:00,ark,https://x/v1,2,1,1,100,50,150,0.03,test\n",
            encoding="utf-8-sig",
        )
        result = build_import(str(report))
        self.assertEqual(result.kind, "cost")
        self.assertEqual(len(result.usages), 1)
        self.assertEqual(result.usages[0]["token_total"], 150)
        outcome = self.service.import_external_report(str(report))
        self.assertEqual(outcome["usage_inserted"], 1)
        again = self.service.import_external_report(str(report))
        self.assertEqual(again["usage_skipped"], 1)

    def test_import_unknown_status(self):
        report = Path(self.temp.name) / "translation_report_unknown.csv"
        report.write_text(
            "所在文件夹,任务ID,状态,中文指令,英文指令\n"
            r"D:\root\x,x,未知状态,中文,English" "\n",
            encoding="utf-8-sig",
        )
        result = build_import(str(report))
        self.assertEqual(result.items[0]["status"], "unknown:未知状态")


class TestSettingsAndSecrets(PrecheckCase):
    def test_default_profiles_and_prompt_reset(self):
        ids = {profile.id for profile in self.service.list_profiles()}
        self.assertTrue({"ark", "dashscope", "openai_compat"}.issubset(ids))
        changed = self.service.save_prompt("translate", "sys {zh_text}", "user {zh_text}")
        self.assertEqual(changed.source, "custom")
        self.assertNotEqual(changed.version, default_prompt("translate").version)
        reset = self.service.reset_prompt("translate")
        self.assertEqual(reset.source, "builtin")

    def test_builtin_profile_delete_rejected(self):
        with self.assertRaises(ValueError):
            self.service.delete_profile("ark")

    def test_provider_classes(self):
        base = dict(name="x", base_url="http://x/v1", model="m")
        ark = ProviderProfile(id="ark", provider="ark", **base)
        dash = ProviderProfile(id="dash", provider="dashscope", **base)
        compat = ProviderProfile(id="c", provider="openai_compat", **base)
        self.assertIsInstance(build_provider(ark, "k"), ArkProvider)
        self.assertIsInstance(build_provider(dash, "k"), DashScopeProvider)
        self.assertEqual(build_provider(compat, "k").name, "openai_compat")

    def test_mask_key(self):
        self.assertEqual(mask_key(""), "")
        self.assertEqual(mask_key("1234"), "****")
        self.assertNotIn("abcdefgh", mask_key("abcdefghijklmnop"))

    def test_dpapi_roundtrip_when_available(self):
        store = SecretsStore(self.data)
        if not store.available():
            self.skipTest("当前环境不支持 DPAPI")
        store.set_key("fake", "sk-test-1234567890")
        self.assertEqual(store.get_key("fake"), "sk-test-1234567890")
        raw = store.path.read_text(encoding="utf-8")
        self.assertNotIn("sk-test-1234567890", raw)
        self.assertIn("preview", raw)
        store.delete_key("fake")
        self.assertFalse(store.has_key("fake"))


class TestTextSafety(unittest.TestCase):
    def test_read_gbk_utf16_and_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            gbk = Path(tmp) / "gbk.txt"
            gbk.write_bytes("中文".encode("gbk"))
            text, encoding = read_text_file(str(gbk))
            self.assertEqual(text, "中文")
            self.assertEqual(encoding, "gb18030")

            utf16 = Path(tmp) / "utf16.txt"
            utf16.write_bytes("中文".encode("utf-16"))
            text, encoding = read_text_file(str(utf16))
            self.assertEqual(text, "中文")
            self.assertEqual(encoding, "utf-16")

            bom = Path(tmp) / "bom.txt"
            bom.write_bytes(b"\xef\xbb\xbfhello")
            text, encoding = read_text_file(str(bom))
            self.assertEqual(text, "hello")
            self.assertEqual(encoding, "utf-8-sig")


if __name__ == "__main__":
    unittest.main(verbosity=2)
