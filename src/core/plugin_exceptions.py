"""plugin_exceptions.py —— 质检验收插件的异常定义。

依据 OpenSpec 规范《plugin_base.spec.yaml》第 3 节「异常定义」生成：
    1. PluginInitError —— 插件初始化失败，错误码 1001
    2. PluginRunError   —— 插件执行失败，错误码 1002

所有异常类均继承自 Exception，并通过 PluginException 基类统一携带
业务错误码 code，便于上层按码区分与处理。
"""


class PluginException(Exception):
    """插件相关异常的统一基类，携带业务错误码。"""

    #: 业务错误码，由具体异常子类覆盖
    code: int = 0

    def __init__(self, message: str = ""):
        """初始化插件异常。

        参数：
            message: 异常描述信息
        """
        super().__init__(message)
        self.message = message


class PluginInitError(PluginException):
    """插件初始化失败异常。

    抛出场景：插件加载配置、初始化模型或资源失败时抛出，
    对应规范错误码 1001。
    """

    code: int = 1001


class PluginRunError(PluginException):
    """插件执行失败异常。

    抛出场景：插件执行核心逻辑过程中发生错误时抛出，
    对应规范错误码 1002。
    """

    code: int = 1002