"""v1.25.4 测试：只读发现、类型识别、CSV/Excel 解析、关联、去重与导入日志。

运行：
    .\\python\\python.exe tests\\test_v1254.py
"""
from __future__ import annotations

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
from modules.record_import.discovery import discover_files  # noqa: E402
from modules.record_import.errors import ERROR_CATALOG  # noqa: E402
from modules.record_import.models import (  # noqa: E402
    TYPE_BACKUP,
    TYPE_EXPORT_CSV,
    TYPE_LEGACY_CSV,
    TYPE_OTHER_STATS,
    TYPE_UNCLASSIFIED,
)
from modules.record_import.parser import (  # noqa: E402
    classify_table,
    inspect_table,
    parse_rows,
    row_fingerprint,
)
from modules.record_import.service import RecordImportService  # noqa: E402

CSV_HEADER = "姓名,图片ID,一级,二级,结果,备注,标签,错误截图,质检时间,路径"


def make_csv(path: Path, rows: list[list[str]], encoding: str = "utf-8") -> Path:
    lines = [CSV_HEADER]
    lines.extend(",".join(row) for row in rows)
    path.write_text("\n".join(lines) + "\n", encoding=encoding)
    return path


def write_xlsx(path: Path, rows: list[list[str]]) -> Path:
    shared: list[str] = []

    def add(value: str) -> int:
        shared.append(str(value))
        return len(shared) - 1

    sheet_rows = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(row):
            column = chr(65 + column_index)
            cells.append(
                f'<c r="{column}{row_index}" t="s"><v>{add(value)}</v></c>'
            )
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


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def seed_batch(db_path: Path) -> None:
    db = open_database(db_path)
    try:
        conn = db.connect()
        conn.execute(
            "INSERT INTO batches(id, display_id, name, data_root, status, data_count)"
            " VALUES ('batch_1', 'B001', '批次一', 'root', 'confirmed', 2)"
        )
        conn.execute(
            "INSERT INTO data_groups(id, batch_id, task_id, normalized_path, status)"
            " VALUES ('grp_1', 'batch_1', '11111111', 'p1', 'pending')"
        )
        conn.execute(
            "INSERT INTO data_groups(id, batch_id, task_id, normalized_path, status)"
            " VALUES ('grp_2', 'batch_1', '22222222', 'p2', 'pending')"
        )
        conn.commit()
    finally:
        db.close()


class TestSchemaAndParser(unittest.TestCase):
    def test_schema_version_and_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = open_database(Path(tmp) / "management.db")
            try:
                self.assertEqual(db.schema_version(), 7)
                tables = {
                    row["name"]
                    for row in db.connect().execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertIn("record_imports", tables)
                self.assertIn("imported_records", tables)
                columns = {
                    row["name"]
                    for row in db.connect().execute("PRAGMA table_info(record_imports)")
                }
                for column in (
                    "source_path",
                    "source_type",
                    "rows_total",
                    "rows_new",
                    "rows_skipped",
                    "rows_error",
                    "operator_detected",
                    "batch_id",
                    "status",
                ):
                    self.assertIn(column, columns)
                record_columns = {
                    row["name"]
                    for row in db.connect().execute(
                        "PRAGMA table_info(imported_records)"
                    )
                }
                for column in (
                    "import_id",
                    "data_group_id",
                    "task_id",
                    "tags_raw",
                    "evidence_raw",
                    "record_time",
                    "source_row_fingerprint",
                    "link_status",
                ):
                    self.assertIn(column, record_columns)
            finally:
                db.close()

    def test_error_catalog_has_expected_codes(self):
        codes = set(ERROR_CATALOG)
        for code in ("RI-000", "RI-004", "RI-005", "RI-009", "RI-012", "RI-014"):
            self.assertIn(code, codes)

    def test_classify_types_and_backup_priority(self):
        record = ["姓名", "图片ID", "结果", "备注", "标签"]
        stats = ["日期", "合格数", "不合格数", "通过率"]
        self.assertEqual(classify_table("工作记录.csv", record), TYPE_LEGACY_CSV)
        self.assertEqual(classify_table("导出记录.csv", record), TYPE_EXPORT_CSV)
        self.assertEqual(classify_table("备份_记录.csv", record), TYPE_BACKUP)
        self.assertEqual(classify_table("统计汇总.csv", stats), TYPE_OTHER_STATS)
        self.assertEqual(
            classify_table("统计汇总.csv", ["日期", "合格数", "备注"]),
            TYPE_OTHER_STATS,
        )
        self.assertEqual(
            classify_table("纯备注.csv", ["备注", "标签"]), TYPE_LEGACY_CSV
        )
        self.assertEqual(
            classify_table("备份_统计汇总.csv", stats), TYPE_BACKUP
        )
        self.assertEqual(classify_table("随便.csv", ["A", "B"]), TYPE_UNCLASSIFIED)

    def test_row_fingerprint_depends_on_source_and_values(self):
        values = ["张三", "111", "合格"]
        first = row_fingerprint("C:/a.csv", values)
        self.assertEqual(first, row_fingerprint("c:/a.csv", values))
        self.assertNotEqual(first, row_fingerprint("C:/b.csv", values))
        self.assertNotEqual(first, row_fingerprint("C:/a.csv", ["张三", "111", "不合格"]))


class TestDiscoveryAndParsing(unittest.TestCase):
    def test_discovery_types_and_encoding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sub").mkdir()
            make_csv(root / "工作记录.csv", [["张三", "11111111", "A", "B", "合格", "", "标签一", "", "", ""]])
            make_csv(
                root / "gbk记录.csv",
                [["李四", "22222222", "A", "B", "不合格", "备注", "标签", "", "", ""]],
                encoding="gb18030",
            )
            make_csv(root / "sub" / "导出明细.csv", [["王五", "33333333", "A", "B", "合格", "", "", "", "", ""]])
            make_csv(root / "备份_记录.csv", [["赵六", "44444444", "A", "B", "合格", "", "", "", "", ""]])
            (root / "统计汇总.csv").write_text(
                "日期,合格数,不合格数,通过率\n2026-11-01,10,2,83%\n", encoding="utf-8"
            )
            (root / "随便.csv").write_text("列A,列B\n1,2\n", encoding="utf-8")
            (root / "旧表.xls").write_bytes(b"\xd0\xcf\x11\xe0")
            write_xlsx(
                root / "表格.xlsx",
                [["姓名", "图片ID", "结果", "备注", "标签"], ["周八", "55555555", "合格", "xlsx", "标签X"]],
            )

            flat = discover_files(str(root))
            self.assertEqual(flat.scanned_dirs, 1)
            by_name = {item.name: item for item in flat.files}
            self.assertEqual(by_name["工作记录.csv"].source_type, TYPE_LEGACY_CSV)
            self.assertEqual(by_name["gbk记录.csv"].source_type, TYPE_LEGACY_CSV)
            self.assertEqual(by_name["gbk记录.csv"].operator, "李四")
            self.assertEqual(by_name["统计汇总.csv"].source_type, TYPE_OTHER_STATS)
            self.assertEqual(by_name["备份_记录.csv"].source_type, TYPE_BACKUP)
            self.assertEqual(by_name["随便.csv"].source_type, TYPE_UNCLASSIFIED)
            self.assertEqual(by_name["旧表.xls"].status, "unsupported")
            self.assertEqual(by_name["表格.xlsx"].source_type, TYPE_LEGACY_CSV)
            self.assertNotIn("导出明细.csv", by_name)

            deep = discover_files(str(root), recursive=True)
            names = {item.name for item in deep.files}
            self.assertIn("导出明细.csv", names)
            self.assertGreaterEqual(deep.scanned_dirs, 2)

    def test_xlsx_reader_and_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_xlsx(
                Path(tmp) / "导出.xlsx",
                [
                    ["姓名", "图片ID", "结果", "备注", "标签"],
                    ["周八", "55555555", "不合格", "xlsx备注", "标签X"],
                ],
            )
            info = inspect_table(str(path))
            self.assertEqual(info["rows_total"], 1)
            self.assertEqual(info["operator"], "周八")
            parsed, errors = parse_rows(str(path), info["headers"], info["rows"])
            self.assertEqual(errors, [])
            self.assertEqual(len(parsed), 1)
            self.assertEqual(parsed[0].task_id, "55555555")
            self.assertEqual(parsed[0].tags_raw, "标签X")
            self.assertIn("备注", parsed[0].note)


class TestImportService(unittest.TestCase):
    def _prepare(self, tmp: str) -> tuple[Path, Path, int]:
        base = Path(tmp)
        data = base / "records"
        data.mkdir()
        db_path = base / "management.db"
        seed_batch(db_path)
        return data, db_path, 0

    def test_import_association_preservation_and_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, db_path, _ = self._prepare(tmp)
            source = make_csv(
                data / "工作记录.csv",
                [
                    ["张三", "11111111", "A", "B", "不合格", "标签描述", "未知标签", "shot.png", "2026-11-01 10:00:00", "C:/x/1"],
                    ["李四", "22222222", "A", "B", "合格", "", "", "", "", ""],
                    ["", "99999999", "", "", "", "未匹配备注", "历史标签", "", "", ""],
                    ["王五", "", "", "", "", "纯备注无ID", "", "", "", ""],
                ],
            )
            service = RecordImportService(db_path)
            try:
                first = service.import_file(str(source), operator="tester", batch_id="batch_1")
                self.assertEqual(first.status, "completed")
                self.assertEqual((first.rows_total, first.rows_new, first.rows_skipped), (4, 4, 0))
                matched = next(row for row in first.rows if row.task_id == "11111111")
                self.assertEqual(matched.link_status, "matched")
                self.assertEqual(matched.data_group_id, "grp_1")
                self.assertEqual(matched.tags_raw, "未知标签")
                self.assertEqual(matched.evidence_raw, "shot.png")
                self.assertEqual(matched.record_time, "2026-11-01 10:00:00")
                unmatched = next(row for row in first.rows if row.task_id == "99999999")
                self.assertEqual(unmatched.link_status, "unmatched")
                self.assertEqual(unmatched.tags_raw, "历史标签")
                note_only = next(row for row in first.rows if not row.task_id)
                self.assertEqual(note_only.link_status, "note_only")
                self.assertEqual(note_only.note, "纯备注无ID")

                repeat = service.import_file(str(source), operator="tester")
                self.assertEqual(repeat.rows_new, 0)
                self.assertEqual(repeat.rows_skipped, 4)

                with source.open("a", encoding="utf-8") as handle:
                    handle.write("钱九,11111111,A,B,合格,追加,,,, \n")
                appended = service.import_file(str(source), operator="tester")
                self.assertEqual(appended.rows_new, 1)
                self.assertEqual(appended.rows_skipped, 4)
                self.assertEqual(appended.status, "partial")

                records = service.list_records()
                self.assertEqual(len(records), 5)
            finally:
                service.close()
            # 导入过程只读；测试自身追加过一行，文件仍存在且未被后台删除。
            self.assertTrue(source.exists())

    def test_import_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, db_path, _ = self._prepare(tmp)
            source = make_csv(
                data / "工作记录.csv",
                [["张三", "11111111", "A", "B", "合格", "", "", "", "", ""]],
            )
            before = snapshot(data)
            service = RecordImportService(db_path)
            try:
                service.import_file(str(source), operator="tester")
            finally:
                service.close()
            self.assertEqual(before, snapshot(data))

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, db_path, _ = self._prepare(tmp)
            source = make_csv(
                data / "工作记录.csv",
                [["张三", "11111111", "A", "B", "合格", "", "", "", "", ""]],
            )
            service = RecordImportService(db_path)
            try:
                result = service.import_file(str(source), dry_run=True)
                self.assertEqual(result.rows_new, 1)
                self.assertEqual(service.list_imports(), [])
                self.assertEqual(service.list_records(), [])
            finally:
                service.close()

    def test_invalid_batch_is_ignored_with_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, db_path, _ = self._prepare(tmp)
            source = make_csv(
                data / "工作记录.csv",
                [["张三", "11111111", "A", "B", "合格", "", "", "", "", ""]],
            )
            service = RecordImportService(db_path)
            try:
                result = service.import_file(str(source), batch_id="missing")
                self.assertTrue(any("批次不存在" in text for text in result.warnings))
                self.assertEqual(result.rows[0].data_group_id, "grp_1")
                self.assertEqual(result.batch_id, "batch_1")
            finally:
                service.close()

    def test_stats_and_unrecognized_are_honest(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, db_path, _ = self._prepare(tmp)
            stats = data / "统计汇总.csv"
            stats.write_text("日期,合格数,不合格数,通过率\n2026-11-01,10,2,83%\n", encoding="utf-8")
            bad = data / "随便.csv"
            bad.write_text("列A,列B\n1,2\n", encoding="utf-8")
            service = RecordImportService(db_path)
            try:
                stats_result = service.import_file(str(stats))
                self.assertEqual(stats_result.status, "completed")
                self.assertIn("RI-014", stats_result.note)
                bad_result = service.import_file(str(bad))
                self.assertEqual(bad_result.status, "failed")
                self.assertIn("RI-004", bad_result.note)
                self.assertEqual(service.list_records(), [])
                imports = service.list_imports()
                self.assertEqual(len(imports), 2)
                self.assertEqual(
                    {item["status"] for item in imports}, {"completed", "failed"}
                )
            finally:
                service.close()

    def test_xls_unsupported_is_logged_as_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, db_path, _ = self._prepare(tmp)
            legacy = data / "旧表.xls"
            legacy.write_bytes(b"\xd0\xcf\x11\xe0")
            service = RecordImportService(db_path)
            try:
                result = service.import_file(str(legacy))
                self.assertEqual(result.status, "failed")
                self.assertIn("RI-005", result.note)
                self.assertEqual(len(service.list_imports()), 1)
            finally:
                service.close()

    def test_import_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, db_path, _ = self._prepare(tmp)
            source = make_csv(
                data / "工作记录.csv",
                [["张三", "11111111", "A", "B", "合格", "", "", "", "", ""]],
            )
            service = RecordImportService(db_path)
            try:
                service.import_file(str(source), operator="tester")
                service.import_file(str(source), operator="tester")
                summary = service.summary()
                self.assertEqual(summary["imports"], 2)
                self.assertEqual(summary["rows_new"], 1)
                self.assertEqual(summary["rows_skipped"], 1)
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
