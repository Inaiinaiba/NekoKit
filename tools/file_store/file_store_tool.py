import base64
import os
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlparse

from astrbot.api import logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext

from ...core import BaseTool, NamespaceStrategy, ToolResult
from ..kv_store.context import get_ai_id, get_session_id
from ..kv_store.kv_store_tool import DefaultNamespaceStrategy
from .storage import FileStorageBackend


class FileStoreTool(BaseTool):
    """Persistent file storage for AI agents."""

    def __init__(self):
        self._storage: Optional[FileStorageBackend] = None
        self._namespace_strategy: NamespaceStrategy = DefaultNamespaceStrategy()
        self._context: Optional[ContextWrapper[AstrAgentContext]] = None
        self._config = {"ai_isolation": True, "session_scope": False}

    def initialize(self, data_dir: str, store_name: str = "file_store") -> None:
        self._storage = FileStorageBackend(data_dir, store_name=store_name)
        logger.info(f"[FileStoreTool] 已初始化，数据目录: {data_dir}")

    def set_config(self, config: Dict[str, Any]) -> None:
        self._config.update(config)
        logger.info(f"[FileStoreTool] 配置已更新: {self._config}")

    def set_context(self, context: ContextWrapper[AstrAgentContext]) -> None:
        self._context = context

    def get_name(self) -> str:
        return "file_store"

    def get_description(self) -> str:
        return "文件存储工具，用于持久化保存、读取、列出和删除文件。"

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["save", "get_path", "get_url", "list", "delete"],
                },
                "key": {"type": "string"},
                "source_path": {"type": "string"},
                "content": {"type": "string"},
                "content_base64": {"type": "string"},
                "retention_days": {"type": "integer"},
                "prefix": {"type": "string"},
            },
            "required": ["action"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        if not self._storage:
            return ToolResult(success=False, message="FileStore 未初始化")

        namespace, scope_desc = await self._build_namespace()
        action = kwargs.get("action", "")

        try:
            try:
                self._storage.cleanup_expired_once_per_day()
            except Exception as e:
                logger.warning(f"[FileStoreTool] 过期文件清理失败: {e}")
            if action == "save":
                return self._handle_save(kwargs, namespace, scope_desc)
            if action == "get_path":
                return self._handle_get_path(kwargs, namespace)
            if action == "get_url":
                return await self._handle_get_url(kwargs, namespace)
            if action == "list":
                return self._handle_list(kwargs, namespace, scope_desc)
            if action == "delete":
                return self._handle_delete(kwargs, namespace)
            return ToolResult(
                success=False,
                message="未知操作，支持: save、get_path、get_url、list、delete",
            )
        except Exception as e:
            logger.error(f"[FileStoreTool] 执行失败: {e}")
            return ToolResult(success=False, message=f"文件存储操作失败: {str(e)}")

    async def _build_namespace(self) -> tuple[Optional[str], str]:
        ai_id = "default_ai"
        session_id = "default_session"
        if self._context:
            try:
                ai_id = await get_ai_id(self._context)
            except Exception as e:
                logger.warning(f"[FileStoreTool] 获取 AI ID 失败: {e}")
            try:
                session_id = get_session_id(self._context)
            except Exception as e:
                logger.warning(f"[FileStoreTool] 获取会话 ID 失败: {e}")

        ai_isolation = self._config.get("ai_isolation", True)
        session_scope = self._config.get("session_scope", False)
        namespace = self._namespace_strategy.build(
            ai_id=ai_id if ai_isolation else None,
            session_id=session_id if session_scope else None,
        )
        scope_desc = self._namespace_strategy.describe(
            ai_isolation, session_scope, ai_id, session_id
        )
        return namespace, scope_desc

    def _handle_save(
        self, kwargs: Dict[str, Any], namespace: Optional[str], scope_desc: str
    ) -> ToolResult:
        key = self._require_key(kwargs)
        source_path = str(kwargs.get("source_path") or "").strip()
        content = kwargs.get("content")
        content_base64 = kwargs.get("content_base64")
        retention_days = self._parse_retention_days(kwargs.get("retention_days", 7))

        provided = sum(
            bool(value)
            for value in [
                source_path,
                content is not None,
                content_base64 is not None,
            ]
        )
        if provided != 1:
            return ToolResult(
                success=False,
                message="保存文件需要且只能提供 source_path、content、content_base64 之一",
            )

        if source_path:
            metadata = self._storage.put_file(
                key,
                self._normalize_source_path(source_path),
                namespace,
                retention_days=retention_days,
                source_filename=kwargs.get("_source_filename"),
            )
        elif content_base64 is not None:
            metadata = self._storage.put_bytes(
                key,
                base64.b64decode(str(content_base64)),
                namespace,
                retention_days=retention_days,
            )
        else:
            metadata = self._storage.put_bytes(
                key,
                str(content).encode("utf-8"),
                namespace,
                retention_days=retention_days,
                default_suffix=".txt",
            )

        metadata["scope"] = scope_desc
        return ToolResult(success=True, message="已保存文件", data=metadata)

    def _handle_get_path(
        self, kwargs: Dict[str, Any], namespace: Optional[str]
    ) -> ToolResult:
        key = self._require_key(kwargs)
        path = self._storage.get_path(key, namespace)
        if not path:
            return ToolResult(success=False, message=f"找不到文件 '{key}'")
        metadata = self._storage.get_metadata(key, namespace) or {"key": key}
        metadata["path"] = path
        return ToolResult(success=True, message="已获取文件路径", data=metadata)

    async def _handle_get_url(
        self, kwargs: Dict[str, Any], namespace: Optional[str]
    ) -> ToolResult:
        key = self._require_key(kwargs)
        path = self._storage.get_path(key, namespace)
        if not path:
            return ToolResult(success=False, message=f"找不到文件 '{key}'")

        from astrbot.core import astrbot_config, file_token_service

        callback_host = astrbot_config.get("callback_api_base")
        if not callback_host:
            return ToolResult(
                success=False,
                message="未配置 callback_api_base，文件 URL 服务不可用",
            )

        token = await file_token_service.register_file(path)
        url = f"{str(callback_host).removesuffix('/')}/api/file/{token}"
        metadata = self._storage.get_metadata(key, namespace) or {"key": key}
        metadata.update({"url": url, "token": token})
        return ToolResult(
            success=True,
            message="已生成临时文件 URL",
            data=metadata,
        )

    def _handle_list(
        self, kwargs: Dict[str, Any], namespace: Optional[str], scope_desc: str
    ) -> ToolResult:
        prefix = str(kwargs.get("prefix") or "")
        files = self._storage.list_files(namespace, prefix=prefix)
        return ToolResult(
            success=True,
            message=f"找到 {len(files)} 个文件",
            data={"files": files, "prefix": prefix, "scope": scope_desc},
        )

    def _handle_delete(
        self, kwargs: Dict[str, Any], namespace: Optional[str]
    ) -> ToolResult:
        key = self._require_key(kwargs)
        if self._storage.delete(key, namespace):
            return ToolResult(success=True, message="已删除文件", data={"key": key})
        return ToolResult(success=False, message=f"找不到文件 '{key}'")

    @staticmethod
    def _require_key(kwargs: Dict[str, Any]) -> str:
        key = str(kwargs.get("key") or "").strip()
        if not key:
            raise ValueError("必须提供 key")
        return key

    @staticmethod
    def _parse_retention_days(value: Any) -> int:
        try:
            retention_days = int(value)
        except Exception:
            raise ValueError("retention_days 必须是整数天数，或 -1 表示永久保留")
        if retention_days < -1:
            raise ValueError("retention_days 只能为 -1 或非负整数")
        return retention_days

    @staticmethod
    def _normalize_source_path(source_path: str) -> str:
        parsed = urlparse(source_path)
        if parsed.scheme == "file":
            path = unquote(parsed.path)
            if os.name == "nt" and len(path) >= 3 and path[0] == "/" and path[2] == ":":
                path = path[1:]
            return path
        return source_path
