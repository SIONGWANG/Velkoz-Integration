"""v1.25.11 测试：目录结构识别、标签/类目 CRUD、目录同步、队列与状态。

运行：
    .\\python\\python.exe tests\\test_v12511.py
"""
from __future__ import annotations

import os
import json
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.database.connection import open_database  # noqa: E402
from modules.workspace.catalog_service import (  # noqa: E402
    FORMAT_CHECKED,
    FORMAT_FAILED,
    FORMAT_NOT_CHECKED,
    MAIN_COMPLETED,
    MAIN_NEW,
    CatalogService,
)
from modules.workspace.catalog_format import (  # noqa: E402
    check_batch,
    check_batches,
    export_excel,
    latest_run,
    run_details,
)
from modules.workspace.catalog_scan import apply_scan, preview_scan  # noqa: E402
from modules.workspace import catalog_translation as tr  # noqa: E402
from modules.workspace import catalog_stats  # noqa: E402
from modules.workspace.path_schema import detect_batches  # noqa: E402
from ui.pages.catalog_workbench import (  # noqa: E402
    _bucket_label,
    _bucket_rows,
    _filter_rows,
    _next_level,
    _summary,
)

TEAMS = ["同正", "河南铸星", "醒醒文化", "驻马店乾亿", "学校"]
TEAM_ALIASES = {"驻马店": "驻马店乾亿"}
REVIEWERS = ["刘斯畅", "王少雄", "朱代"]
LEVELS = {
    "产品背景替换": 1,
    "人物景": 1,
    "学校": 1,
    "虚拟穿衣": 1,
    "类目一_1人物+1服饰": 2,
    "类目二_1人物+2服饰": 2,
}


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _group(batch: Path, task_id: str) -> Path:
    folder = batch / task_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{task_id}.jpg").write_bytes(b"jpg")
    (folder / f"{task_id}_CH.txt").write_text("中文", encoding="utf-8")
    return folder


def _record(batch: Path, reviewer_folder: str) -> Path:
    folder = batch / "_质检记录" / reviewer_folder
    folder.mkdir(parents=True, exist_ok=True)
    _write(folder / f"新标_{reviewer_folder}_验收记录.csv", "图片ID,结果\n110001,合格\n")
    return folder


def _write_xlsx(path: Path, rows: list[list[str]]) -> Path:
    shared: list[str] = []

    def add(value: str) -> int:
        shared.append(str(value))
        return len(shared) - 1

    sheet_rows = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(row):
            column = chr(65 + column_index)
            cells.append(f'<c r="{column}{row_index}" t="s"><v>{add(value)}</v></c>')
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )
    shared_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(shared)}" uniqueCount="{len(shared)}">'
        + "".join(f"<si><t>{value}</t></si>" for value in shared)
        + "</sst>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
        archive.writestr("xl/sharedStrings.xml", shared_xml)
    return path


HEADERS = ["姓名", "图片ID", "一级", "二级", "结果", "备注", "标签", "错误截图", "质检时间", "路径"]


def _write_csv(path: Path, rows: list[list[str]]) -> Path:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADERS)
        writer.writerows(rows)
    return path


def build_tree(base: Path) -> Path:
    root = base / "02-待验数据"
    # 大类 + 团队（无“提交”后缀）+ 日期
    product = root / "产品背景替换" / "同正" / "1110" / "20组产品溶图产品"
    _group(product, "110001")
    _record(product, "刘斯畅_同正111020组")

    # 大类 + 团队提交 + 日期
    person = root / "人物景" / "河南铸星提交" / "1109" / "11月6日提交-人物景-10组-返修数据"
    _group(person, "31106001")
    _record(person, "王少雄11月6日提交-人物景-10组-返修数据")

    # 大类 + 小类 + 团队提交 + 日期 + 同名嵌套批次
    virtual = (
        root
        / "虚拟穿衣"
        / "类目一_1人物+1服饰"
        / "醒醒文化提交"
        / "1109"
        / "11.9-虚拟穿衣-1服饰+1人物_不变-25组"
    )
    nested = virtual / "11.9-虚拟穿衣-1服饰+1人物_不变-25组"
    _group(nested, "10000016")

    # 试标数据：应被忽略
    trial = root / "产品背景替换" / "同正" / "1111" / "试标数据" / "60"
    _group(trial, "20260001")

    # 普通批次名 60：默认保留，可用忽略规则排除
    trial2 = root / "产品背景替换" / "同正" / "1112" / "60"
    _group(trial2, "20260002")
    return root


class TestPathSchema(unittest.TestCase):
    def test_real_shapes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = build_tree(Path(tmp))
            batches = detect_batches(
                str(root),
                reviewer_names=REVIEWERS,
                team_names=TEAMS,
                category_levels=LEVELS,
            )
            by_name = {item.name: item for item in batches}
            self.assertIn("20组产品溶图产品", by_name)
            self.assertIn("11月6日提交-人物景-10组-返修数据", by_name)
            self.assertIn("11.9-虚拟穿衣-1服饰+1人物_不变-25组", by_name)
            self.assertIn("60", by_name)

            product = by_name["20组产品溶图产品"]
            self.assertEqual(product.category_l1, "产品背景替换")
            self.assertEqual(product.category_l2, "")
            self.assertEqual(product.team, "同正")
            self.assertEqual(product.date, "1110")
            self.assertEqual(product.reviewers, ["刘斯畅"])
            self.assertEqual(len(product.data_groups), 1)

            person = by_name["11月6日提交-人物景-10组-返修数据"]
            self.assertEqual(person.category_l1, "人物景")
            self.assertEqual(person.team, "河南铸星")
            self.assertEqual(person.reviewers, ["王少雄"])

            virtual = by_name["11.9-虚拟穿衣-1服饰+1人物_不变-25组"]
            self.assertEqual(virtual.category_l1, "虚拟穿衣")
            self.assertEqual(virtual.category_l2, "类目一_1人物+1服饰")
            self.assertEqual(virtual.team, "醒醒文化")
            self.assertEqual(virtual.segments.count("11.9-虚拟穿衣-1服饰+1人物_不变-25组"), 1)

    def test_ignore_patterns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = build_tree(Path(tmp))
            batches = detect_batches(
                str(root),
                reviewer_names=REVIEWERS,
                team_names=TEAMS,
                category_levels=LEVELS,
                ignore_patterns=["60"],
            )
            self.assertNotIn("60", {item.name for item in batches})

    def test_missing_levels_do_not_misassign(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "02-待验数据"
            batch = root / "人物景" / "河南铸星提交" / "1109" / "批次A"
            _group(batch, "31106001")
            # 只有大类，没有小类/团队/日期
            simple = root / "学校" / "批次B"
            _group(simple, "60122354")
            batches = detect_batches(
                str(root),
                reviewer_names=REVIEWERS,
                team_names=TEAMS,
                category_levels=LEVELS,
            )
            by_name = {item.name: item for item in batches}
            self.assertEqual(by_name["批次A"].category_l1, "人物景")
            self.assertEqual(by_name["批次A"].category_l2, "")
            self.assertEqual(by_name["批次A"].team, "河南铸星")
            self.assertEqual(by_name["批次B"].category_l1, "学校")
            self.assertEqual(by_name["批次B"].category_l2, "")
            self.assertEqual(by_name["批次B"].team, "")


class TestCatalogService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = build_tree(self.base)
        self.db = open_database(self.base / "data" / "management.db")
        self.service = CatalogService(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_migration_and_tag_crud(self):
        self.assertEqual(self.db.schema_version(), 14)
        self.assertEqual(
            {row["name"] for row in self.service.teams()},
            set(TEAMS),
        )
        self.assertEqual(
            {row["name"] for row in self.service.reviewers()},
            set(REVIEWERS),
        )
        self.assertIn("产品背景替换", self.service.category_levels())

        team_id = self.service.save_tag("catalog_teams", "测试团队", aliases="测试")
        self.assertIn("测试团队", {row["name"] for row in self.service.teams()})
        self.service.delete_tag("catalog_teams", team_id)
        self.assertNotIn("测试团队", {row["name"] for row in self.service.teams()})

        category_id = self.service.save_category("测试大类", 1, image_count=3)
        self.assertEqual(self.service.category_levels().get("测试大类"), 1)
        self.service.delete_category(category_id)
        self.assertNotIn("测试大类", self.service.category_levels())

    def test_sync_batches_groups_and_reviewers(self):
        result = self.service.sync_root(
            project_id="p1",
            source_type="pending_acceptance",
            root=str(self.root),
        )
        self.assertEqual(result.batches, 4)
        self.assertEqual(result.data_groups, 4)

        rows = {row["name"]: row for row in self.service.batches(project_id="p1")}
        product = rows["20组产品溶图产品"]
        self.assertEqual(product["team_name"], "同正")
        self.assertEqual(product["reviewer_name"], "刘斯畅")
        self.assertEqual(product["category_l1"], "产品背景替换")
        self.assertEqual(product["submit_date"], "1110")
        self.assertEqual(product["main_status"], MAIN_NEW)
        self.assertEqual(product["data_count"], 1)

        person = rows["11月6日提交-人物景-10组-返修数据"]
        self.assertEqual(person["reviewer_name"], "王少雄")
        self.assertEqual(person["team_name"], "河南铸星")
        self.assertFalse(person["reviewer_conflict"])

        nested = rows["11.9-虚拟穿衣-1服饰+1人物_不变-25组"]
        self.assertEqual(nested["category_l2"], "类目一_1人物+1服饰")
        self.assertEqual(nested["data_count"], 1)
        self.assertEqual(len(self.service.groups(nested["id"])), 1)

        # 再次同步不应重复创建
        again = self.service.sync_root(
            project_id="p1",
            source_type="pending_acceptance",
            root=str(self.root),
        )
        self.assertEqual(again.created, 0)
        self.assertEqual(again.updated, 4)
        self.assertEqual(len(self.service.batches(project_id="p1")), 4)

    def test_team_alias_merges_to_canonical(self):
        alias_root = self.base / "alias_root" / "02-待验数据"
        batch = alias_root / "产品背景替换" / "驻马店提交" / "1110" / "批次Z"
        _group(batch, "110001")
        self.service.sync_root(
            project_id="alias",
            source_type="pending_acceptance",
            root=str(alias_root),
        )
        row = next(row for row in self.service.batches("alias") if row["name"] == "批次Z")
        self.assertEqual(row["team_name"], TEAM_ALIASES["驻马店"])
        self.assertEqual(row["team_id"], "team_zhumadian_qianyi")
        team = next(item for item in self.service.teams() if item["id"] == "team_zhumadian_qianyi")
        self.assertIn("驻马店", team["aliases"])

    def test_ignore_patterns_hide_batches(self):
        self.service.save_ignore_patterns(["60"])
        self.assertEqual(self.service.ignore_patterns(), ["60"])
        result = self.service.sync_root(
            project_id="p1",
            source_type="pending_acceptance",
            root=str(self.root),
        )
        self.assertEqual(result.batches, 3)
        self.assertNotIn("60", {row["name"] for row in self.service.batches()})

    def test_queue_and_status(self):
        self.service.sync_root(
            project_id="p1",
            source_type="pending_acceptance",
            root=str(self.root),
        )
        batch = next(row for row in self.service.batches() if row["name"] == "20组产品溶图产品")
        queue_id = self.service.queue_add(batch["id"], "scan")
        self.assertEqual(len(self.service.queue_list("scan")), 1)
        self.assertEqual(self.service.queue_list("scan")[0]["batch_name"], "20组产品溶图产品")
        self.service.queue_remove(queue_id)
        self.assertEqual(self.service.queue_list("scan"), [])

        self.service.set_main_status(batch["id"], MAIN_COMPLETED, operator="tester", reason="测试确认")
        updated = self.service.batch(batch["id"])
        self.assertEqual(updated["main_status"], MAIN_COMPLETED)
        logs = self.service.status_logs(batch["id"])
        self.assertEqual(logs[0]["from_status"], MAIN_NEW)
        self.assertEqual(logs[0]["to_status"], MAIN_COMPLETED)

    def test_group_cards(self):
        self.service.sync_root(
            project_id="p1",
            source_type="pending_acceptance",
            root=str(self.root),
        )
        cards = {row["name"]: row for row in self.service.group_cards("p1", level=1)}
        self.assertEqual(cards["产品背景替换"]["batches"], 2)
        self.assertEqual(cards["产品背景替换"]["data_count"], 2)
        self.assertEqual(cards["产品背景替换"]["pending_batches"], 2)
        self.assertEqual(cards["人物景"]["batches"], 1)
        self.assertEqual(cards["虚拟穿衣"]["batches"], 1)

        product = next(row for row in self.service.batches("p1") if row["name"] == "20组产品溶图产品")
        self.service.set_main_status(product["id"], MAIN_COMPLETED, operator="tester", reason="测试完成")
        cards = {row["name"]: row for row in self.service.group_cards("p1", level=1)}
        self.assertEqual(cards["产品背景替换"]["completed_batches"], 1)
        self.assertEqual(cards["产品背景替换"]["completed_groups"], 1)
        self.assertEqual(cards["产品背景替换"]["pending_batches"], 1)
        self.assertEqual(cards["产品背景替换"]["pending_groups"], 1)


class TestCardHierarchy(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {
                "id": "1",
                "name": "A-无小类",
                "category_l1": "A",
                "category_l2": "",
                "category_l3": "",
                "data_count": 10,
                "main_status": MAIN_NEW,
            },
            {
                "id": "2",
                "name": "A-B-已完成",
                "category_l1": "A",
                "category_l2": "B",
                "category_l3": "",
                "data_count": 5,
                "main_status": MAIN_COMPLETED,
            },
            {
                "id": "3",
                "name": "A-B-C",
                "category_l1": "A",
                "category_l2": "B",
                "category_l3": "C",
                "data_count": 2,
                "main_status": MAIN_NEW,
            },
        ]

    def test_next_level_skips_missing_layers(self):
        self.assertEqual(_next_level(self.rows, 0), 1)
        self.assertEqual(_next_level(self.rows, 1), 2)
        self.assertEqual(_next_level(self.rows, 2), 3)
        self.assertEqual(_next_level(self.rows, 3), 4)

        no_l1 = [
            {"category_l1": "", "category_l2": "B", "category_l3": "", "data_count": 1},
        ]
        self.assertEqual(_next_level(no_l1, 0), 2)
        no_categories = [{"category_l1": "", "category_l2": "", "category_l3": "", "data_count": 1}]
        self.assertEqual(_next_level(no_categories, 0), 4)

    def test_summary_uses_batch_and_data_counts(self):
        data = _summary(self.rows)
        self.assertEqual(data["batches"], 3)
        self.assertEqual(data["data"], 17)
        self.assertEqual(data["completed_batches"], 1)
        self.assertEqual(data["completed_data"], 5)
        self.assertEqual(data["pending_batches"], 2)
        self.assertEqual(data["pending_data"], 12)

    def test_bucket_and_filter_keep_rows_without_subcategory(self):
        buckets = {item["name"]: item for item in _bucket_rows(self.rows, 2)}
        self.assertEqual(set(buckets), {"", "B"})
        self.assertEqual(_bucket_label("", 2), "未设置小类")
        self.assertEqual(len(buckets[""]["rows"]), 1)

        path = [{"level": 1, "name": "A"}, {"level": 2, "name": "B"}]
        filtered = _filter_rows(self.rows, path)
        self.assertEqual({row["id"] for row in filtered}, {"2", "3"})
        self.assertEqual(_next_level(filtered, 2), 3)

        without_sub = _filter_rows(self.rows, [{"level": 1, "name": "A"}, {"level": 2, "name": ""}])
        self.assertEqual({row["id"] for row in without_sub}, {"1"})
        self.assertEqual(_next_level(without_sub, 2), 4)


class TestCatalogScan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / "02-待验数据"
        self.db = open_database(self.base / "data" / "management.db")
        self.service = CatalogService(self.db)
        self._build_scan_tree()
        self.service.sync_root(
            project_id="p1",
            source_type="pending_acceptance",
            root=str(self.root),
        )
        self.rows = {row["name"]: row for row in self.service.batches("p1")}

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _batch(self, name: str, groups: list[str]) -> Path:
        batch = self.root / "产品背景替换" / "同正" / "1110" / name
        for group in groups:
            _group(batch, group)
        return batch

    def _build_scan_tree(self):
        csv_batch = self._batch("批次CSV", ["100001", "100002", "100003"])
        folder = csv_batch / "_质检记录" / "刘斯畅_批次CSV"
        _write_csv(
            folder / "新标_刘斯畅_批次CSV_验收记录.csv",
            [
                ["刘斯畅", "100001", "产品背景替换", "", "合格", "", "标签A", "_00_Evidence_新标/100001.png", "2026-11-10 10:00:00", "100001"],
                ["刘斯畅", "100002", "产品背景替换", "", "不合格", "原因B", "标签B", "_00_Evidence_新标/100002.png", "2026-11-10 11:00:00", "100002"],
                ["刘斯畅", "100003", "产品背景替换", "", "修改后合格", "", "", "", "", "100003"],
            ],
        )
        _write_csv(
            folder / "新标_刘斯畅_批次CSV_验收记录.csv.bak",
            [
                ["刘斯畅", "100001", "产品背景替换", "", "不合格", "", "", "", "", "100001"],
            ],
        )
        _write_xlsx(
            folder / "新标_刘斯畅_批次CSV_验收记录.xlsx",
            [HEADERS, ["刘斯畅", "100001", "", "", "不合格", "", "", "", "", ""]],
        )

        conflict = self._batch("批次冲突", ["200001", "200002"])
        _write_csv(
            conflict / "_质检记录" / "刘斯畅_冲突" / "记录.csv",
            [["刘斯畅", "200001", "", "", "合格", "", "", "", "", ""]],
        )
        _write_csv(
            conflict / "_质检记录" / "王少雄_冲突" / "记录.csv",
            [["王少雄", "200002", "", "", "不合格", "原因", "标签", "", "", ""]],
        )

        empty = self._batch("批次空", ["300001"])
        (empty / "_质检记录" / "朱代_空").mkdir(parents=True, exist_ok=True)

        xlsx = self._batch("批次XLSX", ["400001"])
        xlsx_folder = xlsx / "_质检记录" / "王少雄_XLSX"
        xlsx_folder.mkdir(parents=True, exist_ok=True)
        _write_xlsx(
            xlsx_folder / "记录.xlsx",
            [HEADERS, ["王少雄", "400001", "", "", "合格", "", "", "", "", ""]],
        )

    def test_scan_csv_records_and_status(self):
        batch = self.rows["批次CSV"]
        self.service.queue_add(batch["id"], "scan")
        preview = preview_scan(self.db, batch["id"])
        self.assertEqual(preview.reviewers, ["刘斯畅"])
        self.assertFalse(preview.conflict)
        self.assertEqual(preview.file_count, 1)
        self.assertTrue(any("XLSX" in warning for folder in preview.folders for warning in folder.warnings))

        result = apply_scan(self.db, batch["id"], preview.reviewers)
        self.assertTrue(result.applied)
        self.assertEqual(result.inserted, 3)
        self.assertEqual(result.qualified, 2)
        self.assertEqual(result.rejected, 1)
        self.assertEqual(result.remaining, 0)

        updated = self.service.batch(batch["id"])
        self.assertEqual(updated["main_status"], MAIN_COMPLETED)
        self.assertEqual(updated["reviewer_name"], "刘斯畅")
        self.assertEqual(updated["qualified_count"], 2)
        self.assertEqual(updated["rejected_count"], 1)

        records = self.service.records(batch["id"])
        by_number = {row["number"]: row for row in records}
        self.assertEqual(len(records), 3)
        self.assertEqual(by_number["100001"]["result"], "合格")
        self.assertEqual(by_number["100001"]["qualified"], 1)
        self.assertEqual(by_number["100002"]["result"], "不合格")
        self.assertEqual(by_number["100002"]["reason"], "原因B")
        self.assertEqual(by_number["100002"]["tags"], "标签B")
        self.assertTrue(
            by_number["100001"]["evidence"].endswith(
                os.path.join("_00_Evidence_新标", "100001.png")
            )
        )
        self.assertTrue(by_number["100001"]["evidence"].startswith(str(batch["batch_path"])))

        groups = {row["task_id"]: row for row in self.service.groups(batch["id"])}
        self.assertEqual(groups["100001"]["status"], "qualified")
        self.assertEqual(groups["100002"]["status"], "rejected")
        self.assertEqual(groups["100001"]["image_count"], 1)
        self.assertEqual(groups["100001"]["txt_count"], 1)
        queue_item = self.service.queue_list("scan")[0]
        self.assertEqual(queue_item["status"], "done")

    def test_conflict_binding_modes(self):
        batch = self.rows["批次冲突"]
        preview = preview_scan(self.db, batch["id"])
        self.assertEqual(preview.reviewers, ["刘斯畅", "王少雄"])
        self.assertTrue(preview.conflict)

        skipped = apply_scan(self.db, batch["id"], [])
        self.assertFalse(skipped.applied)
        self.assertEqual(self.service.batch(batch["id"])["main_status"], MAIN_NEW)
        self.assertEqual(self.service.record_count(batch["id"]), 0)

        one = apply_scan(self.db, batch["id"], ["刘斯畅"])
        self.assertTrue(one.applied)
        self.assertEqual(one.inserted, 1)
        self.assertEqual(self.service.batch(batch["id"])["reviewer_name"], "刘斯畅")
        records = self.service.records(batch["id"])
        self.assertEqual([row["number"] for row in records], ["200001"])

        both = apply_scan(self.db, batch["id"], preview.reviewers)
        self.assertTrue(both.applied)
        self.assertEqual(both.inserted, 2)
        self.assertEqual(both.qualified, 1)
        self.assertEqual(both.rejected, 1)
        updated = self.service.batch(batch["id"])
        self.assertEqual(updated["reviewer_name"], "刘斯畅、王少雄")
        self.assertEqual(updated["main_status"], MAIN_COMPLETED)

    def test_empty_folder_keeps_new(self):
        batch = self.rows["批次空"]
        preview = preview_scan(self.db, batch["id"])
        self.assertEqual(preview.reviewers, ["朱代"])
        self.assertEqual(preview.file_count, 0)
        result = apply_scan(self.db, batch["id"], preview.reviewers)
        self.assertFalse(result.applied)
        self.assertEqual(self.service.batch(batch["id"])["main_status"], MAIN_NEW)
        self.assertEqual(self.service.record_count(batch["id"]), 0)

    def test_xlsx_is_fallback(self):
        batch = self.rows["批次XLSX"]
        preview = preview_scan(self.db, batch["id"])
        self.assertEqual(preview.file_count, 1)
        self.assertTrue(preview.folders[0].files[0].endswith(".xlsx"))
        result = apply_scan(self.db, batch["id"], preview.reviewers)
        self.assertTrue(result.applied)
        self.assertEqual(result.inserted, 1)
        records = self.service.records(batch["id"])
        self.assertEqual(records[0]["number"], "400001")
        self.assertEqual(records[0]["result"], "合格")


class TestCatalogFormat(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = build_tree(self.base)
        # 额外批次：与“20组产品溶图产品”共用编号 110001，用于查重
        duplicate = self.root / "学校" / "批次重复编号"
        _group(duplicate, "110001")
        self.db = open_database(self.base / "data" / "management.db")
        self.service = CatalogService(self.db)
        self.service.sync_root(
            project_id="p1",
            source_type="pending_acceptance",
            root=str(self.root),
        )
        self.rows = {row["name"]: row for row in self.service.batches("p1")}
        self.duplicate_id = self.rows["批次重复编号"]["id"]
        self.db.connect().execute(
            "UPDATE catalog_groups SET status = 'qualified' WHERE batch_id = ?",
            (self.duplicate_id,),
        )
        self.db.connect().commit()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _set_rule(self, name: str, level: int, count: int) -> None:
        self.service.save_category(name, level, image_count=count)

    def test_rule_fallback_and_pass_fail(self):
        self._set_rule("产品背景替换", 1, 1)
        batch = self.rows["20组产品溶图产品"]
        result = check_batch(self.db, batch["id"])
        self.assertEqual(result.rule.source, "大类")
        self.assertEqual(result.rule.expected, 1)
        self.assertEqual(result.status, FORMAT_CHECKED)
        self.assertEqual(result.total, 1)
        self.assertEqual(result.passed, 1)
        self.assertEqual(result.failed, 0)
        self.assertEqual(result.duplicates, 1)
        self.assertEqual(result.duplicate_hits[0]["number"], "110001")
        self.assertEqual(
            self.service.batch(batch["id"])["format_status"], FORMAT_CHECKED
        )
        run = latest_run(self.db, batch["id"])
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], FORMAT_CHECKED)
        details = run_details(self.db, run["id"])
        self.assertEqual(len(details), 1)
        self.assertEqual(details[0]["actual_images"], 1)
        self.assertEqual(details[0]["status"], "passed")
        logs = self.service.status_logs(batch["id"])
        self.assertTrue(any(item["kind"] == "format" for item in logs))

        self._set_rule("产品背景替换", 1, 2)
        failed = check_batch(self.db, batch["id"])
        self.assertEqual(failed.status, FORMAT_FAILED)
        self.assertEqual(failed.failed, 1)
        self.assertEqual(failed.passed, 0)
        self.assertIn("期望 2", failed.details[0].reason)
        self.assertEqual(
            self.service.batch(batch["id"])["format_status"], FORMAT_FAILED
        )

    def test_l2_rule_and_unconfigured_kept(self):
        self._set_rule("类目一_1人物+1服饰", 2, 1)
        virtual = self.rows["11.9-虚拟穿衣-1服饰+1人物_不变-25组"]
        result = check_batch(self.db, virtual["id"])
        self.assertEqual(result.rule.source, "小类")
        self.assertEqual(result.rule.category, "类目一_1人物+1服饰")
        self.assertEqual(result.status, FORMAT_CHECKED)
        self.assertEqual(result.passed, 1)

        person = self.rows["11月6日提交-人物景-10组-返修数据"]
        before = self.service.batch(person["id"])["format_status"]
        skipped = check_batch(self.db, person["id"])
        self.assertEqual(skipped.status, FORMAT_NOT_CHECKED)
        self.assertEqual(skipped.skipped, 1)
        self.assertEqual(
            self.service.batch(person["id"])["format_status"], before
        )
        self.assertEqual(before, FORMAT_NOT_CHECKED)

    def test_batch_summary_and_excel(self):
        self._set_rule("产品背景替换", 1, 1)
        product_id = self.rows["20组产品溶图产品"]["id"]
        summary = check_batches(self.db, [product_id, self.rows["60"]["id"]])
        self.assertEqual(summary.batches, 2)
        self.assertEqual(summary.checked, 2)
        self.assertEqual(summary.skipped, 0)
        self.assertEqual(summary.total, 2)
        self.assertEqual(summary.passed, 2)
        self.assertEqual(summary.duplicates, 1)
        page_summary = summary.to_dict()
        self.assertEqual(len(page_summary["results"]), 2)
        self.assertTrue(page_summary["results"][0]["details"])

        out = self.base / "reports" / "format.xlsx"
        export_excel(self.db, summary, str(out), source_root=str(self.root / "02-待验数据"))
        self.assertTrue(out.is_file())
        with zipfile.ZipFile(out) as archive:
            workbook = archive.read("xl/workbook.xml").decode("utf-8")
        self.assertIn("检查明细", workbook)
        self.assertIn("重复编号", workbook)


class _MockToolHandler(BaseHTTPRequestHandler):
    root = ""
    payloads: list[dict] = []
    fail_numbers: set[str] = set()

    def log_message(self, *_args):  # 静默
        return

    def _json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/health"):
            self._json({"status": "ok", "app": "ai_precheck", "version": "3.6", "port": 0})
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError:
            self._json({"error": "bad json"}, 400)
            return
        type(self).payloads.append({"path": self.path, "payload": payload})
        if self.path.startswith("/api/scan"):
            folders = []
            base = Path(payload.get("path") or "")
            if base.is_dir():
                for entry in sorted(base.iterdir()):
                    if not entry.is_dir() or not entry.name.isdigit() or len(entry.name) != 8:
                        continue
                    names = [item.name for item in entry.iterdir() if item.is_file()]
                    folders.append(
                        {
                            "path": entry.name,
                            "name": entry.name,
                            "has_ch": any(name.endswith("_CH.txt") for name in names),
                            "has_en": any(name.endswith("_EN.txt") for name in names),
                        }
                    )
            self._json({"success": True, "folders": folders})
            return
        if self.path.startswith("/api/translate"):
            results = []
            for folder in payload.get("folders") or []:
                number = str(folder.get("name") or "")
                if number in type(self).fail_numbers:
                    results.append(
                        {"folder": number, "file": f"{number}_CH.txt", "status": "fail", "reason": "模拟失败"}
                    )
                    continue
                target = Path(self.root) / str(folder.get("path") or number) / f"{number}_EN.txt"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f"EN:{number}", encoding="utf-8")
                results.append(
                    {
                        "folder": number,
                        "file": f"{number}_CH.txt",
                        "status": "success",
                        "reason": "",
                        "tokens": 10,
                        "cost": 0.001,
                    }
                )
            self._json(
                {
                    "success": True,
                    "results": results,
                    "stats": {"success": len(results), "fail": 0},
                    "message": "",
                }
            )
            return
        self._json({"error": "not found"}, 404)


class TestCatalogTranslation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _MockToolHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.final_root = self.base / "03-申请终验"
        batch = self.final_root / "人物景" / "学校提交" / "2026-11-04"
        with_ch_en = batch / "60000001"
        with_ch_en.mkdir(parents=True)
        (with_ch_en / "60000001_CH.txt").write_text("中文一", encoding="utf-8")
        (with_ch_en / "60000001_EN.txt").write_text("old en", encoding="utf-8")
        ch_only = batch / "60000002"
        ch_only.mkdir(parents=True)
        (ch_only / "60000002_CH.txt").write_text("中文二", encoding="utf-8")
        en_only = batch / "60000003"
        en_only.mkdir(parents=True)
        (en_only / "60000003_EN.txt").write_text("only en", encoding="utf-8")

        _MockToolHandler.root = str(batch)
        _MockToolHandler.payloads = []
        _MockToolHandler.fail_numbers = set()

        self.db = open_database(self.base / "data" / "management.db")
        self.service = CatalogService(self.db)
        self.service.save_category("人物景", 1)
        self.service.sync_root(
            project_id="p1",
            source_type="final_acceptance",
            root=str(self.final_root),
        )
        self.batch = next(
            row for row in self.service.batches("p1") if row["name"] == "2026-11-04"
        )
        tr.save_settings(
            self.db,
            tr.TranslationSettings(base_url=f"http://127.0.0.1:{self.port}", mode="skip"),
        )

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_preview_is_read_only(self):
        before = (Path(self.batch["batch_path"]) / "60000001" / "60000001_EN.txt").read_text(
            encoding="utf-8"
        )
        with mock.patch.object(tr, "load_api_key", return_value="test-key"):
            preview = tr.preview_batch(self.db, self.batch["id"])
        self.assertEqual(preview.total, 3)
        self.assertEqual(preview.to_translate, 1)
        self.assertEqual(preview.missing_ch, 1)
        self.assertEqual(preview.skipped, 1)
        self.assertEqual(
            [item["path"] for item in _MockToolHandler.payloads], ["/api/scan"]
        )
        after = (Path(self.batch["batch_path"]) / "60000001" / "60000001_EN.txt").read_text(
            encoding="utf-8"
        )
        self.assertEqual(before, after)

    def test_translate_skip_then_all_with_backup(self):
        with mock.patch.object(tr, "load_api_key", return_value="test-key"):
            skipped = tr.translate_batch(self.db, self.batch["id"])
            self.assertEqual(skipped.translated, 1)
            self.assertEqual(skipped.failed, 0)
            self.assertEqual(skipped.status, "translated")
            self.assertEqual(skipped.items[0].number, "60000002")
            self.assertFalse(skipped.backup_dir)
            new_en = Path(self.batch["batch_path"]) / "60000002" / "60000002_EN.txt"
            self.assertEqual(new_en.read_text(encoding="utf-8"), "EN:60000002")
            # 已存在的英文在 skip 模式下不被覆盖
            old_en = Path(self.batch["batch_path"]) / "60000001" / "60000001_EN.txt"
            self.assertEqual(old_en.read_text(encoding="utf-8"), "old en")
            translate_payload = [
                item for item in _MockToolHandler.payloads if item["path"] == "/api/translate"
            ][-1]["payload"]
            self.assertEqual(translate_payload["api_key"], "test-key")
            self.assertEqual([f["name"] for f in translate_payload["folders"]], ["60000002"])

            tr.save_settings(
                self.db,
                tr.TranslationSettings(base_url=f"http://127.0.0.1:{self.port}", mode="all"),
            )
            result = tr.translate_batch(self.db, self.batch["id"])
            self.assertEqual(result.translated, 2)
            self.assertEqual(result.failed, 0)
            self.assertTrue(result.backup_dir)
            backup = Path(result.backup_dir)
            self.assertTrue((backup / "60000001" / "60000001_EN.txt").is_file())
            self.assertEqual(
                (backup / "60000001" / "60000001_EN.txt").read_text(encoding="utf-8"),
                "old en",
            )
            self.assertEqual(old_en.read_text(encoding="utf-8"), "EN:60000001")
            self.assertEqual(
                self.service.batch(self.batch["id"])["translation_status"], "translated"
            )
            logs = self.service.status_logs(self.batch["id"])
            self.assertTrue(any(item["kind"] == "translation" for item in logs))
            self.assertTrue(tr.latest_run(self.db, self.batch["id"]))

    def test_failure_and_missing_key(self):
        _MockToolHandler.fail_numbers = {"60000002"}
        with mock.patch.object(tr, "load_api_key", return_value="test-key"):
            result = tr.translate_batch(self.db, self.batch["id"])
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.translated, 0)
        self.assertEqual(result.status, "translation_failed")
        self.assertEqual(
            self.service.batch(self.batch["id"])["translation_status"], "translation_failed"
        )

        with mock.patch.object(tr, "load_api_key", return_value=""):
            with self.assertRaises(tr.TranslationError):
                tr.translate_batch(self.db, self.batch["id"])


class TestCatalogStats(unittest.TestCase):
    def test_overview_and_breakdowns(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = build_tree(base)
            db = open_database(base / "data" / "management.db")
            try:
                service = CatalogService(db)
                service.sync_root(
                    project_id="p1",
                    source_type="pending_acceptance",
                    root=str(root),
                )
                conn = db.connect()
                groups = conn.execute(
                    "SELECT id FROM catalog_groups ORDER BY task_id LIMIT 2"
                ).fetchall()
                conn.execute(
                    "UPDATE catalog_groups SET status='qualified' WHERE id=?",
                    (groups[0]["id"],),
                )
                conn.execute(
                    "UPDATE catalog_groups SET status='rejected' WHERE id=?",
                    (groups[1]["id"],),
                )
                conn.commit()
                overview = catalog_stats.overview(db, "p1", "pending_acceptance")
                self.assertEqual(overview["batches"], 4)
                self.assertEqual(overview["data"], 4)
                self.assertEqual(overview["qualified"], 1)
                self.assertEqual(overview["rejected"], 1)
                self.assertEqual(overview["pending"], 2)
                self.assertEqual(overview["qualified_rate"], 25.0)
                levels = {
                    row["name"]: row
                    for row in catalog_stats.by_level(db, 1, "p1", "pending_acceptance")
                }
                self.assertIn("产品背景替换", levels)
                self.assertEqual(levels["产品背景替换"]["data"], 2)
                teams = {
                    row["name"]: row
                    for row in catalog_stats.by_team(db, "p1", "pending_acceptance")
                }
                self.assertIn("河南铸星", teams)
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
