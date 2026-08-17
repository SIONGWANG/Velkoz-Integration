"""src.core —— 质检验收插件基础规范（plugin_base.spec.yaml）的 Python 实现。

对外暴露统一接口，便于各插件以 ``from src.core import ...`` 方式导入：
    - BaseQualityPlugin : 插件抽象基类
    - PluginTaskInput   : 任务统一入参
    - PluginTaskOutput  : 任务统一返回
    - PluginInfo        : 插件元信息
    - PluginInitError   : 初始化失败异常
    - PluginRunError    : 执行失败异常
"""

from .base_plugin import BaseQualityPlugin
from .plugin_exceptions import PluginInitError, PluginRunError
from .plugin_types import PluginInfo, PluginTaskInput, PluginTaskOutput

__all__ = [
    "BaseQualityPlugin",
    "PluginInfo",
    "PluginTaskInput",
    "PluginTaskOutput",
    "PluginInitError",
    "PluginRunError",
]