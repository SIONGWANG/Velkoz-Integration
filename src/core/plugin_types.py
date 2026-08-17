"""plugin_types.py —— 质检验收插件数据结构的统一契约。

依据 OpenSpec 规范《plugin_base.spec.yaml》第 1 节「数据结构定义」生成：
    1. PluginTaskInput   —— 插件任务统一入参
    2. PluginTaskOutput  —— 插件任务统一返回
    3. PluginInfo        —— 插件基础元信息
"""

from dataclasses import dataclass, field
from typing import Any, Literal


class TaskStatus:
    """任务执行状态的候选常量值，供运行时取值使用。

    与 PluginTaskOutput.status 的类型注解保持一致：
    success(成功) / failed(失败) / running(执行中) / skipped(已跳过)
    """

    SUCCESS = "success"
    FAILED = "failed"
    RUNNING = "running"
    SKIPPED = "skipped"


# 任务状态合法取值，用于 PluginTaskOutput.status 的类型约束
TaskStatusLiteral = Literal["success", "failed", "running", "skipped"]


@dataclass
class PluginTaskInput:
    """所有插件执行任务的统一入参结构。

    属性：
        task_id:        全局唯一任务 ID，用于全链路追踪
        input_path:     待处理文件夹路径，支持局域网 UNC 路径
        custom_params:  插件自定义参数，不同插件自行扩展
    """

    task_id: str
    input_path: str
    custom_params: dict[str, Any] = field(default_factory=dict)


@dataclass
class PluginTaskOutput:
    """所有插件执行完成后的统一返回结构。

    属性：
        task_id:      对应输入的任务 ID
        status:       任务执行状态（success/failed/running/skipped）
        result_data:  结构化执行结果，不同插件自行定义字段
        output_path:  插件产出文件的存放目录
        error_msg:    失败时的错误信息
        cost_time:    执行耗时，单位秒
    """

    task_id: str
    status: TaskStatusLiteral
    result_data: dict[str, Any] = field(default_factory=dict)
    output_path: str = ""
    error_msg: str = ""
    cost_time: float = 0.0


@dataclass
class PluginInfo:
    """插件基础元信息，供质检平台识别与展示。

    属性：
        name:        插件唯一标识名称
        version:     语义化版本号，如 1.2.0
        description: 插件功能描述
        author:      插件作者
    """

    name: str
    version: str
    description: str
    author: str = ""