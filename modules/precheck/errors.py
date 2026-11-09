"""翻译与预审错误码（PC-xxx）。"""
from __future__ import annotations

# code -> (severity, title, suggestion)
ERROR_CATALOG: dict[str, tuple[str, str, str]] = {
    "PC-000": ("error", "根目录无效或不存在", "重新选择存在且有权限的根目录"),
    "PC-001": ("warn", "未发现可处理任务", "确认目录结构、ID 规则与中文 TXT 后缀"),
    "PC-002": ("error", "缺少中文 TXT", "补齐中文 TXT 后再翻译"),
    "PC-003": ("warn", "缺少英文 TXT", "翻译成功后会自动创建英文 TXT"),
    "PC-004": ("warn", "英文 TXT 为空", "可重新翻译；写入前会自动备份"),
    "PC-005": ("error", "中文 TXT 为空", "补齐中文指令后再翻译"),
    "PC-006": ("error", "TXT 读取失败或编码不支持", "另存为 UTF-8 / GBK 后重试"),
    "PC-007": ("error", "TXT 超过大小限制", "确认文件是否正常，或在设置中提高上限"),
    "PC-101": ("error", "未配置 API Key", "在“模型与提示词”中填写并保存 API Key"),
    "PC-102": ("error", "模型配置无效", "检查 Base URL、模型名与请求参数"),
    "PC-103": ("error", "认证失败", "检查 API Key 是否正确、是否过期"),
    "PC-104": ("warn", "触发限流", "降低并发或稍后重试"),
    "PC-105": ("warn", "请求超时", "提高超时时间或降低并发"),
    "PC-106": ("warn", "网络连接失败", "检查网络、代理与 Base URL"),
    "PC-107": ("warn", "服务端错误", "稍后重试；持续出现时检查模型服务状态"),
    "PC-108": ("error", "响应格式无法解析", "更换模型或调整提示词后重试"),
    "PC-109": ("error", "返回内容为空或被过滤", "检查提示词与内容合规性"),
    "PC-110": ("error", "重试次数用尽", "查看最后一次错误并手工重试"),
    "PC-118": ("error", "请求被拒绝", "检查模型名、参数与账号权限"),
    "PC-201": ("warn", "目标英文 TXT 已存在且非空", "确认需要覆盖时显式勾选“覆盖非空英文”"),
    "PC-202": ("error", "写入路径越界", "检查任务目录与文件路径是否在所选根目录内"),
    "PC-203": ("error", "备份失败", "检查管理数据目录空间与权限"),
    "PC-204": ("error", "原子写入失败", "检查文件占用、只读属性与权限"),
    "PC-301": ("warn", "英文语法检查未通过", "查看建议后人工确认"),
    "PC-302": ("warn", "中英文一致性检查未通过", "查看差异后人工确认"),
    "PC-303": ("warn", "检查结果解析失败", "查看原始返回并手工判断"),
    "PC-401": ("error", "导入报告缺少必要列", "确认表头与外部工具导出一致"),
    "PC-402": ("warn", "导入行解析失败", "该行已跳过，可在报告中查看原因"),
    "PC-403": ("info", "重复导入已跳过", "同一行只统计一次，无需处理"),
}


class PrecheckError(Exception):
    """带错误码的业务异常；message 中不得包含 API Key。"""

    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        retryable: bool = False,
        status_code: int | None = None,
        retry_after: float | None = None,
    ):
        self.code = code
        self.message = message or code_title(code)
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after = retry_after
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


def make_error(code: str, message: str = "", **kwargs) -> PrecheckError:
    return PrecheckError(code, message, **kwargs)


def catalog_rows() -> list[list[str]]:
    return [
        [code, severity, title, suggestion]
        for code, (severity, title, suggestion) in sorted(ERROR_CATALOG.items())
    ]
