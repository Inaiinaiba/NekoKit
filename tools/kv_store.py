import json
from typing import Any, Dict, Optional

from astrbot.api import logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext

from ..core import BaseTool, ToolResult, StorageBackend, NamespaceStrategy
from .storage import create_storage_backend
from .context import get_ai_id, get_session_id


class DefaultNamespaceStrategy(NamespaceStrategy):
    """默认命名空间策略：支持 AI 隔离和会话隔离"""
    
    def build(self, ai_id: Optional[str], session_id: Optional[str]) -> Optional[str]:
        parts = []
        if ai_id:
            parts.append(f"ai:{ai_id}")
        if session_id:
            parts.append(f"session:{session_id}")
        if not parts:
            return None
        return "|".join(parts)
    
    def describe(self, ai_isolation: bool, session_scope: bool, 
                 ai_id: str, session_id: str) -> str:
        if ai_isolation and session_scope:
            return f"AI '{ai_id}' 的会话 '{session_id}' 内"
        if ai_isolation:
            return f"AI '{ai_id}' 专属"
        if session_scope:
            return f"会话 '{session_id}' 内（所有AI共享）"
        return "全局（所有AI共享）"


class KVStoreTool(BaseTool):
    """🐱 KV 存储工具 - 轻量级键值存储，支持 AI 隔离与会话隔离"""
    
    def __init__(self):
        self._storage: Optional[StorageBackend] = None
        self._namespace_strategy: NamespaceStrategy = DefaultNamespaceStrategy()
        self._context: Optional[ContextWrapper[AstrAgentContext]] = None
        self._config = {
            "ai_isolation": True,
            "session_scope": False
        }
    
    def initialize(self, data_dir: str) -> None:
        """初始化工具，统一使用 SQLite"""
        self._storage = create_storage_backend(data_dir)
        logger.info(f"[KVStoreTool] 已初始化，数据目录: {data_dir}")
    
    def set_config(self, config: Dict[str, Any]) -> None:
        """设置配置"""
        self._config.update(config)
        logger.info(f"[KVStoreTool] 配置已更新: {self._config}")
    
    def get_name(self) -> str:
        return "kv_store"
    
    def get_description(self) -> str:
        return (
            "键值存储工具，用于持久化保存和读取数据。默认开启 AI 隔离，"
            "每个 AI 只能访问自己存储的数据。支持会话隔离模式。"
        )
    
    def get_parameters(self) -> Dict[str, Any]:
        return {
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
            },
            "required": ["action"],
        }
    
    def set_context(self, context: ContextWrapper[AstrAgentContext]) -> None:
        """设置当前执行上下文"""
        self._context = context
    
    async def execute(self, **kwargs) -> ToolResult:
        """执行工具逻辑"""
        if not self._storage:
            return ToolResult(
                success=False,
                message="KVStore 未初始化"
            )
        
        action = kwargs.get("action", "")
        if not action:
            return ToolResult(
                success=False,
                message="必须指定操作类型"
            )
        
        ai_id = "default_ai"
        session_id = "default_session"
        if self._context:
            try:
                ai_id = await get_ai_id(self._context)
            except Exception as e:
                logger.warning(f"[KVStoreTool] 获取 AI ID 失败: {e}")
            try:
                session_id = get_session_id(self._context)
            except Exception as e:
                logger.warning(f"[KVStoreTool] 获取会话 ID 失败: {e}")
        
        ai_isolation = self._config.get("ai_isolation", True)
        session_scope = self._config.get("session_scope", False)
        
        namespace = self._namespace_strategy.build(
            ai_id=ai_id if ai_isolation else None,
            session_id=session_id if session_scope else None,
        )
        
        return await self._handle_action(action, kwargs, namespace, 
                                         ai_isolation, session_scope, ai_id, session_id)
    
    async def _handle_action(
        self, action: str, kwargs: Dict, namespace: Optional[str],
        ai_isolation: bool, session_scope: bool, ai_id: str, session_id: str
    ) -> ToolResult:
        if action == "get":
            return self._handle_get(kwargs, namespace)
        elif action == "set":
            return self._handle_set(kwargs, namespace, ai_isolation, session_scope, ai_id, session_id)
        elif action == "delete":
            return self._handle_delete(kwargs, namespace)
        elif action == "list":
            return self._handle_list(namespace, ai_isolation, session_scope, ai_id, session_id)
        elif action == "search":
            return self._handle_search(kwargs, namespace)
        else:
            return ToolResult(
                success=False,
                message=f"未知操作: {action}，支持的操作: get、set、delete、list、search"
            )
    
    def _handle_get(self, kwargs: Dict, namespace: Optional[str]) -> ToolResult:
        key = kwargs.get("key", "")
        if not key:
            return ToolResult(
                success=False,
                message="获取数据需要提供键名"
            )
        
        value = self._storage.get(key, namespace)
        if value is None:
            return ToolResult(
                success=False,
                message=f"喵~ 找不到键 '{key}'"
            )
        
        return ToolResult(
            success=True,
            message="找到了哦 😸",
            data={"key": key, "value": value}
        )
    
    def _handle_set(
        self, kwargs: Dict, namespace: Optional[str],
        ai_isolation: bool, session_scope: bool, ai_id: str, session_id: str
    ) -> ToolResult:
        key = kwargs.get("key", "")
        if not key:
            return ToolResult(
                success=False,
                message="设置数据需要提供键名"
            )
        
        value = kwargs.get("value")
        if value is None:
            return ToolResult(
                success=False,
                message="设置数据需要提供值"
            )
        
        try:
            if isinstance(value, str):
                parsed_value = json.loads(value)
            else:
                parsed_value = value
        except json.JSONDecodeError:
            parsed_value = value
        
        self._storage.set(key, parsed_value, namespace)
        
        scope_desc = self._namespace_strategy.describe(
            ai_isolation, session_scope, ai_id, session_id
        )
        
        return ToolResult(
            success=True,
            message=f"✅ 已保存到 {scope_desc} 喵~ 😺",
            data={"key": key, "scope": scope_desc}
        )
    
    def _handle_delete(self, kwargs: Dict, namespace: Optional[str]) -> ToolResult:
        key = kwargs.get("key", "")
        if not key:
            return ToolResult(
                success=False,
                message="删除数据需要提供键名"
            )
        
        success = self._storage.delete(key, namespace)
        if success:
            return ToolResult(
                success=True,
                message="已删除喵~ 🗑️",
                data={"key": key}
            )
        
        return ToolResult(
            success=False,
            message=f"找不到键 '{key}' 喵~ 😿"
        )
    
    def _handle_list(
        self, namespace: Optional[str],
        ai_isolation: bool, session_scope: bool, ai_id: str, session_id: str
    ) -> ToolResult:
        keys = self._storage.list_keys(namespace)
        scope_desc = self._namespace_strategy.describe(
            ai_isolation, session_scope, ai_id, session_id
        )
        
        if not keys:
            return ToolResult(
                success=True,
                message=f"{scope_desc} 还没有存储任何数据喵~ 📦",
                data={"keys": [], "scope": scope_desc}
            )
        
        return ToolResult(
            success=True,
            message=f"找到 {len(keys)} 个键喵~ 📋",
            data={"keys": keys, "scope": scope_desc}
        )
    
    def _handle_search(self, kwargs: Dict, namespace: Optional[str]) -> ToolResult:
        key = kwargs.get("key", "")
        if not key:
            return ToolResult(
                success=False,
                message="搜索需要提供关键词"
            )
        
        results = self._storage.search(key, namespace)
        if not results:
            return ToolResult(
                success=True,
                message=f"没有找到包含 '{key}' 的数据喵~ 🔍",
                data={"keyword": key, "results": []}
            )
        
        return ToolResult(
            success=True,
            message=f"找到 {len(results)} 条相关记录喵~ ✨",
            data={"keyword": key, "results": results}
        )