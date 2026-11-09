"""v1.25.6 测试：合格数据归档（只复制、预览、冲突、校验、恢复、报告）。

运行：
    .\\python\\python.exe tests\\test_v1256.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.archive.models import (  # noqa: E402
    FILTER_IMPORTED,
    FILTER_MANUAL,
    FILTER_QUALIFIED,
    FILTER_STATUS,
    ITEM_CONFLICT,
    ITEM_FAILED,
    ITEM_PENDING,
    ITEM_SKIPPED,
    ITEM_VERIFIED,
    JOB_COMPLETED,
    JOB_FAILED,
    JOB_PAUSED,
)
from modules.archive.planner import (  # noqa: E402
    build_dest_path,
    build_prefix,
    parse_custom_prefix,
    render_template,
    sanitize_name,
)
from modules.archive.service import ArchiveService  # noqa: E402
from modules.database.connection import open_database  # noqa: E402


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _tree_state(root: Path) -> dict[str, str]:
    state: dict[str, str] = {}
    for current, _dirs, files in os.walk(root):
        for name in files:
            path = Path(current) / name
            state[str(path.relative_to(root))] = _sha(path)
    return state


class ArchiveFixture:
    def __init__(self, base: Path):
        self.base = base
        self.source = base / "source"
        self.output = base / "output"
        self.db_path = base / "data" / "management.db"
        self.reports = base / "reports" / "archive"
        self.batch_path = self.source / "批发市场" / "商品" / "A团队" / "2026-11-01" / "批次甲"
        self.task1 = self.batch_path / "10000001"
        self.task2 = self.batch_path / "10000002"
        _write(self.task1 / "a.jpg", b"image-a")
        _write(self.task1 / "b.txt", "中文说明".encode("utf-8"))
        _write(self.task2 / "c.jpg", b"image-c")
        _write(self.task2 / "d.txt", "第二批".encode("utf-8"))
        self.seed()

    def seed(self) -> None:
        db = open_database(self.db_path)
        try:
            conn = db.connect()
            conn.execute(
                """
                INSERT INTO batches(
                    id, display_id, name, category_l1, category_l2, submit_date,
                    data_root, batch_path, data_count, status, source, identity_json,
                    qualified_count, rejected_count, remaining_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', 'scan', ?, 1, 0, 1, ?, ?)
                """,
                (
                    "batch_1",
                    "B-001",
                    "批次甲",
                    "批发市场",
                    "商品",
                    "2026-11-01",
                    str(self.source),
                    str(self.batch_path),
                    2,
                    json.dumps(
                        {
                            "category_l1": "批发市场",
                            "category_l2": "商品",
                            "team": "A团队",
                            "date": "2026-11-01",
                            "batch": "批次甲",
                        },
                        ensure_ascii=False,
                    ),
                    "2026-11-01T09:00:00",
                    "2026-11-01T10:00:00",
                ),
            )
            for group_id, task_id, task_path, status in (
                ("g1", "10000001", self.task1, "qualified"),
                ("g2", "10000002", self.task2, "pending"),
            ):
                conn.execute(
                    """
                    INSERT INTO data_groups(
                        id, batch_id, task_id, normalized_path, root_path, user_name,
                        image_count, txt_count, file_count, status, first_seen_at, last_seen_at
                    ) VALUES (?, 'batch_1', ?, ?, ?, 'A团队', 1, 1, 2, ?, ?, ?)
                    """,
                    (
                        group_id,
                        task_id,
                        str(task_path),
                        str(self.source),
                        status,
                        "2026-11-01T09:00:00",
                        "2026-11-01T10:00:00",
                    ),
                )
            conn.execute(
                """
                INSERT INTO record_imports(
                    id, source_path, source_type, status, batch_id, created_at
                ) VALUES ('imp1', 'D:/records/a.csv', 'legacy_csv', 'completed', 'batch_1', '2026-11-01T11:00:00')
                """
            )
            conn.execute(
                """
                INSERT INTO imported_records(
                    id, import_id, data_group_id, task_id, operator, result, link_status, source_row_fingerprint, created_at
                ) VALUES ('ir1', 'imp1', 'g2', '10000002', '验收员乙', '合格', 'matched', 'fp1', '2026-11-01T11:00:00')
                """
            )
            conn.commit()
        finally:
            db.close()

    def service(self) -> ArchiveService:
        return ArchiveService(self.db_path, reports_dir=self.reports)


class TestTemplateAndLayers(unittest.TestCase):
    def test_template_rendering_and_fallbacks(self):
        values = {
            "批次名": "批次甲",
            "验收员": "张三",
            "合格数量": "2",
            "日期": "2026-11-01",
            "大类目": "批发市场",
            "小类目": "",
            "团队": "A团队",
            "自定义": "",
        }
        name, missing = render_template("{批次名}_{验收员}合格{合格数量}条", values)
        self.assertEqual(name, "批次甲_张三合格2条")
        self.assertEqual(missing, [])
        name, missing = render_template("{批次名}_{小类目}_{团队}", values)
        self.assertEqual(name, "批次甲_A团队")
        self.assertIn("小类目", missing)
        with self.assertRaises(ValueError):
            render_template("{未知变量}", values)

    def test_sanitize_name(self):
        self.assertEqual(sanitize_name("a/b\\c:d*e?f"), "a_b_c_d_e_f")
        self.assertEqual(sanitize_name("..", "回退"), "回退")
        self.assertEqual(sanitize_name("CON", "回退"), "_CON")

    def test_prefix_by_start_layer(self):
        context = {
            "大类目": "批发市场",
            "小类目": "商品",
            "团队": "A团队",
            "日期": "2026-11-01",
        }
        self.assertEqual(
            build_prefix("team", context, source_root="D:/源目录", custom_prefix="")[0],
            ["A团队", "2026-11-01"],
        )
        self.assertEqual(
            build_prefix("date", context, source_root="D:/源目录", custom_prefix="")[0],
            ["2026-11-01"],
        )
        self.assertEqual(
            build_prefix("batch", context, source_root="D:/源目录", custom_prefix="")[0],
            [],
        )
        root_prefix, _ = build_prefix("root", context, source_root="D:/源目录", custom_prefix="")
        self.assertEqual(root_prefix[0], "源目录")
        custom, _ = build_prefix("custom", context, source_root="D:/源目录", custom_prefix="客户/2026")
        self.assertEqual(custom, ["客户", "2026"])
        with self.assertRaises(ValueError):
            parse_custom_prefix("../逃逸")

    def test_safe_dest_join(self):
        dest = build_dest_path("D:/归档", ["A团队"], "批次_张三合格1条", "10000001")
        self.assertTrue(dest.endswith(os.path.join("A团队", "批次_张三合格1条", "10000001")))
        with self.assertRaises(ValueError):
            build_dest_path("D:/归档", [".."], "批次", "10000001")


class TestPreview(unittest.TestCase):
    def test_preview_status_filter_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = ArchiveFixture(Path(tmp))
            before = _tree_state(fixture.source)
            service = fixture.service()
            try:
                preview = service.preview(
                    "batch_1",
                    str(fixture.output),
                    start_layer="team",
                    name_template="{批次名}_{验收员}合格{合格数量}条",
                    operator="张三",
                    filter_mode=FILTER_STATUS,
                    filter_value="qualified",
                )
            finally:
                service.close()
            self.assertEqual(preview.total_items, 1)
            self.assertEqual(preview.total_files, 2)
            self.assertGreater(preview.total_bytes, 0)
            self.assertEqual(preview.rendered_folder, "批次甲_张三合格1条")
            self.assertFalse(fixture.output.exists())
            self.assertEqual(before, _tree_state(fixture.source))
            group = preview.groups[0]
            self.assertEqual(group.data_group_id, "g1")
            expected = (
                fixture.output
                / "A团队"
                / "2026-11-01"
                / "批次甲_张三合格1条"
                / "10000001"
            )
            self.assertEqual(os.path.normcase(group.dest_path), os.path.normcase(str(expected)))
            self.assertTrue(any(row["files"] == 2 for row in preview.tree))

    def test_preview_imported_and_manual_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = ArchiveFixture(Path(tmp))
            service = fixture.service()
            try:
                imported = service.preview(
                    "batch_1",
                    str(fixture.output),
                    start_layer="date",
                    name_template="{批次名}",
                    operator="张三",
                    filter_mode=FILTER_IMPORTED,
                )
                qualified = service.preview(
                    "batch_1",
                    str(fixture.output),
                    start_layer="date",
                    name_template="{批次名}",
                    operator="张三",
                    filter_mode=FILTER_QUALIFIED,
                )
                manual = service.preview(
                    "batch_1",
                    str(fixture.output),
                    start_layer="batch",
                    name_template="{批次名}",
                    operator="张三",
                    filter_mode=FILTER_MANUAL,
                    group_ids=["g1", "g2"],
                )
                none = service.preview(
                    "batch_1",
                    str(fixture.output),
                    start_layer="batch",
                    name_template="{批次名}",
                    operator="张三",
                    filter_mode=FILTER_STATUS,
                    filter_value="not-a-status",
                )
            finally:
                service.close()
            self.assertEqual([g.data_group_id for g in imported.groups], ["g2"])
            self.assertEqual(imported.rendered_folder, "批次甲")
            self.assertEqual([g.data_group_id for g in qualified.groups], ["g1", "g2"])
            self.assertEqual(manual.total_items, 2)
            self.assertEqual(none.total_items, 0)
            self.assertTrue(none.errors)

    def test_duplicate_destination_is_detected_and_not_copied_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = ArchiveFixture(Path(tmp))
            dup_dir = fixture.batch_path / "10000001_dup"
            _write(dup_dir / "a.jpg", b"image-a")
            db = open_database(fixture.db_path)
            try:
                db.connect().execute(
                    """
                    INSERT INTO data_groups(
                        id, batch_id, task_id, normalized_path, root_path, user_name,
                        image_count, txt_count, file_count, status, first_seen_at, last_seen_at
                    ) VALUES ('g3', 'batch_1', '10000001', ?, ?, 'A团队', 1, 0, 1, 'qualified', ?, ?)
                    """,
                    (str(dup_dir), str(fixture.source), "2026-11-01T09:00:00", "2026-11-01T10:00:00"),
                )
                db.connect().commit()
            finally:
                db.close()
            service = fixture.service()
            try:
                preview = service.preview(
                    "batch_1",
                    str(fixture.output),
                    start_layer="team",
                    name_template="{批次名}",
                    operator="张三",
                    filter_mode=FILTER_MANUAL,
                    group_ids=["g1", "g3"],
                )
                self.assertEqual(preview.error_groups, 1)
                self.assertIn("重复", preview.groups[1].error)
                job_id = service.create_job(preview, operator="张三")
                result = service.run_job(job_id)
                items = service.list_items(job_id)
            finally:
                service.close()
            self.assertEqual(result.failed_items, 1)
            self.assertEqual(result.verified_items, 1)
            statuses = {item["data_group_id"]: item["status"] for item in items}
            self.assertEqual(statuses["g1"], ITEM_VERIFIED)
            self.assertEqual(statuses["g3"], ITEM_FAILED)

    def test_preview_rejects_output_inside_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = ArchiveFixture(Path(tmp))
            service = fixture.service()
            try:
                preview = service.preview(
                    "batch_1",
                    str(fixture.source / "归档"),
                    start_layer="team",
                    name_template="{批次名}",
                    operator="张三",
                    filter_mode=FILTER_STATUS,
                )
            finally:
                service.close()
            self.assertTrue(preview.errors)
            self.assertFalse((fixture.source / "归档").exists())


class TestRun(unittest.TestCase):
    def test_copy_verify_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = ArchiveFixture(Path(tmp))
            source_before = _tree_state(fixture.source)
            service = fixture.service()
            try:
                preview = service.preview(
                    "batch_1",
                    str(fixture.output),
                    start_layer="team",
                    name_template="{批次名}_{验收员}合格{合格数量}条",
                    operator="张三",
                    filter_mode=FILTER_STATUS,
                )
                job_id = service.create_job(preview, operator="张三", verify_hash=True)
                result = service.run_job(job_id)
                items = service.list_items(job_id)
                job = service.get_job(job_id)
                report = service.report_text(job_id)
            finally:
                service.close()
            self.assertEqual(result.status, JOB_COMPLETED)
            self.assertEqual(result.verified_items, 1)
            self.assertEqual(result.conflict_items, 0)
            self.assertEqual(items[0]["status"], ITEM_VERIFIED)
            self.assertTrue(items[0]["source_fingerprint"])
            self.assertEqual(items[0]["source_fingerprint"], items[0]["dest_fingerprint"])
            self.assertTrue(job["report_path"])
            report_path = Path(job["report_path"])
            self.assertTrue(report_path.is_file())
            self.assertFalse(str(report_path).startswith(str(fixture.source)))
            self.assertIn("源图片/TXT/CSV 未被移动", report)
            dest_group = fixture.output / "A团队" / "2026-11-01" / "批次甲_张三合格1条" / "10000001"
            self.assertEqual((dest_group / "a.jpg").read_bytes(), b"image-a")
            self.assertEqual((dest_group / "b.txt").read_text(encoding="utf-8"), "中文说明")
            self.assertEqual(source_before, _tree_state(fixture.source))

    def test_conflict_never_overwrites_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = ArchiveFixture(Path(tmp))
            source_before = _tree_state(fixture.source)
            service = fixture.service()
            try:
                preview = service.preview(
                    "batch_1",
                    str(fixture.output),
                    start_layer="team",
                    name_template="{批次名}",
                    operator="张三",
                    filter_mode=FILTER_STATUS,
                )
                dest_group = fixture.output / "A团队" / "2026-11-01" / "批次甲" / "10000001"
                _write(dest_group / "a.jpg", b"DO-NOT-OVERWRITE")
                _write(dest_group / "b.txt", "中文说明".encode("utf-8"))
                conflict_preview = service.preview(
                    "batch_1",
                    str(fixture.output),
                    start_layer="team",
                    name_template="{批次名}",
                    operator="张三",
                    filter_mode=FILTER_STATUS,
                )
                self.assertEqual(conflict_preview.conflict_groups, 1)
                job_id = service.create_job(conflict_preview, operator="张三")
                first = service.run_job(job_id)
                self.assertEqual(first.status, JOB_FAILED)
                self.assertEqual(first.conflict_items, 1)
                self.assertEqual((dest_group / "a.jpg").read_bytes(), b"DO-NOT-OVERWRITE")
                self.assertEqual(source_before, _tree_state(fixture.source))
                items = service.list_items(job_id)
                self.assertEqual(items[0]["status"], ITEM_CONFLICT)
                (dest_group / "a.jpg").unlink()
                second = service.run_job(job_id)
                items = service.list_items(job_id)
            finally:
                service.close()
            self.assertEqual(second.status, JOB_COMPLETED)
            self.assertEqual(items[0]["status"], ITEM_VERIFIED)
            self.assertEqual((dest_group / "a.jpg").read_bytes(), b"image-a")
            self.assertEqual(source_before, _tree_state(fixture.source))

    def test_hash_mismatch_is_detected_and_partial_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = ArchiveFixture(Path(tmp))
            source_before = _tree_state(fixture.source)
            service = fixture.service()
            try:
                preview = service.preview(
                    "batch_1",
                    str(fixture.output),
                    start_layer="team",
                    name_template="{批次名}",
                    operator="张三",
                    filter_mode=FILTER_STATUS,
                )
                job_id = service.create_job(preview, operator="张三", verify_hash=True)

                def broken_copy(source: str, dest: str) -> None:
                    with open(dest, "wb") as handle:
                        handle.write(b"broken")

                with patch("modules.archive.service._copy_exclusive", side_effect=broken_copy):
                    result = service.run_job(job_id)
                items = service.list_items(job_id)
            finally:
                service.close()
            self.assertEqual(result.status, JOB_FAILED)
            self.assertEqual(result.failed_items, 1)
            self.assertIn("不一致", items[0]["error_message"])
            dest_group = fixture.output / "A团队" / "2026-11-01" / "批次甲" / "10000001"
            self.assertFalse((dest_group / "a.jpg").exists())
            self.assertFalse((dest_group / "b.txt").exists())
            self.assertEqual(source_before, _tree_state(fixture.source))

    def test_identical_existing_files_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = ArchiveFixture(Path(tmp))
            service = fixture.service()
            try:
                preview = service.preview(
                    "batch_1",
                    str(fixture.output),
                    start_layer="team",
                    name_template="{批次名}",
                    operator="张三",
                    filter_mode=FILTER_STATUS,
                )
                dest_group = fixture.output / "A团队" / "2026-11-01" / "批次甲" / "10000001"
                _write(dest_group / "a.jpg", (fixture.task1 / "a.jpg").read_bytes())
                _write(dest_group / "b.txt", (fixture.task1 / "b.txt").read_bytes())
                before_mtime = (dest_group / "a.jpg").stat().st_mtime_ns
                preview = service.preview(
                    "batch_1",
                    str(fixture.output),
                    start_layer="team",
                    name_template="{批次名}",
                    operator="张三",
                    filter_mode=FILTER_STATUS,
                )
                job_id = service.create_job(preview, operator="张三")
                result = service.run_job(job_id)
                items = service.list_items(job_id)
                after_mtime = (dest_group / "a.jpg").stat().st_mtime_ns
            finally:
                service.close()
            self.assertEqual(result.status, JOB_COMPLETED)
            self.assertEqual(items[0]["status"], ITEM_SKIPPED)
            self.assertEqual(before_mtime, after_mtime)

    def test_cancel_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = ArchiveFixture(Path(tmp))
            service = fixture.service()
            try:
                preview = service.preview(
                    "batch_1",
                    str(fixture.output),
                    start_layer="team",
                    name_template="{批次名}",
                    operator="张三",
                    filter_mode=FILTER_MANUAL,
                    group_ids=["g1", "g2"],
                )
                job_id = service.create_job(preview, operator="张三")
                calls = {"count": 0}

                def should_cancel() -> bool:
                    calls["count"] += 1
                    return calls["count"] > 3

                paused = service.run_job(job_id, should_cancel=should_cancel)
                self.assertEqual(paused.status, JOB_PAUSED)
                items = service.list_items(job_id)
                by_task = {item["task_id"]: item for item in items}
                self.assertEqual(by_task["10000001"]["status"], ITEM_VERIFIED)
                self.assertEqual(by_task["10000002"]["status"], ITEM_PENDING)
                dest1 = fixture.output / "A团队" / "2026-11-01" / "批次甲" / "10000001" / "a.jpg"
                mtime = dest1.stat().st_mtime_ns
                resumed = service.run_job(job_id)
                items = service.list_items(job_id)
                by_task = {item["task_id"]: item for item in items}
            finally:
                service.close()
            self.assertEqual(resumed.status, JOB_COMPLETED)
            self.assertEqual(by_task["10000002"]["status"], ITEM_VERIFIED)
            self.assertEqual(by_task["10000001"]["status"], ITEM_VERIFIED)
            self.assertEqual(dest1.stat().st_mtime_ns, mtime)

    def test_create_job_rejects_preview_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = ArchiveFixture(Path(tmp))
            service = fixture.service()
            try:
                preview = service.preview(
                    "batch_1",
                    str(fixture.source / "out"),
                    start_layer="team",
                    name_template="{批次名}",
                    operator="张三",
                    filter_mode=FILTER_STATUS,
                )
                with self.assertRaises(ValueError):
                    service.create_job(preview, operator="张三")
                with self.assertRaises(ValueError):
                    service.preview(
                        "missing-batch",
                        str(fixture.output),
                        start_layer="team",
                        name_template="{批次名}",
                    )
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
