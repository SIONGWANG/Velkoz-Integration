"""v1.25.7 测试：统计与报表（批次/人员/错误/标签/日报/缓存/分页/导出/只读）。

运行：
    .\\python\\python.exe tests\\test_v1257.py
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.database.connection import open_database  # noqa: E402
from modules.stats.models import (  # noqa: E402
    BATCH_STATS_COLUMNS,
    DAILY_STATS_COLUMNS,
    ERROR_STATS_COLUMNS,
    OPERATOR_STATS_COLUMNS,
    TAG_STATS_COLUMNS,
    StatsFilters,
)
from modules.stats.service import StatsService  # noqa: E402


class StatsFixture:
    def __init__(self, base: Path):
        self.base = base
        self.db_path = base / "data" / "management.db"
        self.source = base / "source"
        self.source.mkdir(parents=True, exist_ok=True)
        self.source_file = self.source / "10000001" / "10000001_CH.txt"
        self.source_file.parent.mkdir(parents=True, exist_ok=True)
        self.source_file.write_text("只读源文件", encoding="utf-8")
        self.seed()

    def seed(self) -> None:
        db = open_database(self.db_path)
        try:
            conn = db.connect()
            records = [
                (
                    "b1", "B-001", "批次甲", "批发市场", "商品", "2026-11-01",
                    "2026-11-01T08:00:00", "2026-11-02T09:00:00",
                    {"team": "A团队", "supplier": "供应商甲"}, 2, 1, 1, 2,
                ),
                (
                    "b2", "B-002", "批次乙", "批发市场", "商品", "2026-10-01",
                    "2026-10-01T08:00:00", "2026-10-02T09:00:00",
                    {"team": "B团队", "supplier": "供应商乙"}, 0, 0, 0, 1,
                ),
            ]
            for (
                batch_id, display_id, name, l1, l2, submit_date, created_at,
                updated_at, identity, qualified, rejected, remaining, data_count,
            ) in records:
                conn.execute(
                    """
                    INSERT INTO batches(
                        id, display_id, name, category_l1, category_l2, submit_date,
                        data_root, batch_path, data_count, status, source, identity_json,
                        qualified_count, rejected_count, remaining_count, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', 'scan', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch_id, display_id, name, l1, l2, submit_date,
                        str(self.source), str(self.source / display_id), data_count,
                        json.dumps(identity, ensure_ascii=False),
                        qualified, rejected, remaining, created_at, updated_at,
                    ),
                )
            groups = [
                ("g1", "b1", "10000001", "A团队", 2, 3, "2026-11-01T09:00:00"),
                ("g2", "b1", "10000002", "A团队", 3, 4, "2026-11-02T09:00:00"),
                ("g3", "b2", "10000003", "B团队", 5, 6, "2026-10-01T09:00:00"),
            ]
            for group_id, batch_id, task_id, user_name, images, files, first_seen in groups:
                conn.execute(
                    """
                    INSERT INTO data_groups(
                        id, batch_id, task_id, normalized_path, root_path, user_name,
                        image_count, txt_count, file_count, status, first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, 'pending', ?, ?)
                    """,
                    (
                        group_id, batch_id, task_id, str(self.source / task_id),
                        str(self.source), user_name, images, files, first_seen, first_seen,
                    ),
                )
            conn.execute(
                """
                INSERT INTO record_imports(
                    id, source_path, source_type, rows_total, rows_new, batch_id,
                    status, imported_at, created_at
                ) VALUES ('imp1', 'D:/records/b1.csv', 'legacy_csv', 5, 5, 'b1',
                          'completed', '2026-11-02T09:00:00', '2026-11-02T09:00:00')
                """
            )
            conn.execute(
                """
                INSERT INTO record_imports(
                    id, source_path, source_type, rows_total, rows_new, batch_id,
                    status, imported_at, created_at
                ) VALUES ('imp2', 'D:/records/b2.csv', 'legacy_csv', 1, 1, 'b2',
                          'completed', '2026-10-02T09:00:00', '2026-10-02T09:00:00')
                """
            )
            imported = [
                ("ir1", "imp1", "g1", "10000001", "甲", "合格", "标签A,标签B；未知标签", "2026-11-02 10:00:00", 2),
                ("ir2", "imp1", "g1", "10000001", "甲", "修改后合格", "标签A", "2026-11-02 11:00:00", 3),
                ("ir3", "imp1", "g2", "10000002", "甲", "不合格", "标签C/标签B", "2026-11-03 09:00:00", 4),
                ("ir4", "imp1", "g2", "10000002", "乙", "作废", "标签A", "2026-11-03 10:00:00", 5),
                ("ir5", "imp1", "g2", "10000002", "乙", "待定", "自造标签", "2026-11-03 11:00:00", 6),
                ("ir6", "imp2", "g3", "10000003", "丙", "合格", "标签B", "2026-10-02 10:00:00", 2),
            ]
            for record_id, import_id, group_id, task_id, operator, result, tags, when, row in imported:
                conn.execute(
                    """
                    INSERT INTO imported_records(
                        id, import_id, data_group_id, task_id, operator, result,
                        tags_raw, record_time, source_row, source_row_fingerprint,
                        link_status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'matched', ?)
                    """,
                    (
                        record_id, import_id, group_id, task_id, operator, result,
                        tags, when, row, f"fp_{record_id}", when,
                    ),
                )
            runs = [
                ("r0", "b1", "2026-10-30T09:00:00", "2026-10-30T09:30:00", 99, 9, 9),
                ("r1", "b1", "2026-11-02T09:00:00", "2026-11-02T09:30:00", 2, 1, 0),
                ("r2", "b2", "2026-10-02T09:00:00", "2026-10-02T09:20:00", 1, 0, 1),
            ]
            for run_id, batch_id, started, finished, scanned, failed, warnings in runs:
                conn.execute(
                    """
                    INSERT INTO format_check_runs(
                        id, batch_id, root_path, started_at, finished_at, status,
                        scanned_tasks, passed_tasks, failed_tasks, warning_tasks, skipped_tasks,
                        operator
                    ) VALUES (?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?, 0, '检查员')
                    """,
                    (
                        run_id, batch_id, str(self.source), started, finished,
                        scanned, scanned - failed - warnings, failed, warnings,
                    ),
                )
            issues = [
                ("i1", "r1", "FC-101", "error", "10000002", "2026-11-02T09:10:00"),
                ("i2", "r1", "FC-999", "error", "10000002", "2026-11-02T09:11:00"),
                ("i3", "r0", "FC-101", "error", "10000001", "2026-10-30T09:10:00"),
                ("i4", "r2", "FC-102", "warn", "10000003", "2026-10-02T09:10:00"),
            ]
            for issue_id, run_id, code, severity, task_id, _when in issues:
                conn.execute(
                    """
                    INSERT INTO format_check_issues(
                        id, run_id, code, severity, task_id, task_path, message
                    ) VALUES (?, ?, ?, ?, ?, ?, '')
                    """,
                    (issue_id, run_id, code, severity, task_id, str(self.source / task_id)),
                )
            conn.execute(
                """
                INSERT INTO translation_jobs(
                    id, batch_id, provider, model, status, started_at, finished_at, created_at
                ) VALUES ('j1', 'b1', 'mock', 'mock-model', 'completed',
                          '2026-11-02T12:00:00', '2026-11-02T13:00:00', '2026-11-02T12:00:00')
                """
            )
            for item_id, status, translated_at in (
                ("ti1", "translated", "2026-11-02T12:30:00"),
                ("ti2", "translated", "2026-11-02T12:40:00"),
                ("ti3", "failed", ""),
            ):
                conn.execute(
                    """
                    INSERT INTO translation_items(
                        id, job_id, task_id, folder_path, status, translated_at
                    ) VALUES (?, 'j1', ?, ?, ?, ?)
                    """,
                    (item_id, item_id, str(self.source), status, translated_at or None),
                )
            conn.execute(
                """
                INSERT INTO archive_jobs(
                    id, batch_id, output_root, status, total_items, verified_items,
                    created_at, started_at, finished_at, updated_at
                ) VALUES ('aj1', 'b1', ?, 'completed', 2, 2,
                          '2026-11-04T09:00:00', '2026-11-04T09:01:00',
                          '2026-11-04T10:00:00', '2026-11-04T10:00:00')
                """,
                (str(base_output(self.base)),),
            )
            for item_id, group_id, task_id, status in (
                ("ai1", "g1", "10000001", "verified"),
                ("ai2", "g2", "10000002", "skipped"),
            ):
                conn.execute(
                    """
                    INSERT INTO archive_items(
                        id, job_id, data_group_id, task_id, source_path, dest_path,
                        status, created_at, updated_at
                    ) VALUES (?, 'aj1', ?, ?, ?, ?, ?, '2026-11-04T09:00:00', '2026-11-04T10:00:00')
                    """,
                    (
                        item_id, group_id, task_id, str(self.source / task_id),
                        str(base_output(self.base) / task_id), status,
                    ),
                )
            conn.commit()
        finally:
            db.close()

    def open(self) -> StatsService:
        return StatsService(self.db_path)


def base_output(base: Path) -> Path:
    return base / "archive_output"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestSchema(unittest.TestCase):
    def test_schema_version_and_stats_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = open_database(Path(tmp) / "management.db")
            try:
                self.assertGreaterEqual(db.schema_version(), 8)
                tables = {
                    row["name"]
                    for row in db.connect().execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                self.assertIn("stats_daily", tables)
                indexes = {
                    row["name"]
                    for row in db.connect().execute(
                        "SELECT name FROM sqlite_master WHERE type = 'index'"
                    ).fetchall()
                }
                self.assertIn("idx_stats_daily_scope", indexes)
            finally:
                db.close()

    def test_stats_daily_unique_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = open_database(Path(tmp) / "management.db")
            try:
                conn = db.connect()
                conn.execute(
                    "INSERT INTO stats_daily(id, date) VALUES ('a', '2026-11-01')"
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        "INSERT INTO stats_daily(id, date) VALUES ('b', '2026-11-01')"
                    )
            finally:
                db.close()


class TestBatchStats(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture = StatsFixture(Path(self.tmp.name))
        self.service = self.fixture.open()

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def test_batch_rows_and_sources(self):
        rows = self.service.batch_stats()
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(set(by_id), {"b1", "b2"})
        row = by_id["b1"]
        self.assertEqual(row["team"], "A团队")
        self.assertEqual(row["supplier"], "供应商甲")
        self.assertEqual(row["task_count"], 2)
        self.assertEqual(row["image_count"], 5)
        self.assertEqual(row["format_checked"], 2)
        self.assertEqual(row["format_failed"], 1)
        self.assertEqual(row["translated"], 2)
        self.assertEqual(row["translation_failed"], 1)
        self.assertEqual(row["batch_qualified"], 2)
        self.assertEqual(row["batch_rejected"], 1)
        self.assertEqual(row["batch_remaining"], 1)
        self.assertEqual(row["imported_records"], 5)
        self.assertEqual(row["imported_qualified"], 2)
        self.assertEqual(row["imported_rejected"], 2)
        self.assertEqual(row["archived"], 2)
        self.assertIn("2026-11", row["last_activity"])
        # 最新一次格式检查覆盖旧 run（旧 run 的 99/9 不参与批次统计）
        self.assertNotEqual(row["format_checked"], 99)

    def test_filter_by_date_batch_team_operator(self):
        date_rows = self.service.batch_stats(StatsFilters(date_from="2026-11-01"))
        self.assertEqual([row["id"] for row in date_rows], ["b1"])
        # 批次日期筛选不影响该批次已导入的完整验收记录
        self.assertEqual(date_rows[0]["imported_records"], 5)

        team_rows = self.service.batch_stats(StatsFilters(team="B团队"))
        self.assertEqual([row["id"] for row in team_rows], ["b2"])

        operator_rows = self.service.batch_stats(StatsFilters(operator="乙"))
        self.assertEqual([row["id"] for row in operator_rows], ["b1"])

        batch_rows = self.service.batch_stats(StatsFilters(batch_id="b2"))
        self.assertEqual([row["id"] for row in batch_rows], ["b2"])

    def test_overview_totals(self):
        overview = self.service.overview()
        self.assertEqual(overview["batches"], 2)
        self.assertEqual(overview["task_count"], 3)
        self.assertEqual(overview["image_count"], 10)
        self.assertEqual(overview["format_checked"], 3)
        self.assertEqual(overview["format_failed"], 1)
        self.assertEqual(overview["translated"], 2)
        self.assertEqual(overview["imported_records"], 6)
        self.assertEqual(overview["imported_qualified"], 3)
        self.assertEqual(overview["imported_rejected"], 2)
        self.assertEqual(overview["archived"], 2)

    def test_list_options(self):
        self.assertEqual(self.service.list_teams(), ["A团队", "B团队"])
        self.assertEqual(self.service.list_operators(), ["丙", "乙", "甲"])
        self.assertEqual(len(self.service.list_batches()), 2)


class TestOperatorStats(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture = StatsFixture(Path(self.tmp.name))
        self.service = self.fixture.open()

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def test_operator_aggregation_and_workload(self):
        rows = self.service.operator_stats(StatsFilters(date_from="2026-11-02", date_to="2026-11-02"))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["operator"], "甲")
        self.assertEqual(row["records"], 2)
        self.assertEqual(row["qualified"], 2)
        self.assertEqual(row["rejected"], 0)
        self.assertEqual(row["other"], 0)
        self.assertEqual(row["qualified_rate"], 1.0)
        self.assertEqual(row["groups"], 1)
        self.assertEqual(row["images"], 2)
        self.assertEqual(row["workload"], 2.0)

        images = self.service.operator_stats(
            StatsFilters(date_from="2026-11-02", date_to="2026-11-02"),
            workload_mode="images",
        )
        self.assertEqual(images[0]["workload"], 2.0)

        mixed = self.service.operator_stats(
            StatsFilters(date_from="2026-11-02", date_to="2026-11-02"),
            workload_mode="mixed",
        )
        self.assertEqual(mixed[0]["workload"], 2.1)

    def test_other_results_and_rate(self):
        rows = self.service.operator_stats(StatsFilters(operator="乙"))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["records"], 2)
        self.assertEqual(row["qualified"], 0)
        self.assertEqual(row["rejected"], 1)
        self.assertEqual(row["other"], 1)
        self.assertEqual(row["qualified_rate"], 0.0)

    def test_empty_database_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = StatsService(Path(tmp) / "data" / "management.db")
            try:
                self.assertEqual(service.overview()["batches"], 0)
                self.assertEqual(service.operator_stats(), [])
                self.assertEqual(service.tag_stats(), [])
                self.assertEqual(service.error_type_stats(), [])
                report = service.daily_report("2026-11-01")
                self.assertEqual(report["accepted"], 0)
                self.assertEqual(report["pending"], 0)
            finally:
                service.close()


class TestErrorsAndTags(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture = StatsFixture(Path(self.tmp.name))
        self.service = self.fixture.open()

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def test_error_type_stats(self):
        rows = self.service.error_type_stats()
        by_code = {row["code"]: row for row in rows}
        self.assertEqual(by_code["FC-101"]["count"], 2)
        self.assertEqual(by_code["FC-101"]["tasks"], 2)
        self.assertEqual(by_code["FC-101"]["batches"], 1)
        self.assertEqual(by_code["FC-101"]["severity_label"], "错误")
        self.assertTrue(by_code["FC-101"]["message"])
        self.assertEqual(by_code["FC-999"]["message"], "FC-999")
        self.assertEqual(by_code["FC-102"]["severity_label"], "警告")

        filtered = self.service.error_type_stats(
            StatsFilters(date_from="2026-11-02", date_to="2026-11-02")
        )
        self.assertEqual({row["code"] for row in filtered}, {"FC-101", "FC-999"})

    def test_tag_stats_preserves_history(self):
        rows = self.service.tag_stats()
        by_tag = {row["tag"]: row for row in rows}
        self.assertEqual(by_tag["标签A"]["count"], 3)
        self.assertEqual(by_tag["标签A"]["qualified"], 2)
        self.assertEqual(by_tag["标签A"]["rejected"], 1)
        self.assertEqual(by_tag["标签B"]["count"], 3)
        self.assertEqual(by_tag["自造标签"]["count"], 1)
        self.assertEqual(by_tag["未知标签"]["count"], 1)
        self.assertEqual(by_tag["标签C"]["count"], 1)
        self.assertEqual(by_tag["标签A"]["batches"], 1)

    def test_tag_filter(self):
        rows = self.service.tag_stats(StatsFilters(batch_id="b2"))
        self.assertEqual({row["tag"] for row in rows}, {"标签B"})


class TestDaily(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture = StatsFixture(Path(self.tmp.name))
        self.service = self.fixture.open()

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def test_daily_report(self):
        report = self.service.daily_report("2026-11-02")
        self.assertEqual(report["new_batches"], 0)
        self.assertEqual(report["new_groups"], 1)
        self.assertEqual(report["format_checked"], 2)
        self.assertEqual(report["format_failed"], 1)
        self.assertEqual(report["translated"], 2)
        self.assertEqual(report["translation_failed"], 1)
        self.assertEqual(report["accepted"], 2)
        self.assertEqual(report["qualified"], 2)
        self.assertEqual(report["rejected"], 0)
        self.assertEqual(report["archived"], 0)
        self.assertEqual(report["pending"], 1)

    def test_daily_ignores_range_filters(self):
        report = self.service.daily_report(
            "2026-11-02",
            StatsFilters(date_from="2026-11-03", date_to="2026-11-03"),
        )
        self.assertEqual(report["accepted"], 2)

    def test_daily_archive_day(self):
        report = self.service.daily_report("2026-11-04")
        self.assertEqual(report["archived"], 2)

    def test_daily_series_and_invalid_range(self):
        series = self.service.daily_series("2026-11-01", "2026-11-03")
        self.assertEqual([row["date"] for row in series], ["2026-11-01", "2026-11-02", "2026-11-03"])
        with self.assertRaises(ValueError):
            self.service.daily_series("2020-01-01", "2026-01-01")

    def test_rebuild_cache_is_idempotent(self):
        count = self.service.rebuild_daily_cache("2026-11-01", "2026-11-04")
        self.assertEqual(count, 4)
        rows = self.service.cache_rows("2026-11-01", "2026-11-04")
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[1]["date"], "2026-11-02")
        self.assertEqual(rows[1]["accepted"], 2)
        self.assertEqual(rows[1]["source"], "rebuild")

        self.service.rebuild_daily_cache("2026-11-01", "2026-11-04")
        self.assertEqual(len(self.service.cache_rows("2026-11-01", "2026-11-04")), 4)


class TestTraceAndExport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture = StatsFixture(Path(self.tmp.name))
        self.service = self.fixture.open()

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def test_trace_pagination(self):
        self.assertEqual(self.service.count_imported_records(), 6)
        first = self.service.trace_imported_records(limit=2, offset=0)
        second = self.service.trace_imported_records(limit=2, offset=2)
        self.assertEqual(len(first), 2)
        self.assertEqual(len(second), 2)
        self.assertNotEqual(
            {row["record_id"] for row in first},
            {row["record_id"] for row in second},
        )
        streamed = list(self.service.iter_imported_records(batch_size=2))
        self.assertEqual(len(streamed), 6)
        self.assertEqual(self.service.count_format_issues(), 4)
        issues = self.service.trace_format_issues(limit=1, offset=0)
        self.assertEqual(len(issues), 1)
        self.assertTrue(issues[0]["task_id"])

    def test_trace_filters(self):
        self.assertEqual(
            self.service.count_imported_records(StatsFilters(operator="甲")), 3
        )
        self.assertEqual(
            self.service.count_imported_records(StatsFilters(team="B团队")), 1
        )
        self.assertEqual(
            self.service.count_imported_records(StatsFilters(batch_id="b1")), 5
        )

    def test_export_excel_sheets_and_no_source_writes(self):
        source_hash = _sha(self.fixture.source_file)
        source_files = sorted(
            str(path.relative_to(self.fixture.source))
            for path in self.fixture.source.rglob("*")
            if path.is_file()
        )
        conn = self.service.db.connect()
        before = {
            table: conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
            for table in (
                "batches", "data_groups", "imported_records", "record_imports",
                "format_check_runs", "format_check_issues", "translation_jobs",
                "translation_items", "archive_jobs", "archive_items",
            )
        }

        target = self.service.export_excel()
        self.assertTrue(target.exists())
        self.assertEqual(target.parent, self.fixture.base / "reports" / "stats")
        self.assertGreater(target.stat().st_size, 1000)
        with zipfile.ZipFile(target) as archive:
            workbook = archive.read("xl/workbook.xml").decode("utf-8")
        for name in (
            "筛选与总览", "批次统计", "人员统计", "错误类型", "标签统计",
            "日报", "口径说明", "验收明细", "格式错误明细",
        ):
            self.assertIn(name, workbook)

        after = {
            table: conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
            for table in before
        }
        self.assertEqual(before, after)
        self.assertEqual(_sha(self.fixture.source_file), source_hash)
        after_files = sorted(
            str(path.relative_to(self.fixture.source))
            for path in self.fixture.source.rglob("*")
            if path.is_file()
        )
        self.assertEqual(source_files, after_files)

    def test_export_columns_stable(self):
        from modules.stats import models

        self.assertEqual(len(models.BATCH_STATS_COLUMNS), 22)
        self.assertEqual(len(models.OPERATOR_STATS_COLUMNS), 11)
        self.assertEqual(len(models.ERROR_STATS_COLUMNS), 8)
        self.assertEqual(len(models.TAG_STATS_COLUMNS), 6)
        self.assertEqual(len(models.DAILY_STATS_COLUMNS), 13)
        self.assertEqual(BATCH_STATS_COLUMNS[0], "批次号")
        self.assertEqual(OPERATOR_STATS_COLUMNS[0], "人员")
        self.assertEqual(ERROR_STATS_COLUMNS[0], "错误码")
        self.assertEqual(TAG_STATS_COLUMNS[0], "标签（原文）")
        self.assertEqual(DAILY_STATS_COLUMNS[0], "日期")

    def test_export_with_unicode_note_and_small_trace_limit(self):
        target = self.service.export_excel(trace_limit=1)
        self.assertTrue(target.exists())
        self.assertEqual(self.service.count_imported_records(), 6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
