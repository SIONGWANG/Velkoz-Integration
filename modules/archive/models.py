"""合格数据归档模型（v1.25.6）。

归档只做一件事：把用户确认过的数据组**复制**到输出目录。
不移动、不删除、不修改源图片/TXT/CSV，也不覆盖目标已有文件。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 归档任务状态
JOB_PREVIEW = "preview"
JOB_RUNNING = "running"
JOB_PAUSED = "paused"
JOB_COMPLETED = "completed"
JOB_FAILED = "failed"

JOB_STATUS_LABELS = {
    JOB_PREVIEW: "已预览",
    JOB_RUNNING: "复制中",
    JOB_PAUSED: "已暂停（可恢复）",
    JOB_COMPLETED: "已完成",
    JOB_FAILED: "有失败/冲突",
}

# 归档条目状态
ITEM_PENDING = "pending"
ITEM_COPIED = "copied"
ITEM_SKIPPED = "skipped"
ITEM_CONFLICT = "conflict"
ITEM_FAILED = "failed"
ITEM_VERIFIED = "verified"

ITEM_STATUS_LABELS = {
    ITEM_PENDING: "待复制",
    ITEM_COPIED: "已复制",
    ITEM_SKIPPED: "已存在，跳过",
    ITEM_CONFLICT: "目标冲突，未覆盖",
    ITEM_FAILED: "失败",
    ITEM_VERIFIED: "已复制并校验",
}

# 起始层级
START_LAYERS: tuple[tuple[str, str], ...] = (
    ("root", "根目录"),
    ("category_l1", "大类目"),
    ("category_l2", "小类目"),
    ("team", "团队"),
    ("date", "日期"),
    ("batch", "批次名（只保留模板目录）"),
    ("custom", "自定义前缀"),
)
START_LAYER_KEYS = {key for key, _ in START_LAYERS}
START_LAYER_LABELS = dict(START_LAYERS)

# 合格筛选
FILTER_QUALIFIED = "qualified"
FILTER_ALL = "all"
FILTER_STATUS = "status"
FILTER_IMPORTED = "imported"
FILTER_MANUAL = "manual"

FILTER_LABELS = {
    FILTER_QUALIFIED: "合格（数据组状态或导入记录）",
    FILTER_STATUS: "按数据组状态",
    FILTER_IMPORTED: "按导入记录结果（合格）",
    FILTER_MANUAL: "手动选择数据组",
    FILTER_ALL: "批次内全部数据组",
}

DEFAULT_QUALIFIED_STATUSES = (
    "qualified",
    "passed",
    "completed",
    "accepted",
    "合格",
    "修改后合格",
)
DEFAULT_QUALIFIED_RESULTS = (
    "合格",
    "修改后合格",
    "pass",
    "passed",
    "qualified",
)

# 模板变量与缺失回退
TEMPLATE_VARIABLES = {
    "批次名",
    "验收员",
    "合格数量",
    "日期",
    "大类目",
    "小类目",
    "团队",
    "自定义",
}

DEFAULT_NAME_TEMPLATE = "{批次名}_{验收员}合格{合格数量}条"


@dataclass
class ArchiveFile:
    """一个待复制的源文件。"""

    source_path: str
    relative_path: str
    size: int


@dataclass
class ArchiveGroupPlan:
    """一个数据组的归档计划。"""

    data_group_id: str
    task_id: str
    source_path: str
    dest_path: str
    files: list[ArchiveFile] = field(default_factory=list)
    conflict_paths: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def bytes_total(self) -> int:
        return sum(item.size for item in self.files)

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflict_paths)


@dataclass
class ArchivePreview:
    """复制前预览：目录树、文件数、空间、冲突与回退说明。"""

    batch_id: str
    batch_name: str
    source_root: str
    output_root: str
    start_layer: str
    name_template: str
    operator: str
    filter_mode: str
    filter_value: str
    custom_prefix: str = ""
    groups: list[ArchiveGroupPlan] = field(default_factory=list)
    missing_variables: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    tree: list[dict[str, Any]] = field(default_factory=list)
    rendered_folder: str = ""

    @property
    def total_items(self) -> int:
        return len(self.groups)

    @property
    def total_files(self) -> int:
        return sum(group.file_count for group in self.groups)

    @property
    def total_bytes(self) -> int:
        return sum(group.bytes_total for group in self.groups)

    @property
    def conflict_groups(self) -> int:
        return sum(1 for group in self.groups if group.has_conflict)

    @property
    def error_groups(self) -> int:
        return sum(1 for group in self.groups if group.error)

    @property
    def can_run(self) -> bool:
        return not self.errors and bool(self.groups)


@dataclass
class ArchiveRunResult:
    """一次复制（或恢复）的结果。"""

    job_id: str
    status: str
    total_items: int = 0
    copied_items: int = 0
    skipped_items: int = 0
    conflict_items: int = 0
    failed_items: int = 0
    verified_items: int = 0
    bytes_copied: int = 0
    cancelled: bool = False
    report_path: str = ""
    errors: list[str] = field(default_factory=list)
