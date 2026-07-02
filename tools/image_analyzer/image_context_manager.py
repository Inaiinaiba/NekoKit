import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from astrbot.api import logger

from ...tools.kv_store.kv_store_tool import KVStoreTool
from ._internal import compute_image_hashes, download_image


CONTEXT_TTL_DAYS = 7
CONTEXT_KEY_PREFIX = "cat_eye:ctx:"
CONTEXT_CLEANUP_KEY = "cat_eye:maintenance:ctx_cleanup_last_run"


class KnowledgeEntry:
    def __init__(self, source: str, content: str, **meta):
        self.source = source
        self.content = content
        self.meta = meta
        self.timestamp = meta.get("timestamp", datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        d = {
            "source": self.source,
            "content": self.content,
            "timestamp": self.timestamp,
        }
        d.update({k: v for k, v in self.meta.items() if k not in ("timestamp",)})
        return d


class ImageContext:
    def __init__(self, image_hash: str, image_url: str = ""):
        self.image_hash = image_hash
        self.image_url = image_url
        self.knowledge: List[KnowledgeEntry] = []
        self.scene: Dict[str, str] = {}
        self.intent: Dict[str, Any] = {}
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.updated_at = self.created_at

    def to_dict(self) -> dict:
        return {
            "image_hash": self.image_hash,
            "image_url": self.image_url,
            "knowledge": [k.to_dict() for k in self.knowledge],
            "scene": self.scene,
            "intent": self.intent,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ImageContext":
        ctx = cls(data.get("image_hash", ""), data.get("image_url", ""))
        ctx.knowledge = [
            KnowledgeEntry(
                **{k: v for k, v in k_data.items() if k != "timestamp"},
                timestamp=k_data.get("timestamp", ""),
            )
            for k_data in data.get("knowledge", [])
        ]
        ctx.scene = data.get("scene", {})
        ctx.intent = data.get("intent", {})
        ctx.created_at = data.get("created_at", ctx.created_at)
        ctx.updated_at = data.get("updated_at", ctx.updated_at)
        return ctx


class ImageContextManager:
    def __init__(
        self,
        kv_tool: KVStoreTool,
        data_dir: str,
        bridge: Optional[Any] = None,
    ):
        self._kv_tool = kv_tool
        self._data_dir = data_dir
        self._bridge = bridge

    async def get_context(self, image_url: str) -> Optional[ImageContext]:
        if self._bridge:
            return await self._get_context_via_bridge(image_url)
        await self._maybe_cleanup_expired_contexts()
        return await self._get_context_internal(image_url)

    async def add_knowledge(
        self, image_url: str, source: str, content: str, **meta
    ) -> None:
        if self._bridge:
            image_hash = await self._resolve_hash(image_url)
            if image_hash:
                await self._bridge.add_knowledge(
                    image_url, image_hash, source, content, **meta
                )
            return
        image_hash = await self._resolve_hash(image_url)
        if not image_hash:
            return
        await self._maybe_cleanup_expired_contexts()
        ctx = await self._get_or_create_context(image_url, image_hash)
        entry = KnowledgeEntry(source, content, **meta)
        ctx.knowledge.append(entry)
        ctx.updated_at = datetime.now(timezone.utc).isoformat()
        await self._save_context(ctx)

    async def update_scene(
        self, image_url: str, scene: str, description: str = ""
    ) -> None:
        if self._bridge:
            image_hash = await self._resolve_hash(image_url)
            if image_hash:
                await self._bridge.update_scene(
                    image_url, image_hash, scene, description
                )
            return
        image_hash = await self._resolve_hash(image_url)
        if not image_hash:
            return
        await self._maybe_cleanup_expired_contexts()
        ctx = await self._get_or_create_context(image_url, image_hash)
        ctx.scene = {"name": scene, "description": description}
        ctx.updated_at = datetime.now(timezone.utc).isoformat()
        await self._save_context(ctx)

    async def update_intent(
        self, image_url: str, keywords: list, distilled: str = ""
    ) -> None:
        if self._bridge:
            image_hash = await self._resolve_hash(image_url)
            if image_hash:
                await self._bridge.update_intent(
                    image_url, image_hash, keywords, distilled
                )
            return
        image_hash = await self._resolve_hash(image_url)
        if not image_hash:
            return
        await self._maybe_cleanup_expired_contexts()
        ctx = await self._get_or_create_context(image_url, image_hash)
        ctx.intent = {"keywords": keywords, "distilled": distilled}
        ctx.updated_at = datetime.now(timezone.utc).isoformat()
        await self._save_context(ctx)

    async def build_vision_prompt_context(
        self, image_url: str, current_prompt: str
    ) -> str:
        if self._bridge:
            image_hash = await self._resolve_hash(image_url)
            if image_hash:
                return await self._bridge.build_vision_context(
                    image_url, image_hash, current_prompt
                )
            return ""
        ctx = await self.get_context(image_url)
        if not ctx or not ctx.knowledge:
            return ""
        parts = ["以下是这张图片的历史分析记录："]
        for entry in ctx.knowledge:
            parts.append(f"- [{entry.source}] {entry.content}")
        parts.append("请基于以上历史分析，结合当前问题进行分析。")
        return "\n".join(parts)

    async def remove_context(self, image_url: str) -> None:
        if self._bridge:
            image_hash = await self._resolve_hash(image_url)
            if image_hash:
                await self._bridge.remove_context(image_url, image_hash)
            return
        image_hash = await self._resolve_hash(image_url)
        if not image_hash:
            return
        cache_key = f"{CONTEXT_KEY_PREFIX}{image_hash}"
        await self._kv_tool.execute(action="delete", key=cache_key)

    async def query_by_knowledge(
        self, source: str, keyword: str = ""
    ) -> List[ImageContext]:
        await self._maybe_cleanup_expired_contexts()
        list_result = await self._kv_tool.execute(action="list")
        if not list_result.success:
            return []
        keys = list_result.data.get("keys", [])
        results = []
        for key in keys:
            if not key.startswith(CONTEXT_KEY_PREFIX):
                continue
            result = await self._kv_tool.execute(action="get", key=key)
            if result.success:
                try:
                    raw = result.data.get("value", "{}")
                    data = raw if isinstance(raw, dict) else json.loads(raw)
                    ctx = ImageContext.from_dict(data)
                    for k in ctx.knowledge:
                        if k.source == source and (not keyword or keyword in k.content):
                            results.append(ctx)
                            break
                except (json.JSONDecodeError, AttributeError):
                    pass
        return results

    async def _get_context_via_bridge(self, image_url: str) -> Optional[ImageContext]:
        image_hash = await self._resolve_hash(image_url)
        if not image_hash:
            return None
        entries = await self._bridge.get_knowledge(image_url, image_hash)
        if not entries:
            return None
        ctx = ImageContext(image_hash, image_url)
        ctx.knowledge = entries
        return ctx

    async def _get_context_internal(self, image_url: str) -> Optional[ImageContext]:
        image_hash = await self._resolve_hash(image_url)
        if not image_hash:
            return None
        cache_key = f"{CONTEXT_KEY_PREFIX}{image_hash}"
        result = await self._kv_tool.execute(action="get", key=cache_key)
        if not result.success:
            return None
        try:
            raw = result.data.get("value", "{}")
            data = raw if isinstance(raw, dict) else json.loads(raw)
            ctx = ImageContext.from_dict(data)
            if self._is_expired(ctx):
                await self._kv_tool.execute(action="delete", key=cache_key)
                return None
            return ctx
        except (json.JSONDecodeError, AttributeError):
            return None

    async def _resolve_hash(self, image_url: str) -> Optional[str]:
        try:
            img_dir = os.path.join(self._data_dir, "cateye", "images")
            image_path = await download_image(image_url, img_dir)
            md5_val, dhash_val = compute_image_hashes(image_path)
            return f"{md5_val}_{dhash_val}" if dhash_val else md5_val
        except Exception as e:
            logger.warning(f"[nekokit.cateye] 解析图片哈希失败: {e}")
            return None

    async def _get_or_create_context(
        self, image_url: str, image_hash: str
    ) -> ImageContext:
        cache_key = f"{CONTEXT_KEY_PREFIX}{image_hash}"
        result = await self._kv_tool.execute(action="get", key=cache_key)
        if result.success:
            try:
                raw = result.data.get("value", "{}")
                data = raw if isinstance(raw, dict) else json.loads(raw)
                return ImageContext.from_dict(data)
            except (json.JSONDecodeError, AttributeError):
                pass
        return ImageContext(image_hash, image_url)

    async def _save_context(self, ctx: ImageContext) -> None:
        cache_key = f"{CONTEXT_KEY_PREFIX}{ctx.image_hash}"
        value_json = json.dumps(ctx.to_dict(), ensure_ascii=False)
        await self._kv_tool.execute(action="set", key=cache_key, value=value_json)

    async def _maybe_cleanup_expired_contexts(self) -> None:
        try:
            today = datetime.now(timezone.utc).date().isoformat()
            last_run = await self._kv_tool.execute(
                action="get", key=CONTEXT_CLEANUP_KEY
            )
            if last_run.success and str(last_run.data.get("value", "")) == today:
                return

            list_result = await self._kv_tool.execute(action="list")
            if not list_result.success:
                return

            deleted = 0
            for key in list_result.data.get("keys", []):
                if not str(key).startswith(CONTEXT_KEY_PREFIX):
                    continue
                result = await self._kv_tool.execute(action="get", key=key)
                if not result.success:
                    continue
                try:
                    raw = result.data.get("value", "{}")
                    data = raw if isinstance(raw, dict) else json.loads(raw)
                    ctx = ImageContext.from_dict(data)
                    if self._is_expired(ctx):
                        await self._kv_tool.execute(action="delete", key=key)
                        deleted += 1
                except (json.JSONDecodeError, AttributeError):
                    continue

            await self._kv_tool.execute(
                action="set", key=CONTEXT_CLEANUP_KEY, value=today
            )
            if deleted:
                logger.info(f"[nekokit.cateye] 已清理 {deleted} 条过期图片上下文")
        except Exception as e:
            logger.warning(f"[nekokit.cateye] 图片上下文清理失败: {e}")

    def _is_expired(self, ctx: ImageContext) -> bool:
        try:
            updated = datetime.fromisoformat(ctx.updated_at)
            expires = updated + timedelta(days=CONTEXT_TTL_DAYS)
            return datetime.now(timezone.utc) > expires
        except (ValueError, TypeError):
            return True
