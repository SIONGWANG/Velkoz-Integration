"""v1.25.10 测试：库目录导出、飞书摘要、工作台视图与导航。

运行：
    .\\python\\python.exe tests\\test_v12510.py
"""
from __future__ import annotations

import hashlib
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
from modules.export_library import (  # noqa: E402
    build_library_target,
    export_qualified_groups,
    get_library_root,
    qualified_groups,
    resolve_source_root,
    save_library_root,
    validate_library_root,
)
from modules.feishu_summary import LocalSummaryAdapter, batch_line, daily_summary, summary_rows  # noqa: E402
from modules.workbench_view import (  # noqa: E402
    filter_rows,
    group_by_category,
    group_by_team,
    is_anomaly,
    next_action,
    progress_text,
    status_word,
    todo_counts,
)
from ui.router import ADVANCED_PAGES, PAGE_KEYS, PRIMARY_PAGES  # noqa: E402


def _write(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
        path.write_bytes(b"x")
    else:
        path.write_text("中文指令\n", encoding="utf-8")


def _group(batch: Path, task_id: str) -> Path:
    folder = batch / task_id
    folder.mkdir(parents=True, exist_ok=True)
    _write(folder / f"{task_id}.jpg")
    _write(folder / f"{task_id}_CH.txt")
    return folder


def _snapshot(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _row(**overrides):
    base = {
        "id": "b1",
        "name": "批次A",
        "display_id": "B001",
        "team": "团队A",
        "category_l1": "大类",
        "category_l2": "小类",
        "submit_date": "2026-11-11",
        "task_count": 10,
        "image_count": 40,
        "accepted_records": 0,
        "accepted_qualified": 0,
        "accepted_rejected": 0,
        "remaining_count": 10,
        "format_state": "not_checked",
        "translation_state": "pending",
        "acceptance_state": "no_records",
        "source_type": "pending_acceptance",
        "archived_groups": 0,
        "last_activity": "2026-11-11T09:00:00",
    }
    base.update(overrides)
    return base


class TestLibraryPaths(unittest.TestCase):
    def test_mirror_relative_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / "库"
            pending = base / "项目" / "02-待验数据"
            batch = pending / "大类" / "团队提交" / "1107" / "批次A"
            batch.mkdir(parents=True)
            target = build_library_target(str(library), batch_path=str(batch), source_root_path=str(pending))
            self.assertEqual(
                os.path.normcase(target),
                os.path.normcase(str(library / "大类" / "团队提交" / "1107" / "批次A")),
            )

    def test_existing_hierarchy_is_reused_and_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / "库"
            batch = base / "项目" / "02-待验数据" / "团队" / "1107" / "批次A"
            batch.mkdir(parents=True)
            target = build_library_target(str(library), batch_path=str(batch), source_root_path=str(base / "项目" / "02-待验数据"))
            os.makedirs(target, exist_ok=True)
            self.assertTrue(Path(target).is_dir())
            again = build_library_target(str(library), batch_path=str(batch), source_root_path=str(base / "项目" / "02-待验数据"))
            self.assertEqual(target, again)

    def test_fallback_and_invalid_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp) / "批次A"
            batch.mkdir()
            target = build_library_target(str(Path(tmp) / "库"), batch_path=str(batch))
            self.assertEqual(os.path.normcase(target), os.path.normcase(str(Path(tmp) / "库" / "批次A")))
            with self.assertRaises(ValueError):
                build_library_target("", batch_path=str(batch))
            with self.assertRaises(ValueError):
                build_library_target(str(Path(tmp) / "库"), batch_path="")

    def test_validate_rejects_source_inside(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "项目" / "02-待验数据"
            source.mkdir(parents=True)
            ok, detail = validate_library_root("")
            self.assertFalse(ok)
            ok, detail = validate_library_root(str(source / "库"), source_roots=(str(source),))
            self.assertFalse(ok)
            self.assertIn("源数据目录", detail)
            ok, detail = validate_library_root(str(base / "库"))
            self.assertTrue(ok, detail)

    def test_resolve_source_root_and_settings_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            pending = base / "项目" / "02-待验数据"
            final = base / "项目" / "03-申请终验"
            pending.mkdir(parents=True)
            final.mkdir(parents=True)
            db = open_database(base / "data" / "management.db")
            try:
                db.connect().execute(
                    "INSERT INTO project_roots(id, project_name, parent_path, pending_path, final_path, enabled)"
                    " VALUES ('p1', '项目', ?, ?, ?, 1)",
                    (str(base / "项目"), str(pending), str(final)),
                )
                db.connect().commit()
                batch = pending / "团队" / "1107" / "批次A"
                batch.mkdir(parents=True)
                self.assertEqual(os.path.normcase(resolve_source_root(db, str(batch))), os.path.normcase(str(pending)))
                self.assertEqual(resolve_source_root(db, str(base / "其他")), "")
                self.assertEqual(get_library_root(db), "")
                saved = save_library_root(db, str(base / "库"))
                self.assertTrue(saved.endswith("库"))
                self.assertEqual(get_library_root(db), saved)
            finally:
                db.close()


class TestQualifiedExport(unittest.TestCase):
    def _prepare(self, base: Path):
        pending = base / "项目" / "02-待验数据"
        batch_path = pending / "大类" / "团队" / "1107" / "批次A"
        paths = {
            "g1": _group(batch_path, "10000001"),
            "g2": _group(batch_path, "10000002"),
            "g3": _group(batch_path, "10000003"),
            "g4": _group(batch_path, "10000004"),
        }
        db = open_database(base / "data" / "management.db")
        conn = db.connect()
        conn.execute(
            """
            INSERT INTO batches(id, display_id, name, submit_date, data_root, batch_path, data_count,
                                status, source, identity_json, created_at, updated_at, source_type,
                                source_root_path, relative_path)
            VALUES ('b1','B001','批次A','2026-11-11',?,?,4,'confirmed','scan',?,'2026-11-11T09:00:00',
                    '2026-11-11T09:00:00','pending_acceptance',?,?)
            """,
            (
                str(pending),
                str(batch_path),
                json.dumps({"team": "团队"}, ensure_ascii=False),
                str(pending),
                str(batch_path.relative_to(pending)),
            ),
        )
        for group_id, path in paths.items():
            conn.execute(
                "INSERT INTO data_groups(id, batch_id, task_id, normalized_path, user_name, image_count, txt_count, file_count)"
                " VALUES (?, 'b1', ?, ?, '验收员甲', 1, 1, 2)",
                (group_id, path.name, str(path)),
            )
        conn.execute(
            "INSERT INTO record_imports(id, source_path, source_type, status, imported_at, created_at)"
            " VALUES ('imp1','C:/records.xlsx','xlsx','completed','2026-11-11T10:00:00','2026-11-11T10:00:00')"
        )
        records = [
            ("r1", "g1", "10000001", "合格", "2026-11-11T10:00:00"),
            ("r2", "g1", "10000001", "不合格", "2026-11-11T11:00:00"),
            ("r3", "g2", "10000002", "修改后合格", "2026-11-11T10:00:00"),
            ("r4", "g3", "10000003", "不合格", "2026-11-11T10:00:00"),
            ("r5", "g4", "10000004", "合格", "2026-11-11T10:00:00"),
        ]
        for record_id, group_id, task_id, result, record_time in records:
            conn.execute(
                "INSERT INTO imported_records(id, import_id, data_group_id, task_id, result, record_time,"
                " source_row_fingerprint, created_at) VALUES (?, 'imp1', ?, ?, ?, ?, ?, ?)",
                (record_id, group_id, task_id, result, record_time, record_id, record_time),
            )
        conn.commit()
        return db, batch_path, paths

    def test_latest_result_wins_and_export_is_copy_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            db, batch_path, paths = self._prepare(base)
            try:
                groups = qualified_groups(db, "b1")
                self.assertEqual({item["data_group_id"] for item in groups}, {"g2", "g4"})
                target = build_library_target(
                    str(base / "库"),
                    batch_path=str(batch_path),
                    source_root_path=str(base / "项目" / "02-待验数据"),
                )
                before = _snapshot(batch_path)
                first = export_qualified_groups(groups, target)
                self.assertEqual(first.copied, 2, first.errors)
                self.assertEqual(first.files, 4)
                self.assertEqual(_snapshot(batch_path), before)
                self.assertTrue((Path(target) / "10000002" / "10000002.jpg").is_file())
                self.assertTrue((Path(target) / "10000004" / "10000004.jpg").is_file())

                second = export_qualified_groups(groups, target)
                self.assertEqual(second.skipped, 2)
                self.assertEqual(second.copied, 0)

                (Path(target) / "10000002" / "extra.jpg").write_bytes(b"y")
                third = export_qualified_groups(groups, target)
                self.assertEqual(third.conflicts, 1)
                self.assertEqual(third.skipped, 1)

                fourth = export_qualified_groups(groups, target, overwrite=True)
                self.assertEqual(fourth.copied, 2, fourth.errors)
                self.assertFalse((Path(target) / "10000002" / "extra.jpg").exists())
            finally:
                db.close()

    def test_missing_source_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            db, _batch_path, paths = self._prepare(base)
            try:
                groups = qualified_groups(db, "b1")
                broken = dict(groups[0])
                broken["normalized_path"] = str(base / "不存在")
                result = export_qualified_groups([broken], str(base / "库"))
                self.assertEqual(result.missing, 1)
                self.assertEqual(result.copied, 0)
            finally:
                db.close()


class TestFeishuSummary(unittest.TestCase):
    def test_batch_line_and_daily_summary(self):
        done = _row(
            accepted_records=10,
            accepted_qualified=9,
            accepted_rejected=1,
            acceptance_state="completed",
        )
        line = batch_line(done)
        self.assertIn("团队A", line)
        self.assertIn("大类 / 小类", line)
        self.assertIn("合格 9", line)
        self.assertIn("已完成", line)

        empty = _row(id="b2", name="批次B", acceptance_state="no_records")
        text = daily_summary([done, empty], date="2026-11-11")
        self.assertIn("2026-11-11 验收日报", text)
        self.assertIn("暂无已导入验收记录", text)
        self.assertIn("合计：合格 9，不合格 1", text)
        rows = summary_rows([done])
        self.assertEqual(rows[0]["数据量"], 10)
        self.assertEqual(rows[0]["状态"], "已完成")

    def test_empty_and_local_adapter(self):
        text = daily_summary([], date="2026-11-11")
        self.assertIn("暂无已导入验收记录", text)
        adapter = LocalSummaryAdapter()
        result = adapter.send(text)
        self.assertFalse(result.sent)
        self.assertEqual(result.reason, "local_only")


class TestWorkbenchView(unittest.TestCase):
    def test_next_action_order(self):
        self.assertEqual(next_action(_row(format_state="not_checked"))[0], "format")
        self.assertEqual(next_action(_row(format_state="failed"))[1], "处理格式异常")
        self.assertEqual(next_action(_row(format_state="passed", translation_state="pending"))[0], "translate")
        self.assertEqual(next_action(_row(format_state="passed", translation_state="completed"))[0], "acceptance")
        self.assertEqual(
            next_action(
                _row(
                    format_state="passed",
                    translation_state="completed",
                    acceptance_state="completed",
                    accepted_records=10,
                    remaining_count=0,
                )
            )[0],
            "archive",
        )
        self.assertEqual(
            next_action(
                _row(
                    source_type="final_acceptance",
                    final_state="found",
                )
            )[1],
            "开始终验",
        )

    def test_is_anomaly_and_status(self):
        self.assertTrue(is_anomaly(_row(format_state="warning")))
        self.assertTrue(is_anomaly(_row(import_errors=2)))
        self.assertFalse(is_anomaly(_row()))
        self.assertEqual(status_word(_row()), "待验收")
        self.assertEqual(status_word(_row(acceptance_state="in_progress", accepted_records=3)), "进行中")
        self.assertEqual(progress_text(_row(accepted_records=5, task_count=10)), "已验收 5/10 组 · 50%")

    def test_filter_group_and_totals(self):
        rows = [
            _row(id="b1", name="A", team="团队A", category_l1="大类", category_l2="小类"),
            _row(
                id="b2",
                name="B",
                team="团队B",
                category_l1="大类",
                category_l2="小类",
                acceptance_state="completed",
                accepted_records=10,
                remaining_count=0,
            ),
        ]
        self.assertEqual([row["id"] for row in filter_rows(rows, team="团队A")], ["b1"])
        self.assertEqual([row["id"] for row in filter_rows(rows, only_todo=True)], ["b1"])
        self.assertEqual([row["id"] for row in filter_rows(rows, keyword="团队B")], ["b2"])
        grouped = group_by_team(rows)
        self.assertEqual([team for team, _ in grouped], ["团队A", "团队B"])
        self.assertEqual(group_by_category(rows)[0][0], "大类 / 小类")
        counts = todo_counts(rows, 3)
        self.assertEqual(counts["pending"], 1)
        self.assertEqual(counts["done"], 1)
        self.assertEqual(counts["candidates"], 3)


class TestNavigation(unittest.TestCase):
    def test_primary_and_advanced_pages(self):
        self.assertEqual(
            {key for key, _ in PRIMARY_PAGES},
            {"workbench", "record_import", "stats", "settings"},
        )
        advanced = {key for key, _ in ADVANCED_PAGES}
        self.assertIn("format", advanced)
        self.assertIn("precheck", advanced)
        self.assertTrue({"workbench", "batch_detail", "format", "precheck", "archive", "stats"} <= PAGE_KEYS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
