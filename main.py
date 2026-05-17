from astrbot.api import logger
from astrbot.api import star
from astrbot.api.star import StarTools
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext

from .tools import KVStoreTool
from .core import ToolResult


class KVStoreFunctionTool(FunctionTool[AstrAgentContext]):
    """AstrBot 函数工具包装器 - 适配 AstrBot 的 FunctionTool 接口"""
    name: str = "kv_store"
    description: str = (
        "键值存储工具，用于持久化保存和读取数据。默认开启 AI 隔离，"
        "每个 AI 只能访问自己存储的数据。支持会话隔离模式。"
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "操作类型：get(读取)、set(写入)、delete(删除)、list(列出键)、search(搜索)",
                "enum": ["get", "set", "delete", "list", "search"],
            },
            "key": {
                "type": "string",
                "description": "键名，用于唯一标识数据（get、set、delete、search 操作需要）",
            },
            "value": {
                "type": "string",
                "description": "值（set 操作需要），可以是任意 JSON 兼容的数据",
            },
            "session_scope": {
                "type": "boolean",
                "description": "是否为会话隔离模式：true=仅当前会话可见，false=当前 AI 所有会话可见（默认 false）",
                "default": False,
            },
            "ai_isolation": {
                "type": "boolean",
                "description": "是否启用 AI 隔离：true=仅当前 AI 可访问（默认 true），false=所有 AI 共享",
                "default": True,
            },
        },
        "required": ["action"],
    }

    _kv_tool: KVStoreTool = None

    @classmethod
    def create_with_tool(cls, kv_tool: KVStoreTool) -> "KVStoreFunctionTool":
        """工厂方法：创建绑定了 KVStoreTool 的实例"""
        wrapper = cls()
        wrapper._kv_tool = kv_tool
        return wrapper

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        if not self._kv_tool:
            return ToolExecResult(error="KVStoreTool 未初始化")

        # 设置上下文
        self._kv_tool.set_context(context)

        # 执行工具
        try:
            result: ToolResult = await self._kv_tool.execute(**kwargs)
            # 转换为 AstrBot 的 ToolExecResult
            if result.success:
                return ToolExecResult(result=result.to_dict())
            else:
                return ToolExecResult(error=result.message)
        except Exception as e:
            logger.error(f"[KVStoreFunctionTool] 执行失败: {e}")
            return ToolExecResult(error=f"执行失败: {str(e)}")


class Main(star.Star):
    """NekoKit 插件主类"""

    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self.context = context

        # 初始化数据目录
        self.data_dir = str(StarTools.get_data_dir("nekokit"))

        # 初始化 KV 存储工具
        self._kv_tool = KVStoreTool()
        self._kv_tool.initialize(self.data_dir, use_sqlite=False)

        # 创建 AstrBot 函数工具包装器并注册
        self._func_tool = KVStoreFunctionTool.create_with_tool(self._kv_tool)
        self.context.add_llm_tools(self._func_tool)

        logger.info("[NekoKit] 插件已加载，已注册 kv_store 工具")

    async def terminate(self):
        """插件卸载时调用，清理资源"""
        if hasattr(self, "_kv_tool") and self._kv_tool:
            logger.info("[NekoKit] 插件卸载，清理资源")

