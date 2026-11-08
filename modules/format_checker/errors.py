"""格式检查错误码、级别与修复建议。"""
from __future__ import annotations

from .models import Issue

# code -> (默认级别, 通用说明, 建议)
ERROR_CATALOG: dict[str, tuple[str, str, str]] = {
    "FC-000": ("error", "根目录不存在或不是目录", "检查路径是否正确、是否有访问权限"),
    "FC-001": ("error", "任务目录名不符合 ID 规则", "按模板改名，或调整模板 ID 规则"),
    "FC-002": ("error", "任务目录下没有任何可识别图片", "确认文件是否放错目录或扩展名是否受支持"),
    "FC-003": ("error", "文件名中的任务 ID 与目录 ID 不一致", "统一目录与文件的任务 ID"),
    "FC-004": ("warn", "同一任务 ID 在本次范围内重复出现", "确认是否不同批次，必要时分批检查"),
    "FC-005": ("error", "目录层级不符合规则", "调整目录结构或模板规则"),
    "FC-006": ("error", "目录不可读或权限不足", "检查共享权限或文件占用"),
    "FC-007": ("warn", "跳过符号链接/junction", "确认是否应跟随该链接"),
    "FC-008": ("warn", "超过最大任务数，扫描提前结束", "提高上限或分批检查"),
    "FC-009": ("warn", "路径过长（超过 260 字符）", "缩短路径或启用 Windows 长路径"),
    "FC-101": ("error", "缺少主图 ID.jpg/jpeg/png", "补齐主图或调整模板"),
    "FC-102": ("error", "缺少结果图 ID_result.jpg/jpeg/png", "补齐结果图，或把该规则设为非必需"),
    "FC-103": ("error", "缺少 PSD 文件 ID.psd", "补齐 PSD，或把该规则设为非必需"),
    "FC-104": ("error", "缺少中文 TXT ID_CH.txt", "补齐中文 TXT，或把该规则设为非必需"),
    "FC-105": ("error", "缺少英文 TXT ID_EN.txt", "补齐英文 TXT，或把该规则设为非必需"),
    "FC-106": ("error", "主图扩展名不在允许范围", "转换为主图允许的扩展名"),
    "FC-107": ("error", "结果图扩展名不在允许范围", "转换为结果图允许的扩展名"),
    "FC-108": ("error", "同一规则匹配到多个扩展名", "只保留一个正确扩展名的文件"),
    "FC-201": ("error", "多图数量少于下限", "补齐多图编号文件"),
    "FC-202": ("error", "多图数量超过上限", "删除多余编号，或调整模板上限"),
    "FC-203": ("error", "多图编号不连续", "补齐缺失编号，或关闭连续性要求"),
    "FC-204": ("error", "多图编号重复或同号多扩展名", "每个编号只保留一个文件"),
    "FC-205": ("warn", "多图编号从非约定起点开始", "按模板起点重新编号"),
    "FC-206": ("error", "多图扩展名不在允许范围", "转换为多图允许的扩展名"),
    "FC-207": ("error", "多图文件名不符合 ID_n 格式", "按 ID_1、ID_2… 命名"),
    "FC-301": ("warn", "存在未预期的额外文件", "删除无关文件，或调整模板额外文件策略"),
    "FC-302": ("error", "文件大小为 0", "重新导出或替换空文件"),
    "FC-303": ("warn", "文件名大小写/空格/全角字符异常", "统一为模板要求的大小写和半角字符"),
    "FC-304": ("error", "文件被占用或无法读取", "关闭占用程序或检查权限"),
    "FC-305": ("warn", "TXT 为空或编码异常", "检查 TXT 内容并另存为 UTF-8"),
    "FC-306": ("info", "忽略的临时/系统文件", "无需处理"),
    "FC-307": ("warn", "同名文件大小写冲突", "只保留一个文件"),
    "FC-308": ("warn", "文件修改时间异常（未来时间）", "检查系统时间或文件来源"),
    "FC-309": ("info", "隐藏文件", "确认是否需要保留"),
    "FC-310": ("warn", "图片扩展名与实际格式不符", "按实际格式修正扩展名"),
    "FC-311": ("warn", "检查期间任务目录发生变化", "确认是否有其他程序正在写入，建议重跑"),
}

SEVERITY_ORDER = {"info": 0, "warn": 1, "error": 2}


def severity_of(code: str, override: str | None = None) -> str:
    if override:
        return override
    return ERROR_CATALOG.get(code, ("error", "", ""))[0]


def suggestion_of(code: str) -> str:
    return ERROR_CATALOG.get(code, ("", "", ""))[2]


def make_issue(
    code: str,
    *,
    task_id: str = "",
    task_path: str = "",
    file_path: str | None = None,
    expected: str | None = None,
    actual: str | None = None,
    message: str | None = None,
    suggestion: str | None = None,
    severity: str | None = None,
) -> Issue:
    default_severity, default_message, default_suggestion = ERROR_CATALOG.get(
        code, ("error", code, "")
    )
    return Issue(
        code=code,
        severity=severity or default_severity,
        task_id=task_id,
        task_path=task_path,
        file_path=file_path,
        expected=expected,
        actual=actual,
        message=message or default_message,
        suggestion=suggestion if suggestion is not None else default_suggestion,
    )


def code_title(code: str) -> str:
    return ERROR_CATALOG.get(code, ("", code, ""))[1]
