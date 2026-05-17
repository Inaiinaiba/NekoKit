from astrbot.api import logger
from astrbot.api import star
from astrbot.api.star import StarTools
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext
from dataclasses import dataclass, field

from .tools import KVStoreTool
from .core import ToolResult


@dataclass
class KVGetTool(FunctionTool[AstrAgentContext]):
    """获取键值对"""
    name: str = "get_kv"
    description: str = "根据键名获取存储的值"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "要获取的键名",
            },
        },
        "required": ["key"],
    })

    _kv_tool: KVStoreTool = None

    @classmethod
    def create_with_tool(cls, kv_tool: KVStoreTool) -> "KVGetTool":
        tool = cls()
        tool._kv_tool = kv_tool
        return tool

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        if not self._kv_tool:
            return ToolExecResult(error="KVStoreTool 未初始化")
        
        self._kv_tool.set_context(context)
        try:
            result: ToolResult = await self._kv_tool.execute(action="get", **kwargs)
            if result.success:
                return ToolExecResult(result=result.to_dict())
            else:
                return ToolExecResult(error=result.message)
        except Exception as e:
            logger.error(f"[KVGetTool] 执行失败: {e}")
            return ToolExecResult(error=f"执行失败: {str(e)}")


@dataclass
class KVSetTool(FunctionTool[AstrAgentContext]):
    """设置键值对"""
    name: str = "set_kv"
    description: str = "设置或更新键值对"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "键名",
            },
            "value": {
                "type": "string",
                "description": "值，可以是任意 JSON 兼容的数据",
            },
        },
        "required": ["key", "value"],
    })

    _kv_tool: KVStoreTool = None

    @classmethod
    def create_with_tool(cls, kv_tool: KVStoreTool) -> "KVSetTool":
        tool = cls()
        tool._kv_tool = kv_tool
        return tool

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        if not self._kv_tool:
            return ToolExecResult(error="KVStoreTool 未初始化")
        
        self._kv_tool.set_context(context)
        try:
            result: ToolResult = await self._kv_tool.execute(action="set", **kwargs)
            if result.success:
                return ToolExecResult(result=result.to_dict())
            else:
                return ToolExecResult(error=result.message)
        except Exception as e:
            logger.error(f"[KVSetTool] 执行失败: {e}")
            return ToolExecResult(error=f"执行失败: {str(e)}")


@dataclass
class KVDeleteTool(FunctionTool[AstrAgentContext]):
    """删除键值对"""
    name: str = "delete_kv"
    description: str = "根据键名删除存储的值"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "要删除的键名",
            },
        },
        "required": ["key"],
    })

    _kv_tool: KVStoreTool = None

    @classmethod
    def create_with_tool(cls, kv_tool: KVStoreTool) -> "KVDeleteTool":
        tool = cls()
        tool._kv_tool = kv_tool
        return tool

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        if not self._kv_tool:
            return ToolExecResult(error="KVStoreTool 未初始化")
        
        self._kv_tool.set_context(context)
        try:
            result: ToolResult = await self._kv_tool.execute(action="delete", **kwargs)
            if result.success:
                return ToolExecResult(result=result.to_dict())
            else:
                return ToolExecResult(error=result.message)
        except Exception as e:
            logger.error(f"[KVDeleteTool] 执行失败: {e}")
            return ToolExecResult(error=f"执行失败: {str(e)}")


@dataclass
class KVListTool(FunctionTool[AstrAgentContext]):
    """列出所有键"""
    name: str = "list_kv"
    description: str = "列出当前作用域下的所有键"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {},
    })

    _kv_tool: KVStoreTool = None

    @classmethod
    def create_with_tool(cls, kv_tool: KVStoreTool) -> "KVListTool":
        tool = cls()
        tool._kv_tool = kv_tool
        return tool

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        if not self._kv_tool:
            return ToolExecResult(error="KVStoreTool 未初始化")
        
        self._kv_tool.set_context(context)
        try:
            result: ToolResult = await self._kv_tool.execute(action="list", **kwargs)
            if result.success:
                return ToolExecResult(result=result.to_dict())
            else:
                return ToolExecResult(error=result.message)
        except Exception as e:
            logger.error(f"[KVListTool] 执行失败: {e}")
            return ToolExecResult(error=f"执行失败: {str(e)}")


class Main(star.Star):
    """NekoKit 插件主类"""

    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self.context = context

        self.data_dir = str(StarTools.get_data_dir("nekokit"))

        self._kv_tool = KVStoreTool()
        self._kv_tool.initialize(self.data_dir)

        self._load_config()

        self._register_tools()

        logger.info("[NekoKit] 插件已加载，已注册 KV 存储工具")

    def _load_config(self):
        """加载配置"""
        try:
            config = self.context.get_config()
            kvstore_config = config.get("kvstore", {})
            self._kv_tool.set_config(kvstore_config)
        except Exception as e:
            logger.warning(f"[NekoKit] 加载配置失败，使用默认配置: {e}")

    def _register_tools(self):
        """注册工具"""
        tools = [
            KVGetTool.create_with_tool(self._kv_tool),
            KVSetTool.create_with_tool(self._kv_tool),
            KVDeleteTool.create_with_tool(self._kv_tool),
            KVListTool.create_with_tool(self._kv_tool),
        ]
        self.context.add_llm_tools(*tools)

    async def terminate(self):
        """插件卸载时调用，清理资源"""
        if hasattr(self, "_kv_tool") and self._kv_tool:
            logger.info("[NekoKit] 插件卸载，清理资源")