"""v1.25.0 基础测试：规则、匹配、只读扫描、数据库与备份。

运行：
    .\\python\\python.exe tests\\test_v1250.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.common.path_guard import safe_join  # noqa: E402
from modules.common.timeutil import parse_date  # noqa: E402
from modules.database.backup import backup_database, verify_database  # noqa: E402
from modules.database.connection import open_database  # noqa: E402
from modules.scanning.matcher import match_path  # noqa: E402
from modules.scanning.rules import builtin_ruleset, ruleset_from_legacy  # noqa: E402
from modules.scanning.scanner import ScanOptions, scan_root  # noqa: E402
from modules.scanning.service import ScanService  # noqa: E402


def make_fixture(base: Path) -> Path:
    root = base / "data_root"
    batch = root / "团队A" / "2026-11-07" / "batch_20261107_001"
    task1 = batch / "12345678"
    task1.mkdir(parents=True)
    (task1 / "12345678.jpg").write_bytes(b"fake-jpg")
    (task1 / "12345678_CH.txt").write_text("中文", encoding="utf-8")
    (task1 / "12345678_EN.txt").write_text("English", encoding="utf-8")
    task2 = batch / "87654321"
    task2.mkdir(parents=True)
    (task2 / "87654321.png").write_bytes(b"fake-png")
    (task2 / "87654321_CH.txt").write_text("中文2", encoding="utf-8")
    (task2 / "87654321_EN.txt").write_text("English2", encoding="utf-8")
    return root


def snapshot(root: Path) -> dict[str, tuple[int, int, int]]:
    items: dict[str, tuple[int, int, int]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            st = path.stat()
            items[str(path.relative_to(root))] = (st.st_size, st.st_mtime_ns, st.st_mode)
    return items


class TestRulesAndMatcher(unittest.TestCase):
    def test_match_batch_and_task(self):
        ruleset = builtin_ruleset()
        result = match_path(["batch_20261107_001", "12345678"], ruleset)
        self.assertTrue(result.matched)
        self.assertEqual(result.assignment.get("task"), "12345678")
        self.assertEqual(result.assignment.get("batch"), "batch_20261107_001")
        self.assertGreaterEqual(result.confidence, 0.9)

    def test_required_task_missing(self):
        ruleset = builtin_ruleset()
        result = match_path(["not-a-task"], ruleset)
        self.assertFalse(result.matched)
        self.assertIn("task", result.required_missing)

    def test_legacy_rules_migration(self):
        legacy = {
            "folder_digit_length": 8,
            "image_slots": [{"stem_suffixes": [""]}, {"stem_suffixes": ["_result"]}],
            "text_slots": [{"suffixes": ["_CH"]}, {"suffixes": ["_EN"]}],
        }
        ruleset = ruleset_from_legacy(legacy)
        self.assertEqual(len(ruleset.image_slots), 2)
        self.assertTrue(match_path(["12345678"], ruleset).matched)
        self.assertFalse(match_path(["1234567"], ruleset).matched)

    def test_date_parse(self):
        self.assertEqual(parse_date("20261107"), "2026-11-07")
        self.assertEqual(parse_date("2026.11.07"), "2026-11-07")
        self.assertIsNone(parse_date("20261399"))


class TestReadOnlyScan(unittest.TestCase):
    def test_scan_fixture_is_readonly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_fixture(Path(tmp))
            before = snapshot(root)
            result = scan_root(
                str(root),
                builtin_ruleset(),
                options=ScanOptions(count_fingerprint=True),
            )
            after = snapshot(root)
            self.assertEqual(before, after, "扫描不应修改源目录")
            self.assertEqual(len(result.groups), 2)
            self.assertEqual(len(result.candidates), 1)
            self.assertEqual(result.candidates[0].group_count, 2)
            self.assertTrue(all(group.txt_zh for group in result.groups))
            self.assertTrue(all(group.txt_en for group in result.groups))

    def test_scan_missing_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = scan_root(str(Path(tmp) / "missing"), builtin_ruleset())
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.issues[0].code, "DR-001")

    def test_scan_no_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root" / "12345678"
            root.mkdir(parents=True)
            (root / "12345678_CH.txt").write_text("x", encoding="utf-8")
            result = scan_root(str(root.parent), builtin_ruleset())
            self.assertEqual(len(result.groups), 0)
            self.assertTrue(any(issue.code == "DR-009" for issue in result.issues))


class TestDatabaseAndService(unittest.TestCase):
    def test_migration_and_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "data" / "management.db"
            db = open_database(db_path)
            try:
                self.assertEqual(db.schema_version(), 1)
                ok, detail = db.integrity_check()
                self.assertTrue(ok, detail)
                tables = {
                    row["name"]
                    for row in db.connect().execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                self.assertIn("scan_runs", tables)
                self.assertIn("operation_logs", tables)
            finally:
                db.close()
            backup = backup_database(db_path, Path(tmp) / "backups", label="test")
            self.assertTrue(backup.is_file())
            ok, detail = verify_database(backup)
            self.assertTrue(ok, detail)

    def test_service_save_scan_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_fixture(Path(tmp))
            db_path = Path(tmp) / "management.db"
            service = ScanService(db_path)
            try:
                for item in service.list_rule_sets():
                    if item.get("id") == "legacy_scan_rules":
                        ruleset = service.get_rule_set(item["id"])
                        break
                else:
                    ruleset = service.default_ruleset()
                result = service.preview(str(root), ruleset)
                run_id = service.save_result(result, operator="tester")
                runs = service.recent_runs(5)
                self.assertEqual(len(runs), 1)
                self.assertEqual(runs[0]["id"], run_id)
                count = service.db.connect().execute("SELECT COUNT(*) AS c FROM scan_candidates").fetchone()["c"]
                self.assertEqual(count, len(result.candidates))
                log_count = service.db.connect().execute("SELECT COUNT(*) AS c FROM operation_logs").fetchone()["c"]
                self.assertGreaterEqual(log_count, 1)
            finally:
                service.close()


class TestPathGuard(unittest.TestCase):
    def test_safe_join_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                safe_join(tmp, "..", "escape")
            with self.assertRaises(ValueError):
                safe_join(tmp, str(Path(tmp) / "absolute"))
            self.assertTrue(safe_join(tmp, "ok").endswith("ok"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
