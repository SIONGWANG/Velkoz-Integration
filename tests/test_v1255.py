"""v1.25.5 测试：管理中心导航、批次总览、工作台、团队/类别聚合与批次详情。

运行：
    .\\python\\python.exe tests\\test_v1255.py
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

from modules.batch_management.dashboard import (  # noqa: E402
    BatchDashboard,
    acceptance_label,
    format_label,
    translation_label,
)
from modules.batch_management.service import BatchService  # noqa: E402
from modules.database.connection import open_database  # noqa: E402


def _insert_batch(conn, batch_id, name, identity, **overrides):
    values = {
        "id": batch_id,
        "display_id": overrides.get("display_id", batch_id.upper()),
        "name": name,
        "category_l1": overrides.get("category_l1", ""),
        "category_l2": overrides.get("category_l2", ""),
        "submit_date": overrides.get("submit_date", "2026-11-02"),
        "data_root": overrides.get("data_root", "D:/data"),
        "batch_path": overrides.get("batch_path", f"D:/data/{name}"),
        "data_count": overrides.get("data_count", 0),
        "format_status": overrides.get("format_status", "not_checked"),
        "translation_status": overrides.get("translation_status", "not_started"),
        "acceptance_status": overrides.get("acceptance_status", "not_started"),
        "qualified_count": overrides.get("qualified_count", 0),
        "rejected_count": overrides.get("rejected_count", 0),
        "remaining_count": overrides.get("remaining_count", 0),
        "status": overrides.get("status", "confirmed"),
        "source": "scan",
        "identity_json": json.dumps(identity, ensure_ascii=False),
        "created_at": overrides.get("created_at", "2026-11-02T09:00:00"),
        "updated_at": overrides.get("updated_at", "2026-11-02T10:00:00"),
    }
    columns = ",".join(values)
    placeholders = ",".join("?" for _ in values)
    conn.execute(f"INSERT INTO batches({columns}) VALUES ({placeholders})", tuple(values.values()))


def _insert_group(conn, group_id, batch_id, task_id, user_name):
    conn.execute(
        "INSERT INTO data_groups(id, batch_id, task_id, normalized_path, user_name, image_count, txt_count, file_count, status)"
        " VALUES (?, ?, ?, ?, ?, 2, 1, 3, 'pending')",
        (group_id, batch_id, task_id, f"D:/data/{batch_id}/{task_id}", user_name),
    )


def seed(db_path: Path) -> None:
    db = open_database(db_path)
    try:
        conn = db.connect()
        _insert_batch(
            conn, "batch_a1", "A团队批次一",
            {"team": "A团队", "supplier": "供应商一", "project": "项目甲", "category_l1": "图文", "category_l2": "商品"},
            display_id="B-001", submit_date="2026-11-01", data_count=8,
            acceptance_status="in_progress", qualified_count=5, rejected_count=1, remaining_count=2,
        )
        _insert_batch(
            conn, "batch_a2", "A团队批次二",
            {"team": "A团队", "supplier": "供应商一", "project": "项目甲", "category_l1": "图文", "category_l2": "场景"},
            display_id="B-002", data_count=3, remaining_count=3,
        )
        _insert_batch(
            conn, "batch_b1", "B团队批次",
            {"team": "B团队", "supplier": "供应商二", "project": "项目乙", "category_l1": "视频", "category_l2": "口播"},
            display_id="B-003", data_count=4, acceptance_status="completed",
            qualified_count=4, rejected_count=0, remaining_count=0,
            format_status="passed", translation_status="completed",
        )
        for index in range(8):
            _insert_group(conn, f"a1_g{index}", "batch_a1", f"1000000{index}", "验收员甲")
        for index in range(3):
            _insert_group(conn, f"a2_g{index}", "batch_a2", f"2000000{index}", "验收员乙")
        for index in range(4):
            _insert_group(conn, f"b1_g{index}", "batch_b1", f"3000000{index}", "验收员丙")
        conn.execute(
            "INSERT INTO sub_batches(id, batch_id, name, data_root, data_count, status, note, created_at)"
            " VALUES ('sub1', 'batch_a1', '子批次一', 'D:/data', 3, 'pending', '测试子批次', '2026-11-01T09:00:00')"
        )
        conn.execute(
            "INSERT INTO batch_status_log(id, batch_id, from_status, to_status, operator, reason, created_at)"
            " VALUES ('log1', 'batch_a1', NULL, 'confirmed', 'tester', '来源：scan', '2026-11-01T09:00:00')"
        )
        conn.execute(
            "INSERT INTO record_imports(id, source_path, source_type, file_size, file_mtime_ns, rows_total, rows_new, rows_skipped, rows_error, operator_detected, batch_id, status, imported_at, imported_by, note, created_at)"
            " VALUES ('imp1', 'D:/records/a1.csv', 'legacy_csv', 10, 1, 3, 2, 0, 1, '验收员甲', 'batch_a1', 'partial', '2026-11-02T10:00:00', 'tester', '测试导入', '2026-11-02T10:00:00')"
        )
        conn.execute(
            "INSERT INTO record_imports(id, source_path, source_type, file_size, file_mtime_ns, rows_total, rows_new, rows_skipped, rows_error, operator_detected, batch_id, status, imported_at, imported_by, note, created_at)"
            " VALUES ('imp2', 'D:/records/a2.csv', 'legacy_csv', 10, 1, 1, 1, 0, 0, '验收员乙', 'batch_a2', 'completed', '2026-11-02T11:00:00', 'tester', '测试导入', '2026-11-02T11:00:00')"
        )
        conn.execute(
            "INSERT INTO format_check_runs(id, batch_id, root_path, started_at, finished_at, status, scanned_tasks, passed_tasks, failed_tasks, warning_tasks, skipped_tasks, integrity_status, operator)"
            " VALUES ('fmt1', 'batch_a1', 'D:/data/batch_a1', '2026-11-02T09:00:00', '2026-11-02T09:10:00', 'failed', 8, 6, 2, 0, 0, 'ok', 'tester')"
        )
        conn.execute(
            "INSERT INTO format_check_runs(id, batch_id, root_path, started_at, finished_at, status, scanned_tasks, passed_tasks, failed_tasks, warning_tasks, skipped_tasks, integrity_status, operator)"
            " VALUES ('fmt2', 'batch_b1', 'D:/data/batch_b1', '2026-11-02T09:00:00', '2026-11-02T09:10:00', 'passed', 4, 4, 0, 0, 0, 'ok', 'tester')"
        )
        conn.execute(
            "INSERT INTO translation_jobs(id, batch_id, provider, model, status, started_at, finished_at, operator, note, created_at)"
            " VALUES ('tr1', 'batch_b1', 'ark', 'model-x', 'completed', '2026-11-02T09:00:00', '2026-11-02T09:30:00', 'tester', '翻译', '2026-11-02T09:00:00')"
        )
        conn.execute(
            "INSERT INTO translation_items(id, job_id, data_group_id, task_id, folder_path, status)"
            " VALUES ('tri1', 'tr1', 'b1_g0', '30000000', 'D:/data/batch_b1/30000000', 'translated')"
        )
        conn.commit()
    finally:
        db.close()


class TestDashboardAggregation(unittest.TestCase):
    def test_batches_and_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "management.db"
            seed(db_path)
            db = open_database(db_path)
            try:
                dashboard = BatchDashboard(db)
                rows = dashboard.batches()
                self.assertEqual(len(rows), 3)
                by_id = {row["id"]: row for row in rows}
                self.assertEqual(by_id["batch_a1"]["team"], "A团队")
                self.assertEqual(by_id["batch_a1"]["task_count"], 8)
                self.assertEqual(by_id["batch_a1"]["imported_records"], 2)
                self.assertEqual(by_id["batch_a1"]["format_failed"], 2)
                self.assertEqual(by_id["batch_a1"]["translation_jobs"], 0)
                self.assertEqual(by_id["batch_b1"]["imported_records"], 0)
                self.assertEqual(by_id["batch_b1"]["translation_jobs"], 1)
                self.assertEqual(by_id["batch_b1"]["translated_items"], 1)

                self.assertEqual(len(dashboard.filter_rows(rows, team="A团队")), 2)
                self.assertEqual(len(dashboard.filter_rows(rows, supplier="供应商二")), 1)
                self.assertEqual(len(dashboard.filter_rows(rows, date="2026-11-01")), 1)
                self.assertEqual(len(dashboard.filter_rows(rows, category_l1="图文")), 2)
                self.assertEqual(len(dashboard.filter_rows(rows, category_l2="场景")), 1)
                self.assertEqual(len(dashboard.filter_rows(rows, status="confirmed")), 3)
                self.assertEqual(len(dashboard.filter_rows(rows, keyword="B团队")), 1)
                self.assertEqual(
                    len(dashboard.filter_rows(rows, team="A团队", category_l1="图文", date="2026-11-02")),
                    1,
                )

                options = dashboard.filter_options(rows)
                self.assertEqual(options["teams"], ["A团队", "B团队"])
                self.assertEqual(options["suppliers"], ["供应商一", "供应商二"])
                self.assertIn("图文", options["categories_l1"])
                self.assertIn("场景", options["categories_l2"])
            finally:
                db.close()

    def test_team_and_category_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "management.db"
            seed(db_path)
            db = open_database(db_path)
            try:
                dashboard = BatchDashboard(db)
                rows = dashboard.batches()
                teams = {item["team"]: item for item in dashboard.team_summary(rows)}
                self.assertEqual(teams["A团队"]["batches"], 2)
                self.assertEqual(teams["A团队"]["task_count"], 11)
                self.assertEqual(teams["A团队"]["qualified_count"], 5)
                self.assertEqual(teams["A团队"]["imported_records"], 3)
                self.assertEqual(teams["B团队"]["batches"], 1)
                self.assertEqual(teams["B团队"]["task_count"], 4)

                categories = dashboard.category_summary(rows)
                first = next(item for item in categories if item["category_l2"] == "商品")
                self.assertEqual(first["category_l1"], "图文")
                self.assertEqual(first["batches"], 1)
                self.assertEqual(first["task_count"], 8)
                self.assertEqual(first["format_failed"], 2)
                second = next(item for item in categories if item["category_l2"] == "口播")
                self.assertEqual(second["task_count"], 4)
            finally:
                db.close()

    def test_workbench(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "management.db"
            seed(db_path)
            db = open_database(db_path)
            try:
                dashboard = BatchDashboard(db)
                data = dashboard.workbench()
                self.assertEqual(data["totals"]["batches"], 3)
                self.assertEqual(data["totals"]["task_count"], 15)
                self.assertEqual(data["totals"]["pending_batches"], 2)
                self.assertEqual(data["totals"]["anomaly_batches"], 1)
                self.assertEqual(len(data["recent_imports"]), 2)
                self.assertEqual(len(data["recent_formats"]), 2)
            finally:
                db.close()

    def test_batch_detail_and_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "management.db"
            seed(db_path)
            db = open_database(db_path)
            try:
                before = {
                    table: db.connect().execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
                    for table in ("batches", "data_groups", "record_imports", "format_check_runs", "translation_jobs")
                }
                dashboard = BatchDashboard(db)
                detail = dashboard.batch_detail("batch_a1")
                self.assertIsNotNone(detail)
                self.assertEqual(detail["batch"]["display_id"], "B-001")
                self.assertEqual(len(detail["groups"]), 8)
                self.assertEqual(len(detail["imports"]), 1)
                self.assertEqual(len(detail["format_runs"]), 1)
                self.assertEqual(len(detail["translation_jobs"]), 0)
                self.assertEqual(len(detail["status_logs"]), 1)
                self.assertEqual(len(detail["sub_batches"]), 1)
                self.assertEqual(detail["sub_batches"][0]["name"], "子批次一")
                after = {
                    table: db.connect().execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
                    for table in before
                }
                self.assertEqual(before, after)
            finally:
                db.close()

    def test_batch_status_update_writes_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "management.db"
            seed(db_path)
            service = BatchService(db_path)
            try:
                service.update_batch_status("batch_a1", "paused", operator="tester", reason="暂停测试")
                db = service.db
                row = db.connect().execute(
                    "SELECT status FROM batches WHERE id = 'batch_a1'"
                ).fetchone()
                self.assertEqual(row["status"], "paused")
                logs = db.connect().execute(
                    "SELECT * FROM batch_status_log WHERE batch_id = 'batch_a1' ORDER BY created_at"
                ).fetchall()
                self.assertEqual(len(logs), 2)
                self.assertEqual(logs[-1]["from_status"], "confirmed")
                self.assertEqual(logs[-1]["to_status"], "paused")
                audit = db.connect().execute(
                    "SELECT * FROM operation_logs WHERE module = 'batch_management' AND action = 'update_batch_status'"
                ).fetchall()
                self.assertEqual(len(audit), 1)
                service.update_batch_status("batch_a1", "confirmed", operator="tester")
                service.update_batch_status("batch_a1", "archived", operator="tester")
                self.assertEqual(
                    db.connect().execute("SELECT status FROM batches WHERE id = 'batch_a1'").fetchone()["status"],
                    "archived",
                )
                with self.assertRaises(ValueError):
                    service.update_batch_status("batch_a1", "deleted")
                with self.assertRaises(ValueError):
                    service.update_batch_status("missing", "paused")
            finally:
                service.close()

    def test_labels(self):
        self.assertEqual(acceptance_label("not_started"), "未开始")
        self.assertEqual(format_label("not_checked"), "未检查")
        self.assertEqual(translation_label("completed"), "已完成")
        self.assertEqual(acceptance_label("unknown-value"), "unknown-value")


if __name__ == "__main__":
    unittest.main()
