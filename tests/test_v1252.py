"""v1.25.2 测试：原生格式检查、模板、报告、历史与差异。

运行：
    .\\python\\python.exe tests\\test_v1252.py
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.database.connection import open_database  # noqa: E402
from modules.format_checker.checker import preview_path  # noqa: E402
from modules.format_checker.id_rules import IdMatcher, has_width_issue  # noqa: E402
from modules.format_checker.models import (  # noqa: E402
    FormatCheckRequest,
    IdRule,
    Template,
)
from modules.format_checker.report import guard_report_dir  # noqa: E402
from modules.format_checker.service import FormatCheckerService  # noqa: E402
from modules.format_checker.templates import (  # noqa: E402
    builtin_template,
    parse_template_json,
    validate_template,
)


def make_task(
    root: Path,
    task_id: str,
    files: dict[str, bytes | str],
    *,
    parent: Path | None = None,
) -> Path:
    task = (parent or root) / task_id
    task.mkdir(parents=True)
    for name, content in files.items():
        target = task / name
        if isinstance(content, str):
            target.write_text(content, encoding="utf-8")
        else:
            target.write_bytes(content or b"x")
    return task


def single_files(task_id: str, *, with_txt: bool = True) -> dict[str, bytes | str]:
    files: dict[str, bytes | str] = {f"{task_id}.jpg": b"jpg"}
    if with_txt:
        files[f"{task_id}_CH.txt"] = "中文"
        files[f"{task_id}_EN.txt"] = "English"
    return files


def snapshot(root: Path) -> dict[str, tuple[int, int, int]]:
    items: dict[str, tuple[int, int, int]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            st = path.stat()
            items[str(path.relative_to(root))] = (st.st_size, st.st_mtime_ns, st.st_mode)
    return items


def codes(result) -> list[str]:
    return [issue.code for issue in result.tasks[0].issues]


class TestSchemaAndRules(unittest.TestCase):
    def test_schema_version_is_8(self):
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
                    "format_templates",
                    "format_template_versions",
                    "format_check_runs",
                    "format_check_task_results",
                    "format_check_issues",
                ):
                    self.assertIn(table, tables)
            finally:
                db.close()

    def test_id_rules_digits_and_prefix(self):
        digits = IdMatcher(IdRule(mode="digits", length=8))
        self.assertTrue(digits.matches("12345678"))
        self.assertFalse(digits.matches("1234567"))
        self.assertFalse(digits.matches("123456789"))
        self.assertFalse(digits.matches("JD12345678"))

        prefixed = IdMatcher(IdRule(mode="prefix_digits", length=8, prefix="JD"))
        self.assertTrue(prefixed.matches("JD12345678"))
        self.assertTrue(prefixed.matches("jd12345678"))
        self.assertFalse(prefixed.matches("JD1234567"))
        self.assertFalse(IdMatcher(IdRule(mode="prefix_digits", length=8, prefix="JD", case_sensitive=True)).matches("jd12345678"))

    def test_width_issue(self):
        self.assertTrue(has_width_issue("12345678　"))
        self.assertTrue(has_width_issue("1234 5678"))
        self.assertTrue(has_width_issue("１２３４５６７８"))
        self.assertFalse(has_width_issue("12345678_CH"))

    def test_template_json_roundtrip(self):
        template = builtin_template()
        text = template.to_json()
        parsed, unknown = parse_template_json(text)
        self.assertEqual(unknown, [])
        self.assertEqual(parsed.id, "builtin_default")
        self.assertEqual(parsed.id_rule.length, 8)
        self.assertEqual(validate_template(parsed), [])

        data = template.to_dict()
        data["strange_field"] = 1
        parsed_unknown, unknown_fields = parse_template_json(json.dumps(data))
        self.assertEqual(unknown_fields, ["strange_field"])
        self.assertTrue(parsed_unknown)


class TestSingleMode(unittest.TestCase):
    def test_valid_single_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            task_id = "12345678"
            make_task(
                root,
                task_id,
                {
                    f"{task_id}.jpg": b"jpg",
                    f"{task_id}_result.png": b"png",
                    f"{task_id}.psd": b"psd",
                    f"{task_id}_CH.txt": "中文",
                    f"{task_id}_EN.txt": "English",
                },
            )
            result = preview_path(str(root), builtin_template())
            self.assertEqual(len(result.tasks), 1)
            task = result.tasks[0]
            self.assertEqual(task.result, "pass")
            self.assertEqual(task.detected_mode, "single")
            self.assertEqual(task.image_count, 2)
            self.assertEqual(task.txt_count, 2)
            self.assertEqual(task.issues, [])

    def test_missing_primary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            task_id = "12345678"
            make_task(root, task_id, {f"{task_id}_CH.txt": "中文"})
            result = preview_path(str(root), builtin_template())
            self.assertEqual(result.tasks[0].result, "fail")
            self.assertIn("FC-101", codes(result))

    def test_psd_only_is_missing_primary_not_wrong_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            task_id = "12345678"
            make_task(root, task_id, {f"{task_id}.psd": b"psd"})
            result = preview_path(str(root), builtin_template())
            self.assertIn("FC-101", codes(result))
            self.assertNotIn("FC-106", codes(result))

    def test_wrong_primary_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            task_id = "12345678"
            make_task(root, task_id, {f"{task_id}.bmp": b"bmp"})
            result = preview_path(str(root), builtin_template())
            self.assertIn("FC-106", codes(result))
            self.assertEqual(result.tasks[0].result, "fail")

    def test_duplicate_extension_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            task_id = "12345678"
            make_task(
                root,
                task_id,
                {f"{task_id}.jpg": b"jpg", f"{task_id}.png": b"png"},
            )
            result = preview_path(str(root), builtin_template())
            self.assertIn("FC-108", codes(result))
            self.assertEqual(result.tasks[0].result, "fail")

    def test_required_txt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            task_id = "12345678"
            make_task(root, task_id, single_files(task_id, with_txt=False))
            template = builtin_template()
            single = tuple(
                dataclasses.replace(rule, required=True) if rule.role == "zh" else rule
                for rule in template.single
            )
            template = dataclasses.replace(template, single=single)
            result = preview_path(str(root), template)
            self.assertIn("FC-104", codes(result))
            self.assertEqual(result.tasks[0].result, "fail")

    def test_id_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            make_task(
                root,
                "12345678",
                {"87654321.jpg": b"jpg", "12345678_CH.txt": "中文"},
            )
            result = preview_path(str(root), builtin_template())
            self.assertIn("FC-003", codes(result))


class TestMultiMode(unittest.TestCase):
    def test_valid_multi(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            task_id = "12345678"
            make_task(
                root,
                task_id,
                {f"{task_id}_{index}.jpg": b"jpg" for index in range(1, 5)},
            )
            result = preview_path(str(root), builtin_template())
            task = result.tasks[0]
            self.assertEqual(task.result, "pass")
            self.assertEqual(task.detected_mode, "multi")
            self.assertEqual(task.image_count, 4)

    def test_multi_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            task_id = "12345678"
            make_task(
                root,
                task_id,
                {f"{task_id}_1.jpg": b"jpg", f"{task_id}_3.jpg": b"jpg"},
            )
            result = preview_path(str(root), builtin_template())
            self.assertIn("FC-203", codes(result))

    def test_multi_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            task_id = "12345678"
            make_task(
                root,
                task_id,
                {f"{task_id}_1.jpg": b"jpg", f"{task_id}_1.png": b"png"},
            )
            result = preview_path(str(root), builtin_template())
            self.assertIn("FC-204", codes(result))

    def test_multi_too_many(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            task_id = "12345678"
            make_task(
                root,
                task_id,
                {f"{task_id}_{index}.jpg": b"jpg" for index in range(1, 6)},
            )
            result = preview_path(str(root), builtin_template())
            self.assertIn("FC-202", codes(result))

    def test_multi_below_min_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            task_id = "12345678"
            make_task(root, task_id, {f"{task_id}_1.jpg": b"jpg"})
            template = builtin_template()
            template = dataclasses.replace(
                template,
                modes=("multi",),
                multi=dataclasses.replace(template.multi, min_count=2),
            )
            result = preview_path(str(root), template)
            self.assertIn("FC-201", codes(result))

    def test_multi_allow_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            task_id = "12345678"
            make_task(
                root,
                task_id,
                {f"{task_id}_1.jpg": b"jpg", f"{task_id}_result.jpg": b"jpg"},
            )
            template = builtin_template()
            template = dataclasses.replace(
                template,
                modes=("multi",),
                multi=dataclasses.replace(template.multi, allow_result=True),
            )
            result = preview_path(str(root), template)
            task = result.tasks[0]
            self.assertEqual(task.image_count, 2)
            self.assertNotIn("FC-301", [issue.code for issue in task.issues])


class TestDualAndExtras(unittest.TestCase):
    def test_dual_single_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            task_id = "12345678"
            make_task(root, task_id, single_files(task_id))
            result = preview_path(str(root), builtin_template())
            self.assertEqual(result.tasks[0].result, "pass")
            self.assertEqual(result.tasks[0].detected_mode, "single")

    def test_dual_multi_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            task_id = "12345678"
            make_task(
                root,
                task_id,
                {f"{task_id}_{index}.jpg": b"jpg" for index in range(1, 3)},
            )
            result = preview_path(str(root), builtin_template())
            self.assertEqual(result.tasks[0].result, "pass")
            self.assertEqual(result.tasks[0].detected_mode, "multi")

    def test_dual_none_reports_fc002(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            task_id = "12345678"
            make_task(root, task_id, {f"{task_id}_notes.txt": "说明"})
            result = preview_path(str(root), builtin_template())
            self.assertIn("FC-002", codes(result))
            self.assertEqual(result.tasks[0].result, "fail")

    def test_mixed_single_and_multi_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            task_id = "12345678"
            make_task(
                root,
                task_id,
                {f"{task_id}.jpg": b"jpg", f"{task_id}_1.jpg": b"jpg"},
            )
            result = preview_path(str(root), builtin_template())
            task = result.tasks[0]
            self.assertEqual(task.detected_mode, "single")
            self.assertEqual(task.result, "warn")
            self.assertIn("FC-301", [issue.code for issue in task.issues])

    def test_extra_files_policies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            task_id = "12345678"
            files = single_files(task_id)
            files["notes.txt"] = "备注"
            make_task(root, task_id, files)

            base = builtin_template()
            ignore = preview_path(str(root), dataclasses.replace(base, extra_files="ignore"))
            warn = preview_path(str(root), dataclasses.replace(base, extra_files="warn"))
            error = preview_path(str(root), dataclasses.replace(base, extra_files="error"))
            self.assertEqual(ignore.tasks[0].result, "pass")
            self.assertEqual(warn.tasks[0].result, "warn")
            self.assertEqual(error.tasks[0].result, "fail")
            for result in (ignore, warn, error):
                self.assertIn("FC-301", codes(result))

    def test_ignored_system_file_is_info(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            task_id = "12345678"
            files = single_files(task_id)
            files["Thumbs.db"] = b"x"
            make_task(root, task_id, files)
            result = preview_path(str(root), builtin_template())
            task = result.tasks[0]
            self.assertEqual(task.result, "pass")
            self.assertIn("FC-306", [issue.code for issue in task.issues])

    def test_warn_as_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            task_id = "12345678"
            files = single_files(task_id)
            files["notes.txt"] = "备注"
            make_task(root, task_id, files)
            template = dataclasses.replace(
                builtin_template(), extra_files="warn", warn_as_fail=True
            )
            result = preview_path(str(root), template)
            self.assertEqual(result.tasks[0].result, "fail")

    def test_max_tasks_marks_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            make_task(root, "11111111", single_files("11111111"))
            make_task(root, "22222222", single_files("22222222"))
            result = preview_path(str(root), builtin_template(), max_tasks=1)
            self.assertTrue(result.summary.truncated)
            self.assertEqual(len(result.tasks), 1)
            self.assertIn("FC-008", [issue.code for issue in result.run_issues])


class TestDiscovery(unittest.TestCase):
    def test_recursive_off_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            nested = root / "团队A"
            make_task(nested, "12345678", single_files("12345678"))
            result = preview_path(str(root), builtin_template())
            self.assertEqual(result.tasks, [])

            recursive = preview_path(str(root), builtin_template(), recursive=True)
            self.assertEqual(len(recursive.tasks), 1)
            self.assertEqual(recursive.tasks[0].task_id, "12345678")

    def test_duplicate_task_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            make_task(root, "12345678", single_files("12345678"), parent=root / "批次A")
            make_task(root, "12345678", single_files("12345678"), parent=root / "批次B")
            result = preview_path(str(root), builtin_template(), recursive=True)
            self.assertEqual(len(result.tasks), 2)
            for task in result.tasks:
                self.assertIn("FC-004", [issue.code for issue in task.issues])
            run_codes = [issue.code for issue in result.run_issues]
            self.assertNotIn("FC-004", run_codes)

    def test_root_name_not_matching_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "1234567"
            make_task(root.parent, "1234567", {"1234567.jpg": b"jpg"})
            result = preview_path(str(root), builtin_template())
            self.assertEqual(result.tasks, [])
            self.assertIn("FC-001", [issue.code for issue in result.run_issues])

    def test_invalid_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "not-exist"
            result = preview_path(str(missing), builtin_template())
            self.assertEqual(result.tasks, [])
            self.assertIn("FC-000", [issue.code for issue in result.run_issues])


class TestServicePersistence(unittest.TestCase):
    def _service(self, tmp: Path) -> FormatCheckerService:
        return FormatCheckerService(
            db_path=tmp / "management.db",
            report_dir=tmp / "reports",
        )

    def test_read_only_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            make_task(root, "12345678", single_files("12345678"))
            make_task(root, "87654321", {"87654321_CH.txt": "中文"})
            before = snapshot(root)
            service = self._service(base)
            try:
                result = service.run_check(
                    FormatCheckRequest(root_path=str(root), operator="tester")
                )
                self.assertEqual(result.summary.scanned_tasks, 2)
                self.assertEqual(result.summary.passed_tasks, 1)
                self.assertEqual(result.summary.failed_tasks, 1)
                self.assertEqual(result.summary.integrity_status, "ok")
                self.assertTrue(result.summary.run_id.startswith("fmt_"))
                self.assertTrue(result.report_paths.get("xlsx"))
                self.assertTrue(Path(result.report_paths["xlsx"]).is_file())
                self.assertEqual(snapshot(root), before, "检查不应修改源目录")

                # 源目录不出现任何新文件/报告
                names = {path.name for path in root.rglob("*")}
                self.assertFalse(any(name.endswith((".xlsx", ".csv", ".json", ".log", ".tmp")) for name in names))

                xlsx = Path(result.report_paths["xlsx"])
                with zipfile.ZipFile(xlsx) as archive:
                    workbook = archive.read("xl/workbook.xml").decode("utf-8")
                for sheet in ("总览", "任务结果", "异常明细", "规则说明"):
                    self.assertIn(sheet, workbook)

                tasks_csv = Path(result.report_paths["tasks_csv"])
                issues_csv = Path(result.report_paths["issues_csv"])
                self.assertTrue(tasks_csv.is_file())
                self.assertTrue(issues_csv.is_file())
                self.assertEqual(tasks_csv.read_bytes()[:3], b"\xef\xbb\xbf")
                self.assertIn("任务ID", tasks_csv.read_text(encoding="utf-8-sig"))
                self.assertIn("FC-101", issues_csv.read_text(encoding="utf-8-sig"))
            finally:
                service.close()

    def test_report_guard_blocks_source_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            make_task(root, "12345678", single_files("12345678"))
            before = snapshot(root)
            service = self._service(base)
            try:
                with self.assertRaises(ValueError):
                    service.run_check(
                        FormatCheckRequest(
                            root_path=str(root),
                            report_dir=str(root / "reports"),
                        )
                    )
                self.assertEqual(snapshot(root), before)
                self.assertFalse((root / "reports").exists())
                self.assertEqual(service.list_runs(), [])
            finally:
                service.close()

            with self.assertRaises(ValueError):
                guard_report_dir(str(root), str(root))

    def test_template_crud_and_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = self._service(Path(tmp))
            try:
                builtin = service.default_template()
                self.assertEqual(builtin.id, "builtin_default")
                self.assertGreaterEqual(len(service.list_templates()), 1)

                with self.assertRaises(ValueError):
                    service.save_template(builtin)

                custom = dataclasses.replace(
                    builtin,
                    id="",
                    name="测试模板",
                    builtin=False,
                )
                version_id = service.save_template(custom, operator="tester")
                self.assertTrue(version_id)
                rows = [row for row in service.list_templates() if row["name"] == "测试模板"]
                self.assertEqual(len(rows), 1)
                template_id = rows[0]["id"]
                loaded = service.get_template_with_version(template_id)
                self.assertIsNotNone(loaded)
                template, _, version = loaded
                self.assertEqual(version, 1)
                self.assertFalse(template.builtin)

                template.name = "测试模板改名"
                service.save_template(template, operator="tester", note="第二版")
                loaded2 = service.get_template_with_version(template_id)
                self.assertEqual(loaded2[2], 2)
                self.assertEqual(loaded2[0].name, "测试模板改名")
                self.assertEqual(len(service.template_versions(template_id)), 2)

                with self.assertRaises(ValueError):
                    service.delete_template("builtin_default")
                service.delete_template(template_id)
                self.assertIsNone(service.get_template(template_id))
            finally:
                service.close()

    def test_history_diff_and_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            make_task(root, "11111111", single_files("11111111"))
            failing = make_task(root, "22222222", {"22222222_CH.txt": "中文"})
            service = self._service(base)
            try:
                first = service.run_check(
                    FormatCheckRequest(root_path=str(root), operator="tester")
                )
                first_id = first.summary.run_id
                self.assertEqual(len(service.run_tasks(first_id)), 2)
                self.assertGreaterEqual(len(service.run_issues(first_id)), 1)
                self.assertTrue(first.summary.report_path)

                (failing / "22222222.jpg").write_bytes(b"jpg")
                second = service.run_check(
                    FormatCheckRequest(
                        root_path=str(root),
                        operator="tester",
                        previous_run_id=first_id,
                    )
                )
                second_id = second.summary.run_id
                self.assertNotEqual(first.summary.report_path, second.summary.report_path)
                self.assertTrue(Path(first.summary.report_path).is_file())
                self.assertTrue(Path(second.summary.report_path).is_file())
                diff = service.diff_runs(first_id, second_id)
                self.assertEqual(len(diff["fail_to_pass"]), 1)
                self.assertEqual(
                    Path(diff["fail_to_pass"][0]["task_path"]).name, "22222222"
                )
                self.assertGreaterEqual(diff["unchanged_count"], 1)

                rerun = service.rerun(first_id, only_anomalies=True, operator="tester")
                self.assertEqual(rerun.summary.scanned_tasks, 1)
                self.assertEqual(rerun.summary.passed_tasks, 1)
                self.assertEqual(rerun.summary.previous_run_id, first_id)
                self.assertEqual(len(service.list_runs()), 3)

                rerun_all = service.rerun(second_id, operator="tester")
                self.assertEqual(rerun_all.summary.scanned_tasks, 2)

                with self.assertRaises(ValueError):
                    service.diff_runs(first_id, "fmt_not_exist")
                with self.assertRaises(ValueError):
                    service.rerun("fmt_not_exist")
            finally:
                service.close()

    def test_only_anomalies_requires_previous(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            make_task(root, "12345678", single_files("12345678"))
            service = self._service(base)
            try:
                with self.assertRaises(ValueError):
                    service.run_check(
                        FormatCheckRequest(
                            root_path=str(root),
                            only_anomalies=True,
                        )
                    )
            finally:
                service.close()

    def test_cancelled_run_keeps_partial_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "root"
            make_task(root, "11111111", single_files("11111111"))
            make_task(root, "22222222", single_files("22222222"))
            service = self._service(base)
            try:
                result = service.run_check(
                    FormatCheckRequest(root_path=str(root)),
                    should_cancel=lambda: True,
                )
                self.assertEqual(result.summary.status, "cancelled")
                self.assertEqual(result.summary.scanned_tasks, 0)
                self.assertEqual(len(service.run_tasks(result.summary.run_id)), 0)
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
