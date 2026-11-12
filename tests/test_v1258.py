"""v1.25.8 测试：业务目录接入、批次工作台、格式/翻译关联与来源关联。

运行：
    .\\python\\python.exe tests\\test_v1258.py
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
TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from test_v1253 import FakeProvider, fake_factory, make_task  # noqa: E402

from modules.batch_management.dashboard import BatchDashboard  # noqa: E402
from modules.database.connection import open_database  # noqa: E402
from modules.format_checker.models import FormatCheckRequest  # noqa: E402
from modules.format_checker.service import FormatCheckerService  # noqa: E402
from modules.precheck.models import ProviderProfile, TranslationRequest  # noqa: E402
from modules.precheck.service import PrecheckService  # noqa: E402
from modules.scanning.rules import load_default_ruleset  # noqa: E402
from modules.scanning.scanner import ScanOptions  # noqa: E402
from modules.workspace.discovery import derive_identity, discover_source  # noqa: E402
from modules.workspace.models import (  # noqa: E402
    SOURCE_FINAL,
    SOURCE_PENDING,
)
from modules.workspace.project_roots import ProjectRootService  # noqa: E402
from modules.workspace.service import WorkspaceService  # noqa: E402


def _write(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".psd"}:
        path.write_bytes(b"x")
    else:
        path.write_text("中文指令\n", encoding="utf-8")


def _task(batch_dir: Path, task_id: str, *, en: bool = True) -> Path:
    task = batch_dir / task_id
    task.mkdir(parents=True, exist_ok=True)
    _write(task / f"{task_id}.jpg")
    _write(task / f"{task_id}_CH.txt")
    if en:
        _write(task / f"{task_id}_EN.txt")
    return task


def _snapshot(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _insert_batch(conn, batch_id: str, path: Path, *, name: str = "批次", source_type: str = "legacy") -> None:
    conn.execute(
        """
        INSERT INTO batches(
            id, display_id, name, submit_date, data_root, batch_path, data_count,
            status, source, identity_json, created_at, updated_at, source_type
        ) VALUES (?, ?, ?, '2026-11-08', ?, ?, 1, 'confirmed', 'scan', ?, ?, ?, ?)
        """,
        (
            batch_id,
            batch_id.upper(),
            name,
            str(path.parent),
            str(path),
            json.dumps({"team": "测试团队"}, ensure_ascii=False),
            "2026-11-08T09:00:00",
            "2026-11-08T10:00:00",
            source_type,
        ),
    )


class TreeFixture:
    def __init__(self, base: Path):
        self.base = base
        self.root = base / "项目根目录"
        self.pending = self.root / "02-待验数据"
        self.final = self.root / "03-申请终验"
        self.material = self.root / "01-素材仓库"
        self.pending.mkdir(parents=True)
        self.final.mkdir(parents=True)
        self.material.mkdir(parents=True)
        self.batch_a = self.pending / "产品背景替换" / "醒醒文化提交" / "1107" / "批次A"
        self.batch_b = (
            self.pending
            / "虚拟穿衣"
            / "类目一_1人物+1服饰"
            / "驻马店乾亿提交"
            / "1105"
            / "批次B"
        )
        _task(self.batch_a, "10000001")
        _task(self.batch_a, "10000003")
        _task(self.batch_b, "10000002")
        self.final_plain = self.final / "人物景" / "河南铸星提交" / "1109" / "合格数据目录"
        self.final_nested = (
            self.final
            / "虚拟穿衣"
            / "类目一_1人物+1服饰"
            / "驻马店乾亿提交"
            / "1105"
            / "原始批次"
            / "验收员合格数据"
        )
        self.final_shared = self.final / "人物景" / "河南铸星提交" / "1110" / "合格数据目录B"
        _task(self.final_plain, "10000004")
        _task(self.final_nested, "10000005")
        _task(self.final_shared, "10000001")
        # 素材仓库不应被扫描
        _task(self.material / "历史素材" / "旧批次", "10000009")
        # 垃圾目录应被跳过
        _task(self.pending / "_质检记录" / "旧数据", "10000008")
        _task(self.pending / "_00_Evidence_备份" / "旧数据", "10000007")
        _task(self.pending / "__MACOSX" / "旧数据", "10000006")


class TestSchemaAndProjectRoots(unittest.TestCase):
    def test_schema_8_and_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = open_database(Path(tmp) / "management.db")
            try:
                self.assertGreaterEqual(db.schema_version(), 8)
                batch_columns = {
                    row["name"] for row in db.connect().execute("PRAGMA table_info(batches)").fetchall()
                }
                for column in ("source_type", "source_root_id", "source_root_path", "relative_path", "parent_batch_id"):
                    self.assertIn(column, batch_columns)
                tables = {
                    row["name"]
                    for row in db.connect().execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                self.assertIn("project_roots", tables)
            finally:
                db.close()

    def test_detect_and_multi_project_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = TreeFixture(Path(tmp))
            service = ProjectRootService(Path(tmp) / "data" / "management.db")
            try:
                detection = service.detect_parent(str(fixture.root))
                self.assertTrue(detection.pending and detection.pending.ok)
                self.assertTrue(detection.final and detection.final.ok)
                missing = service.detect_path(str(fixture.root / "不存在"), label="待验数据目录")
                self.assertFalse(missing.ok)
                self.assertIn("不存在", missing.error)

                first = service.create("项目一", parent_path=str(fixture.root))
                second = service.create(
                    "项目二",
                    pending_path=str(fixture.pending),
                    final_path=str(fixture.final),
                )
                self.assertEqual(len(service.list()), 2)
                service.update(first.id, project_name="项目一改名")
                self.assertEqual(service.get(first.id).project_name, "项目一改名")
                self.assertEqual(service.get(second.id).project_name, "项目二")
                service.set_enabled(second.id, False)
                self.assertFalse(service.get(second.id).enabled)
                service.delete(first.id)
                self.assertIsNone(service.get(first.id))
                self.assertIsNotNone(service.get(second.id))
            finally:
                service.close()

    def test_forbidden_and_parent_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = TreeFixture(Path(tmp))
            service = ProjectRootService(Path(tmp) / "data" / "management.db")
            try:
                errors = service.validate_paths(str(fixture.material), "")
                self.assertTrue(any("不能是" in item for item in errors))
                errors = service.validate_paths(str(fixture.root), "")
                self.assertTrue(any("项目父目录" in item for item in errors))
                errors = service.validate_paths("", "")
                self.assertTrue(errors)
                with self.assertRaises(ValueError):
                    service.create("非法项目", pending_path=str(fixture.material))
            finally:
                service.close()


class TestDiscovery(unittest.TestCase):
    def test_pending_and_final_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = TreeFixture(Path(tmp))
            ruleset = load_default_ruleset()
            pending = discover_source(
                str(fixture.pending),
                SOURCE_PENDING,
                ruleset,
                project_name="测试项目",
                options=ScanOptions(max_dirs=10000, max_groups=1000, max_depth=20),
            )
            self.assertEqual(pending.status, "completed", pending.issues)
            names = {item.batch_name for item in pending.candidates}
            self.assertEqual(names, {"批次A", "批次B"})
            a = next(item for item in pending.candidates if item.batch_name == "批次A")
            self.assertEqual(a.group_count, 2)
            self.assertEqual(a.identity.get("team"), "醒醒文化")
            self.assertEqual(a.identity.get("date"), "1107")
            self.assertEqual(a.identity.get("category_l1"), "产品背景替换")
            b = next(item for item in pending.candidates if item.batch_name == "批次B")
            self.assertEqual(b.identity.get("team"), "驻马店乾亿")
            self.assertEqual(b.identity.get("category_l2"), "类目一_1人物+1服饰")
            self.assertEqual(b.identity.get("category_l1"), "虚拟穿衣")
            self.assertFalse(any("素材仓库" in item.batch_path for item in pending.candidates))

            final = discover_source(
                str(fixture.final),
                SOURCE_FINAL,
                ruleset,
                project_name="测试项目",
                options=ScanOptions(max_dirs=10000, max_groups=1000, max_depth=20),
            )
            final_names = {item.batch_name for item in final.candidates}
            self.assertIn("合格数据目录", final_names)
            self.assertIn("原始批次", final_names)
            self.assertNotIn("验收员合格数据", final_names)

    def test_forbidden_root_and_missing_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = TreeFixture(Path(tmp))
            ruleset = load_default_ruleset()
            rejected = discover_source(str(fixture.material), SOURCE_PENDING, ruleset)
            self.assertEqual(rejected.status, "failed")
            self.assertTrue(any("不允许" in issue.message for issue in rejected.issues))

            parent = discover_source(str(fixture.root), SOURCE_PENDING, ruleset)
            self.assertEqual(parent.status, "failed")
            self.assertTrue(any("父目录" in issue.message for issue in parent.issues))

            missing = discover_source(str(Path(tmp) / "断网目录"), SOURCE_PENDING, ruleset)
            self.assertEqual(missing.status, "failed")
            self.assertGreaterEqual(missing.access_failures, 1)

    def test_deep_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            deep = base / "根" / "a" / "b" / "c" / "d" / "e" / "f" / "batch" / "task"
            _task(deep, "12345678")
            ruleset = load_default_ruleset()
            result = discover_source(
                str(base / "根"),
                SOURCE_PENDING,
                ruleset,
                options=ScanOptions(max_dirs=1000, max_groups=100, max_depth=20),
            )
            self.assertEqual(result.status, "completed")
            self.assertTrue(any(group.task_id == "12345678" for group in result.groups))

    def test_identity_does_not_guess_wrong_category(self):
        identity = derive_identity(
            root_path="/root",
            batch_path="/root/大类/团队提交/1107/批次",
            batch_name="批次",
            source_type=SOURCE_PENDING,
            project_name="项目",
        )
        self.assertEqual(identity.get("team"), "团队")
        self.assertEqual(identity.get("date"), "1107")
        self.assertEqual(identity.get("category_l1"), "大类")


class TestWorkspaceRefresh(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = TreeFixture(Path(self.temp.name))
        self.db_path = Path(self.temp.name) / "data" / "management.db"
        self.service = WorkspaceService(self.db_path)
        self.project = self.service.projects.create(
            "测试项目",
            pending_path=str(self.fixture.pending),
            final_path=str(self.fixture.final),
        )

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_refresh_creates_candidates_and_is_incremental(self):
        before = _snapshot(self.fixture.root)
        result = self.service.refresh_project(self.project.id, sources=(SOURCE_PENDING,))
        summary = result["summaries"][0]
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["new_batches"], 2)
        self.assertEqual(summary["groups"], 3)
        self.assertEqual(_snapshot(self.fixture.root), before)

        conn = self.service.db.connect()
        runs = conn.execute(
            "SELECT * FROM scan_runs WHERE source_type = ? ORDER BY started_at DESC",
            (SOURCE_PENDING,),
        ).fetchall()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["project_root_id"], self.project.id)
        candidates = conn.execute(
            "SELECT COUNT(*) AS c FROM scan_candidates WHERE status = 'pending'"
        ).fetchone()["c"]
        self.assertEqual(candidates, 2)

        second = self.service.refresh_project(self.project.id, sources=(SOURCE_PENDING,))
        self.assertEqual(second["summaries"][0]["new_batches"], 0)
        self.assertEqual(second["summaries"][0]["existing_batches"], 0)
        total_candidates = conn.execute(
            "SELECT COUNT(*) AS c FROM scan_candidates WHERE status = 'pending'"
        ).fetchone()["c"]
        self.assertEqual(total_candidates, 2)

    def test_confirm_batch_and_sync_groups(self):
        self.service.refresh_project(self.project.id, sources=(SOURCE_PENDING,))
        conn = self.service.db.connect()
        candidate = conn.execute(
            "SELECT c.id FROM scan_candidates c JOIN scan_runs r ON r.id=c.run_id"
            " WHERE c.batch_name = '批次A' ORDER BY r.started_at DESC LIMIT 1"
        ).fetchone()
        batch_id = self.service.confirm_candidate(candidate["id"], operator="tester")
        row = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
        self.assertEqual(row["source_type"], SOURCE_PENDING)
        self.assertEqual(row["source_root_id"], self.project.id)
        self.assertEqual(row["source_root_path"], str(self.fixture.pending))
        self.assertTrue(row["relative_path"].endswith("批次A"))
        self.assertEqual(row["data_count"], 2)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) AS c FROM data_groups WHERE batch_id=?", (batch_id,)).fetchone()["c"],
            2,
        )

        # 新增一个任务，再次刷新应更新快照而不是重复建组
        _task(self.fixture.batch_a, "10000010")
        result = self.service.refresh_project(self.project.id, sources=(SOURCE_PENDING,))
        summary = result["summaries"][0]
        self.assertEqual(summary["existing_batches"], 1)
        self.assertGreaterEqual(summary["changed_batches"], 1)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) AS c FROM data_groups WHERE batch_id=?", (batch_id,)).fetchone()["c"],
            3,
        )
        self.assertEqual(
            conn.execute("SELECT data_count FROM batches WHERE id=?", (batch_id,)).fetchone()["data_count"],
            3,
        )

    def test_final_source_and_parent_link(self):
        self.service.refresh_project(self.project.id, sources=(SOURCE_PENDING,))
        conn = self.service.db.connect()
        candidate = conn.execute(
            "SELECT c.id FROM scan_candidates c JOIN scan_runs r ON r.id=c.run_id"
            " WHERE c.batch_name = '批次A' ORDER BY r.started_at DESC LIMIT 1"
        ).fetchone()
        parent_id = self.service.confirm_candidate(candidate["id"])

        self.service.refresh_project(self.project.id, sources=(SOURCE_FINAL,))
        candidate = conn.execute(
            "SELECT c.id FROM scan_candidates c JOIN scan_runs r ON r.id=c.run_id"
            " WHERE c.batch_name = '合格数据目录B' AND r.source_type = ?"
            " ORDER BY r.started_at DESC LIMIT 1",
            (SOURCE_FINAL,),
        ).fetchone()
        child_id = self.service.confirm_candidate(candidate["id"])
        suggestions = self.service.parent_suggestions(child_id)
        self.assertTrue(any(item["id"] == parent_id and int(item["overlap"]) >= 1 for item in suggestions))
        self.service.link_parent_batch(child_id, parent_id, operator="tester")
        self.assertEqual(
            conn.execute("SELECT parent_batch_id FROM batches WHERE id=?", (child_id,)).fetchone()[
                "parent_batch_id"
            ],
            parent_id,
        )
        self.service.clear_parent_batch(child_id)
        self.assertIsNone(
            conn.execute("SELECT parent_batch_id FROM batches WHERE id=?", (child_id,)).fetchone()[
                "parent_batch_id"
            ]
        )

    def test_same_name_batches_in_different_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            service = WorkspaceService(base / "data" / "management.db")
            project_ids = []
            batch_ids = []
            try:
                for index in (1, 2):
                    root = base / f"项目{index}" / "02-待验数据"
                    batch = root / "大类" / "团队提交" / "1107" / "同名批次"
                    _task(batch, f"4000000{index}")
                    project = service.projects.create(f"项目{index}", pending_path=str(root))
                    project_ids.append(project.id)
                    service.refresh_project(project.id, sources=(SOURCE_PENDING,))
                    candidate = service.db.connect().execute(
                        "SELECT c.id FROM scan_candidates c JOIN scan_runs r ON r.id=c.run_id"
                        " WHERE c.batch_name='同名批次' AND r.project_root_id=?"
                        " ORDER BY r.started_at DESC LIMIT 1",
                        (project.id,),
                    ).fetchone()
                    batch_ids.append(service.confirm_candidate(candidate["id"]))
                self.assertNotEqual(batch_ids[0], batch_ids[1])
                self.assertNotEqual(project_ids[0], project_ids[1])
                rows = service.db.connect().execute("SELECT * FROM batches").fetchall()
                self.assertEqual(len(rows), 2)
                self.assertEqual(
                    {row["source_root_id"] for row in rows}, set(project_ids)
                )
            finally:
                service.close()

    def test_network_failure_keeps_history(self):
        self.service.refresh_project(self.project.id, sources=(SOURCE_PENDING,))
        conn = self.service.db.connect()
        candidate = conn.execute(
            "SELECT c.id FROM scan_candidates c JOIN scan_runs r ON r.id=c.run_id"
            " WHERE c.batch_name = '批次A' ORDER BY r.started_at DESC LIMIT 1"
        ).fetchone()
        batch_id = self.service.confirm_candidate(candidate["id"])
        self.service.projects.update(
            self.project.id, pending_path=str(Path(self.temp.name) / "断网目录")
        )
        result = self.service.refresh_project(self.project.id, sources=(SOURCE_PENDING,))
        summary = result["summaries"][0]
        self.assertEqual(summary["status"], "failed")
        self.assertGreaterEqual(summary["access_failures"], 1)
        self.assertIsNotNone(
            conn.execute("SELECT id FROM batches WHERE id=?", (batch_id,)).fetchone()
        )
        self.assertGreaterEqual(
            conn.execute(
                "SELECT COUNT(*) AS c FROM data_groups WHERE batch_id=?", (batch_id,)
            ).fetchone()["c"],
            1,
        )


class TestFormatAndTranslationLinkage(unittest.TestCase):
    def _db(self, base: Path):
        return open_database(base / "data" / "management.db")

    def test_format_check_writes_batch_id_and_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            batch_path = base / "batch"
            _task(batch_path, "10000001")
            db = self._db(base)
            try:
                _insert_batch(db.connect(), "b1", batch_path, name="格式批次")
                db.connect().commit()
            finally:
                db.close()
            service = FormatCheckerService(
                db_path=base / "data" / "management.db",
                report_dir=base / "reports",
            )
            try:
                result = service.run_check(
                    FormatCheckRequest(
                        root_path=str(batch_path),
                        template_id="builtin_default",
                        recursive=True,
                        batch_id="b1",
                        operator="tester",
                        write_report=False,
                    )
                )
                self.assertEqual(result.summary.scanned_tasks, 1)
                row = service.db.connect().execute(
                    "SELECT batch_id FROM format_check_runs WHERE id = ?",
                    (result.summary.run_id,),
                ).fetchone()
                self.assertEqual(row["batch_id"], "b1")
                batch = service.db.connect().execute(
                    "SELECT format_status FROM batches WHERE id='b1'"
                ).fetchone()
                self.assertEqual(batch["format_status"], "passed")
            finally:
                service.close()

    def test_translation_writes_batch_id_and_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            batch_path = base / "batch"
            _task(batch_path, "20000001", en=False)
            db = self._db(base)
            try:
                _insert_batch(db.connect(), "b2", batch_path, name="翻译批次")
                db.connect().commit()
            finally:
                db.close()
            provider = FakeProvider()
            service = PrecheckService(
                db_path=base / "data" / "management.db",
                data_dir=base,
                provider_factory=fake_factory(provider),
                backoff_base=0.0,
            )
            try:
                service.save_profile(
                    ProviderProfile(
                        id="fake",
                        name="Fake",
                        provider="openai_compat",
                        base_url="http://fake.local/v1",
                        model="fake-model",
                    )
                )
                service.set_active_profile("fake")
                run = service.translate(
                    TranslationRequest(
                        root_path=str(batch_path),
                        profile_id="fake",
                        batch_id="b2",
                        recursive=True,
                    ),
                    api_key="test-key",
                )
                self.assertEqual(run.status, "done")
                job = service.db.connect().execute(
                    "SELECT batch_id FROM translation_jobs WHERE id = ?", (run.job_id,)
                ).fetchone()
                self.assertEqual(job["batch_id"], "b2")
                batch = service.db.connect().execute(
                    "SELECT translation_status FROM batches WHERE id='b2'"
                ).fetchone()
                self.assertIn(batch["translation_status"], {"completed", "in_progress"})
            finally:
                service.close()


class TestDashboardAndReadOnly(unittest.TestCase):
    def _db(self, base: Path):
        return open_database(base / "data" / "management.db")

    def test_workbench_status_and_detail(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            batch_path = base / "batch"
            _task(batch_path, "30000001")
            db = self._db(base)
            try:
                _insert_batch(db.connect(), "b3", batch_path, name="工作台批次", source_type=SOURCE_PENDING)
                conn = db.connect()
                conn.execute(
                    "INSERT INTO format_check_runs(id, batch_id, root_path, started_at, finished_at, status, scanned_tasks, passed_tasks, failed_tasks, warning_tasks, operator)"
                    " VALUES ('f1', 'b3', ?, '2026-11-08T09:00:00', '2026-11-08T09:10:00', 'completed', 1, 0, 1, 0, 'tester')",
                    (str(batch_path),),
                )
                conn.execute(
                    "INSERT INTO translation_jobs(id, batch_id, provider, model, status, started_at, finished_at, created_at)"
                    " VALUES ('j1', 'b3', 'fake', 'm', 'completed', '2026-11-08T10:00:00', '2026-11-08T10:10:00', '2026-11-08T10:00:00')"
                )
                conn.execute(
                    "INSERT INTO translation_items(id, job_id, task_id, folder_path, status) VALUES ('ti1', 'j1', '30000001', ?, 'failed')",
                    (str(batch_path / "30000001"),),
                )
                conn.commit()
                dashboard = BatchDashboard(db)
                row = next(item for item in dashboard.batches() if item["id"] == "b3")
                self.assertEqual(row["format_state"], "failed")
                self.assertEqual(row["translation_state"], "failed")
                self.assertEqual(row["acceptance_state"], "no_records")
                detail = dashboard.batch_detail("b3")
                self.assertEqual(detail["latest_format"]["id"], "f1")
                self.assertEqual(len(detail["format_issues"]), 0)
                self.assertEqual(detail["translation_counts"].get("failed"), 1)
                self.assertIn("format_issues", detail)
                self.assertIn("parent_suggestions", detail)
            finally:
                db.close()

    def test_read_only_discovery_and_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = TreeFixture(Path(tmp))
            before = _snapshot(fixture.root)
            service = WorkspaceService(Path(tmp) / "data" / "management.db")
            try:
                project = service.projects.create(
                    "只读项目",
                    pending_path=str(fixture.pending),
                    final_path=str(fixture.final),
                )
                service.refresh_project(project.id, sources=(SOURCE_PENDING, SOURCE_FINAL))
                self.assertEqual(_snapshot(fixture.root), before)
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
