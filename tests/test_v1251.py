"""v1.25.1 测试：候选确认、忽略、合并/拆分、扫描差异。

运行：
    .\\python\\python.exe tests\\test_v1251.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.batch_management.service import BatchService  # noqa: E402
from modules.database.connection import open_database  # noqa: E402
from modules.scanning.rules import builtin_ruleset  # noqa: E402
from modules.scanning.service import ScanService  # noqa: E402


def make_batch(root: Path, batch_name: str, task_ids: list[str]) -> Path:
    batch = root / "团队A" / "2026-11-07" / batch_name
    for task_id in task_ids:
        task = batch / task_id
        task.mkdir(parents=True)
        (task / f"{task_id}.jpg").write_bytes(b"fake-jpg")
        (task / f"{task_id}_CH.txt").write_text("中文", encoding="utf-8")
        (task / f"{task_id}_EN.txt").write_text("English", encoding="utf-8")
    return batch


def make_root(base: Path) -> Path:
    root = base / "data_root"
    make_batch(root, "batch_20261107_001", ["11111111", "22222222"])
    make_batch(root, "batch_20261107_002", ["33333333"])
    return root


def snapshot(root: Path) -> dict[str, tuple[int, int, int]]:
    items: dict[str, tuple[int, int, int]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            st = path.stat()
            items[str(path.relative_to(root))] = (st.st_size, st.st_mtime_ns, st.st_mode)
    return items


def initialise(base: Path):
    root = make_root(base)
    db_path = base / "management.db"
    scan = ScanService(db_path)
    batch = BatchService(db_path)
    return root, scan, batch


class TestBatchConfirm(unittest.TestCase):
    def test_schema_version_is_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = open_database(Path(tmp) / "management.db")
            try:
                self.assertEqual(db.schema_version(), 4)
                tables = {
                    row["name"]
                    for row in db.connect().execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                for table in ("batches", "sub_batches", "data_groups", "data_group_files", "batch_status_log"):
                    self.assertIn(table, tables)
                columns = {
                    row["name"]
                    for row in db.connect().execute("PRAGMA table_info(scan_candidates)")
                }
                self.assertIn("group_paths_json", columns)
                self.assertIn("notes", columns)
            finally:
                db.close()

    def test_confirm_creates_batch_and_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, scan, batch = initialise(Path(tmp))
            try:
                result = scan.preview(str(root), builtin_ruleset())
                self.assertEqual(len(result.candidates), 2)
                run_id = scan.save_result(result, operator="tester")
                candidates = batch.list_candidates(run_id)
                self.assertEqual(len(candidates), 2)
                self.assertTrue(all(item.get("group_paths_json") for item in candidates))

                target = next(item for item in candidates if item["group_count"] == 2)
                batch_id = batch.confirm_candidate(
                    target["id"],
                    operator="tester",
                    name="自定义批次",
                )
                self.assertTrue(batch_id.startswith("batch_"))

                batches = batch.list_batches()
                self.assertEqual(len(batches), 1)
                self.assertEqual(batches[0]["name"], "自定义批次")
                self.assertEqual(batches[0]["actual_group_count"], 2)
                self.assertEqual(batches[0]["source"], "scan")
                self.assertEqual(batches[0]["status"], "confirmed")

                groups = batch.batch_groups(batch_id)
                self.assertEqual(len(groups), 2)
                self.assertTrue(all(item["image_count"] >= 1 for item in groups))

                again = batch.get_candidate(target["id"])
                self.assertEqual(again["status"], "confirmed")
                self.assertEqual(again["confirmed_batch_id"], batch_id)

                # 重复确认应幂等
                self.assertEqual(batch.confirm_candidate(target["id"], operator="tester"), batch_id)
                self.assertEqual(len(batch.list_batches()), 1)

                files = batch.db.connect().execute("SELECT COUNT(*) AS c FROM data_group_files").fetchone()["c"]
                self.assertGreaterEqual(files, 6)

                logs = batch.db.connect().execute(
                    "SELECT COUNT(*) AS c FROM operation_logs WHERE module = 'batch_management'"
                ).fetchone()["c"]
                self.assertGreaterEqual(logs, 1)
            finally:
                scan.close()
                batch.close()

    def test_confirmed_batch_has_rule_version_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, scan, batch = initialise(Path(tmp))
            try:
                ruleset = scan.default_ruleset()
                result = scan.preview(str(root), ruleset)
                self.assertTrue(result.ruleset_json)
                run_id = scan.save_result(result)
                run = scan.db.connect().execute(
                    "SELECT rule_set_id, rule_version_id FROM scan_runs WHERE id = ?",
                    (run_id,),
                ).fetchone()
                self.assertEqual(run["rule_set_id"], ruleset.id)
                self.assertTrue(run["rule_version_id"])

                target = batch.list_candidates(run_id)[0]
                batch_id = batch.confirm_candidate(target["id"], operator="tester")
                stored = batch.db.connect().execute(
                    "SELECT rule_set_id, rule_version_id FROM batches WHERE id = ?",
                    (batch_id,),
                ).fetchone()
                self.assertEqual(stored["rule_set_id"], run["rule_set_id"])
                self.assertEqual(stored["rule_version_id"], run["rule_version_id"])
                version = batch.db.connect().execute(
                    "SELECT config_json FROM scan_rule_versions WHERE id = ?",
                    (stored["rule_version_id"],),
                ).fetchone()
                self.assertTrue(version["config_json"])
            finally:
                scan.close()
                batch.close()

    def test_confirm_does_not_modify_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, scan, batch = initialise(Path(tmp))
            try:
                result = scan.preview(str(root), builtin_ruleset())
                run_id = scan.save_result(result)
                before = snapshot(root)
                target = batch.list_candidates(run_id)[0]
                batch.confirm_candidate(target["id"], operator="tester")
                after = snapshot(root)
                self.assertEqual(before, after, "确认批次不应修改源目录")
            finally:
                scan.close()
                batch.close()

    def test_confirm_selected_groups_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, scan, batch = initialise(Path(tmp))
            try:
                result = scan.preview(str(root), builtin_ruleset())
                run_id = scan.save_result(result)
                target = next(item for item in batch.list_candidates(run_id) if item["group_count"] == 2)
                groups = batch.candidate_groups(target["id"])
                self.assertEqual(len(groups), 2)
                batch_id = batch.confirm_candidate(
                    target["id"],
                    selected_group_paths=[groups[0]["path"]],
                )
                stored = batch.batch_groups(batch_id)
                self.assertEqual(len(stored), 1)
                self.assertEqual(stored[0]["normalized_path"].casefold(), groups[0]["path"].casefold())
            finally:
                scan.close()
                batch.close()

    def test_ignore_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, scan, batch = initialise(Path(tmp))
            try:
                result = scan.preview(str(root), builtin_ruleset())
                run_id = scan.save_result(result)
                target = batch.list_candidates(run_id)[0]
                batch.ignore_candidate(target["id"], operator="tester", reason="重复批次")
                after = batch.get_candidate(target["id"])
                self.assertEqual(after["status"], "ignored")
                self.assertEqual(after["notes"], "重复批次")
                with self.assertRaises(ValueError):
                    batch.confirm_candidate(target["id"], operator="tester")
            finally:
                scan.close()
                batch.close()

    def test_update_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, scan, batch = initialise(Path(tmp))
            try:
                result = scan.preview(str(root), builtin_ruleset())
                run_id = scan.save_result(result)
                target = batch.list_candidates(run_id)[0]
                batch.update_candidate(
                    target["id"],
                    operator="tester",
                    name="新名称",
                    identity={"team": "团队B"},
                    notes="人工校正",
                    status="abnormal",
                )
                after = batch.get_candidate(target["id"])
                self.assertEqual(after["batch_name"], "新名称")
                self.assertEqual(after["notes"], "人工校正")
                self.assertEqual(after["status"], "abnormal")
                identity = json.loads(after["identity_json"])
                self.assertEqual(identity["team"], "团队B")
                with self.assertRaises(ValueError):
                    batch.update_candidate(target["id"], operator="tester", status="confirmed")
            finally:
                scan.close()
                batch.close()

    def test_merge_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, scan, batch = initialise(Path(tmp))
            try:
                result = scan.preview(str(root), builtin_ruleset())
                run_id = scan.save_result(result)
                candidates = batch.list_candidates(run_id)
                self.assertEqual(len(candidates), 2)
                batch_id = batch.merge_candidates(
                    [item["id"] for item in candidates],
                    operator="tester",
                    name="合并批次",
                )
                batches = batch.list_batches()
                self.assertEqual(len(batches), 1)
                self.assertEqual(batches[0]["name"], "合并批次")
                self.assertEqual(batches[0]["source"], "merge")
                self.assertEqual(batches[0]["actual_group_count"], 3)
                for item in batch.list_candidates(run_id):
                    self.assertEqual(item["status"], "confirmed")
                    self.assertEqual(item["confirmed_batch_id"], batch_id)
            finally:
                scan.close()
                batch.close()

    def test_split_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, scan, batch = initialise(Path(tmp))
            try:
                result = scan.preview(str(root), builtin_ruleset())
                run_id = scan.save_result(result)
                target = next(item for item in batch.list_candidates(run_id) if item["group_count"] == 2)
                groups = batch.candidate_groups(target["id"])
                outcome = batch.split_candidate(
                    target["id"],
                    [groups[0]["path"]],
                    operator="tester",
                    name="拆分批次",
                )
                self.assertTrue(outcome["batch_id"].startswith("batch_"))
                self.assertTrue(outcome["new_candidate_id"].startswith("cand"))

                stored = batch.batch_groups(outcome["batch_id"])
                self.assertEqual(len(stored), 1)
                self.assertEqual(stored[0]["normalized_path"].casefold(), groups[0]["path"].casefold())

                old = batch.get_candidate(target["id"])
                self.assertEqual(old["status"], "confirmed")
                remaining = batch.get_candidate(outcome["new_candidate_id"])
                self.assertEqual(remaining["status"], "pending")
                self.assertEqual(remaining["group_count"], 1)
                remaining_groups = batch.candidate_groups(remaining["id"])
                self.assertEqual(len(remaining_groups), 1)
                self.assertEqual(remaining_groups[0]["path"].casefold(), groups[1]["path"].casefold())

                with self.assertRaises(ValueError):
                    batch.split_candidate(target["id"], [groups[1]["path"]], operator="tester")
            finally:
                scan.close()
                batch.close()

    def test_diff_runs_detects_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, scan, batch = initialise(Path(tmp))
            try:
                first = scan.preview(str(root), builtin_ruleset())
                old_run = scan.save_result(first, operator="tester")

                # 修改一个数据组：新增一张图片
                group_path = Path(first.groups[0].path)
                (group_path / f"{first.groups[0].task_id}_1.jpg").write_bytes(b"new-jpg")
                # 删除另一个数据组
                removed_group = Path(first.groups[-1].path)
                for item in list(removed_group.iterdir()):
                    item.unlink()
                removed_group.rmdir()

                second = scan.preview(str(root), builtin_ruleset())
                new_run = scan.save_result(second, operator="tester")

                diff = batch.diff_runs(old_run, new_run)
                self.assertEqual(diff["old_run_id"], old_run)
                self.assertEqual(diff["new_run_id"], new_run)
                self.assertEqual(len(diff["groups"]["removed"]), 1)
                self.assertEqual(
                    diff["groups"]["removed"][0].casefold(),
                    str(removed_group).casefold(),
                )
                modified_paths = {item["path"].casefold() for item in diff["groups"]["modified"]}
                self.assertIn(str(group_path).casefold(), modified_paths)

                with self.assertRaises(ValueError):
                    batch.diff_runs(old_run, "run_not_exist")
            finally:
                scan.close()
                batch.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
