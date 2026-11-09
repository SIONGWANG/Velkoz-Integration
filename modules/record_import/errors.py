"""记录导入错误码（RI-xxx）。"""
from __future__ import annotations

ERROR_CATALOG: dict[str, tuple[str, str, str]] = {
    "RI-000": ("error", "根目录无效或不存在", "重新选择存在且有权限的目录"),
    "RI-001": ("info", "未发现 CSV/Excel 文件", "确认目录、扩展名与递归选项"),
    "RI-002": ("error", "文件无法读取", "检查文件占用、权限与路径"),
    "RI-003": ("error", "文本编码无法识别", "另存为 UTF-8 或 GBK 后重试"),
    "RI-004": ("warn", "表结构无法识别", "确认是否包含图片ID/任务ID或备注/标签列"),
    "RI-005": ("warn", "旧版 .xls 暂不支持", "用 Excel 另存为 .xlsx 后重试"),
    "RI-006": ("error", "Excel 读取失败", "确认文件未损坏且不是加密文档"),
    "RI-007": ("warn", "缺少图片ID列", "纯备注记录仍会保留；任务记录需补齐图片ID"),
    "RI-008": ("warn", "行解析失败", "该行已记录错误并跳过"),
    "RI-009": ("info", "重复行已跳过", "同一文件重复导入不会重复生成记录"),
    "RI-010": ("error", "导入失败", "查看导入日志与错误信息"),
    "RI-011": ("error", "文件超过大小上限", "拆分文件或提高上限后重试"),
    "RI-012": ("info", "未匹配到数据组", "记录已保留为未关联；确认批次是否已确认"),
    "RI-013": ("info", "标签原样保留", "不要求本机存在同名标签；不做自动映射"),
    "RI-014": ("info", "统计类文件仅识别不导入", "统计表没有逐条验收记录，未写入记录明细"),
}


class RecordImportError(Exception):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        self.message = message or code_title(code)
        super().__init__(f"{code} {self.message}")


def code_title(code: str) -> str:
    entry = ERROR_CATALOG.get(code)
    return entry[1] if entry else code


def code_suggestion(code: str) -> str:
    entry = ERROR_CATALOG.get(code)
    return entry[2] if entry else ""


def code_severity(code: str) -> str:
    entry = ERROR_CATALOG.get(code)
    return entry[0] if entry else "error"


def catalog_rows() -> list[list[str]]:
    return [
        [code, severity, title, suggestion]
        for code, (severity, title, suggestion) in sorted(ERROR_CATALOG.items())
    ]
