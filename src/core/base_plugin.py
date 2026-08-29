"""base_plugin.py —— 质检验收插件的抽象基类。

依据 OpenSpec 规范《plugin_base.spec.yaml》第 2 节「接口定义」生成：
BaseQualityPlugin 为所有质检插件的统一接口契约，任何插件必须实现
get_plugin_info / init / run 三个抽象方法，保证低耦合插拔。

本接口保证：
    - 插件元信息可被平台统一读取与展示
    - 插件初始化过程标准化，失败时抛出 PluginInitError
    - 插件执行过程标准化，失败时抛出 PluginRunError
"""

from abc import ABC, abstractmethod
from typing import Any

from .plugin_exceptions import PluginInitError, PluginRunError
from .plugin_types import PluginInfo, PluginTaskInput, PluginTaskOutput


class BaseQualityPlugin(ABC):
    """质检插件抽象基类，所有插件必须实现此接口。"""

    @abstractmethod
    def get_plugin_info(self) -> PluginInfo:
        """获取插件元信息。

        返回：
            当前插件的元信息（名称、版本、描述、作者等）。
        """

    @abstractmethod
    def init(self, config: dict[str, Any]) -> bool:
        """插件初始化：加载配置，初始化模型/资源。

        参数：
            config: 从配置文件读取的插件专属配置

        返回：
            初始化是否成功

        异常：
            PluginInitError: 初始化失败时抛出
        """

    @abstractmethod
    def run(self, input_data: PluginTaskInput) -> PluginTaskOutput:
        """同步执行插件核心逻辑。

        参数：
            input_data: 任务统一入参（含任务 ID、输入路径、自定义参数）

        返回：
            任务统一返回结构（含状态、结果数据、耗时等）

        异常：
            PluginRunError: 执行失败时抛出
        """