"""统计与报表模型（v1.25.7）。

统计只读管理数据库；所有口径都能回到 `data_groups`、`imported_records`、
`format_check_issues`、`translation_items`、`archive_items` 等源表。
未知或未同步数据不臆造，按 0 / “未同步”显示。
"""
from __future__ import annotations

from dataclasses import dataclass

RESULT_QUALIFIED = ("合格", "修改后合格")
RESULT_REJECTED = ("不合格", "作废")

RESULT_QUALIFIED_LABEL = "合格"
RESULT_REJECTED_LABEL = "不合格"

WORKLOAD_RECORDS = "records"
WORKLOAD_IMAGES = "images"
WORKLOAD_MIXED = "mixed"

WORKLOAD_LABELS = {
    WORKLOAD_RECORDS: "按验收记录数",
    WORKLOAD_IMAGES: "按图片数",
    WORKLOAD_MIXED: "记录数 + 图片数×0.05",
}

SEVERITY_LABELS = {
    "error": "错误",
    "warn": "警告",
    "info": "提示",
}

BATCH_STATS_COLUMNS = (
    "批次号",
    "批次名",
    "团队",
    "供应商",
    "日期",
    "一级",
    "二级",
    "状态",
    "任务数",
    "图片数",
    "格式已检查",
    "格式失败",
    "翻译已翻译",
    "翻译失败",
    "批次合格",
    "批次不合格",
    "批次剩余",
    "导入记录",
    "导入合格",
    "导入不合格",
    "归档已校验",
    "最近活动",
)

OPERATOR_STATS_COLUMNS = (
    "人员",
    "日期",
    "批次",
    "验收记录",
    "合格",
    "不合格",
    "其他",
    "合格率",
    "涉及任务",
    "图片数",
    "工作量",
)

ERROR_STATS_COLUMNS = (
    "错误码",
    "说明",
    "级别",
    "次数",
    "涉及任务",
    "涉及批次",
    "首次时间",
    "最近时间",
)

TAG_STATS_COLUMNS = (
    "标签（原文）",
    "次数",
    "涉及批次",
    "涉及人员",
    "合格",
    "不合格",
)

DAILY_STATS_COLUMNS = (
    "日期",
    "新增批次",
    "新增数据组",
    "格式检查",
    "格式失败",
    "格式警告",
    "翻译完成",
    "翻译失败",
    "验收记录",
    "合格",
    "不合格",
    "归档数据组",
    "剩余",
)

TRACE_RECORD_COLUMNS = (
    "记录ID",
    "日期",
    "人员",
    "结果",
    "任务ID",
    "批次",
    "数据组",
    "来源文件",
    "来源行",
    "标签（原文）",
    "备注",
)

TRACE_ISSUE_COLUMNS = (
    "错误码",
    "级别",
    "批次",
    "任务ID",
    "任务路径",
    "文件",
    "期望",
    "实际",
    "说明",
    "检查时间",
)

# 统计口径说明：指标 -> (源表, 说明)
METRIC_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("批次/任务数", "batches / data_groups", "批次与数据组索引"),
    ("图片数", "data_groups.image_count", "扫描时记录；未扫描时可能为 0"),
    ("格式检查", "format_check_runs / format_check_task_results", "按最近一次检查或指定日期范围汇总"),
    ("翻译", "translation_jobs / translation_items", "按最近一次翻译任务或指定日期范围汇总"),
    ("验收/合格/不合格", "imported_records.result", "只统计已导入管理数据库的记录；未导入显示 0"),
    ("批次合格/不合格/剩余", "batches.qualified_count/rejected_count/remaining_count", "批次验收统计快照；未同步显示 0"),
    ("错误类型", "format_check_issues.code", "保留原始错误码与级别"),
    ("标签", "imported_records.tags_raw", "保留历史标签原文，未定义标签也统计"),
    ("归档", "archive_jobs / archive_items", "只统计已校验或已存在跳过的数据组"),
    ("日报", "上述源表按日期归集", "缓存 stats_daily 可随时重建"),
)


@dataclass
class StatsFilters:
    date_from: str = ""
    date_to: str = ""
    batch_id: str = ""
    team: str = ""
    operator: str = ""

    def normalized(self) -> "StatsFilters":
        return StatsFilters(
            date_from=(self.date_from or "").strip(),
            date_to=(self.date_to or "").strip(),
            batch_id=(self.batch_id or "").strip(),
            team=(self.team or "").strip(),
            operator=(self.operator or "").strip(),
        )
