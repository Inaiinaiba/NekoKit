import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from astrbot.api import logger

from ...core import BaseTool, ToolResult
from ...tools.kv_store.kv_store_tool import KVStoreTool
from ._internal import compute_image_hashes, download_image


class CacheTool(BaseTool):
    def __init__(self):
        self._data_dir: str = ""
        self._config: Dict[str, Any] = {}
        self._kv_tool: Optional[KVStoreTool] = None

    def initialize(
        self,
        data_dir: str,
        config: Dict[str, Any] = None,
        kv_tool: KVStoreTool = None,
        **kwargs,
    ) -> None:
        self._data_dir = data_dir
        if config:
            self._config = config
        if kv_tool:
            self._kv_tool = kv_tool

    def get_name(self) -> str:
        return "nkit_ce_cache"

    def get_description(self) -> str:
        return (
            "图片缓存管理工具。通过内部 KV 存储管理图片分析缓存，"
            "支持查询缓存、存储结果、更新条目。"
            "缓存键格式为 cat_eye:cache:{image_hash}_{tool_name}，有效期 48 小时。"
        )

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "image_url": {
                    "type": "string",
                    "description": "图片 URL 或本地文件路径",
                },
                "action": {
                    "type": "string",
                    "description": (
                        "操作类型："
                        "check（查询缓存，返回命中结果或空）、"
                        "store（存储新的缓存条目）、"
                        "update（更新已有缓存条目，合并结果并刷新过期时间）"
                    ),
                    "enum": ["check", "store", "update"],
                },
                "task_type": {
                    "type": "string",
                    "description": "任务类型：ocr、search 或 vision",
                    "enum": ["ocr", "search", "vision"],
                },
                "result": {
                    "type": "string",
                    "description": "结果数据 JSON 字符串（store/update 时必填）",
                },
                "evaluation": {
                    "type": "integer",
                    "description": "任务评价，范围 [-2, 2]（update 时可选）",
                },
            },
            "required": ["image_url", "action"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        if not self._kv_tool:
            return ToolResult(success=False, message="KVStoreTool 未初始化")

        image_url = kwargs.get("image_url", "")
        action = kwargs.get("action", "")
        task_type = kwargs.get("task_type", "vision")

        if not image_url:
            return ToolResult(success=False, message="必须提供 image_url")
        if not action:
            return ToolResult(success=False, message="必须提供 action")

        try:
            img_dir = os.path.join(self._data_dir, "cateye", "images")
            image_path = await download_image(image_url, img_dir)
            md5_val, dhash_val = compute_image_hashes(image_path)
            image_hash = f"{md5_val}_{dhash_val}" if dhash_val else md5_val
            cache_key = f"cat_eye:cache:{image_hash}_{task_type}"

            if action == "check":
                return await self._check_cache(cache_key)
            elif action == "store":
                return await self._store_cache(cache_key, kwargs)
            elif action == "update":
                return await self._update_cache(cache_key, kwargs)
            else:
                return ToolResult(success=False, message=f"未知操作: {action}")

        except Exception as e:
            logger.error(f"[nekokit.cateye] 缓存操作失败: {e}")
            return ToolResult(success=False, message=f"缓存操作失败: {str(e)}")

    async def _check_cache(self, cache_key: str) -> ToolResult:
        result = await self._kv_tool.execute(action="get", key=cache_key)
        if not result.success:
            return ToolResult(success=True, message="缓存未命中", data={"hit": False})

        try:
            raw_value = result.data.get("value", "{}")
            if isinstance(raw_value, dict):
                entry = raw_value
            else:
                entry = json.loads(raw_value)
        except (json.JSONDecodeError, AttributeError):
            return ToolResult(success=True, message="缓存未命中", data={"hit": False})

        expires_at = entry.get("expires_at", "")
        if expires_at:
            try:
                expire_dt = datetime.fromisoformat(expires_at)
                if datetime.now(timezone.utc) > expire_dt:
                    await self._kv_tool.execute(action="delete", key=cache_key)
                    return ToolResult(
                        success=True,
                        message="缓存已过期，已清除",
                        data={"hit": False, "expired": True},
                    )
            except ValueError:
                pass

        return ToolResult(
            success=True,
            message="缓存命中",
            data={"hit": True, "cache_key": cache_key, "entry": entry},
        )

    async def _store_cache(self, cache_key: str, kwargs: Dict[str, Any]) -> ToolResult:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=48)

        result_str = kwargs.get("result", "{}")

        try:
            result_data = json.loads(result_str)
        except json.JSONDecodeError:
            result_data = {}

        entry = {
            "result": result_data,
            "evaluation": 0,
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
        }

        value_json = json.dumps(entry, ensure_ascii=False)
        store_result = await self._kv_tool.execute(
            action="set", key=cache_key, value=value_json
        )

        if store_result.success:
            logger.info(f"[nekokit.cateye] 缓存已存储: {cache_key[:32]}...")
            return ToolResult(
                success=True,
                message="缓存已存储",
                data={"cache_key": cache_key, "expires_at": expires_at.isoformat()},
            )
        return ToolResult(success=False, message="缓存存储失败")

    async def _update_cache(self, cache_key: str, kwargs: Dict[str, Any]) -> ToolResult:
        check_result = await self._check_cache(cache_key)
        if not check_result.data.get("hit"):
            return await self._store_cache(cache_key, kwargs)

        entry = check_result.data.get("entry", {})

        result_str = kwargs.get("result", "")
        if result_str:
            try:
                new_result = json.loads(result_str)
                entry["result"].update(new_result)
            except json.JSONDecodeError:
                pass

        if "evaluation" in kwargs:
            evaluation = kwargs.get("evaluation", 0)
            evaluation = max(-2, min(2, int(evaluation)))
            entry["evaluation"] = evaluation

        now = datetime.now(timezone.utc)
        entry["expires_at"] = (now + timedelta(hours=48)).isoformat()

        value_json = json.dumps(entry, ensure_ascii=False)
        store_result = await self._kv_tool.execute(
            action="set", key=cache_key, value=value_json
        )

        if store_result.success:
            logger.info(f"[nekokit.cateye] 缓存已更新: {cache_key[:32]}...")
            return ToolResult(
                success=True,
                message="缓存已更新",
                data={
                    "cache_key": cache_key,
                    "expires_at": entry["expires_at"],
                },
            )
        return ToolResult(success=False, message="缓存更新失败")
