import json
import os
import re

from astrbot.api import logger
from astrbot.api import star
from astrbot.api.star import StarTools
from astrbot.api import AstrBotConfig
from astrbot.api.message_components import Image, Reply
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.utils.astrbot_path import get_astrbot_workspaces_path
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Tuple
from urllib.parse import unquote, urlparse

from .tools import KVStoreTool
from .tools.image_analyzer import (
    OCRTool,
    ImageSearchTool,
    VisionTool,
    PreprocessTool,
    CacheTool,
    ScenePresetTool,
    CateyeServices,
    ImageContextManager,
)
from .tools.image_analyzer.angel_memory_bridge import AngelMemoryBridge
from .core import ToolResult


IMAGE_URL_DESCRIPTION = (
    'Image URL or local path, e.g. "https://example.com/a.png" or '
    '"/AstrBot/data/temp/a.png". The value can be an http/https URL or a local '
    "path. Local paths support AstrBot temp directory files and non-sandbox "
    "workspace files. AstrBot temp directory paths usually contain data/temp; "
    "files retrieved from the sandbox with astrbot_download_file are automatically "
    "placed in the temp directory."
)


def _parse_image_url(image_url: Any) -> str:
    if not image_url:
        return ""
    if isinstance(image_url, (list, tuple, dict)):
        raise ValueError("image_url 只接受单个图片 URL 或路径")

    image = str(image_url).strip()
    if not image:
        return ""
    parsed = urlparse(image)
    if parsed.scheme and parsed.scheme not in ("http", "https", "file"):
        raise ValueError(f"无效图片输入：{image[:120]}")
    if parsed.scheme in ("http", "https") and not parsed.netloc:
        raise ValueError(f"无效图片URL：{image[:120]}")
    return image


def _get_event_from_context(context: ContextWrapper[AstrAgentContext]):
    try:
        return context.context.event
    except Exception:
        return None


def _image_url_to_local_path(image_url: str) -> str:
    parsed = urlparse(image_url)
    if parsed.scheme == "file":
        path = unquote(parsed.path)
        if os.name == "nt" and re.match(r"^/[a-zA-Z]:/", path):
            path = path[1:]
        return path
    return image_url


def _workspace_root_for_event(event) -> Optional[Path]:
    umo = getattr(event, "unified_msg_origin", None)
    if not umo:
        return None
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", str(umo).strip()) or "unknown"
    return (Path(get_astrbot_workspaces_path()) / normalized).resolve(strict=False)


def _resolve_image_local_path(event, image_path: str) -> str:
    path = _image_url_to_local_path(image_path).strip()
    if not path:
        raise ValueError("图片路径为空")

    # Absolute path access is governed by AstrBot/runtime filesystem permissions.
    # This helper only resolves relative paths against the current workspace.
    if os.path.isabs(path):
        return path

    workspace_root = _workspace_root_for_event(event)
    if workspace_root is None:
        raise ValueError("无法获取当前会话 workspace，不能使用相对图片路径")

    candidate = (workspace_root / path).resolve(strict=False)
    try:
        candidate.relative_to(workspace_root)
    except ValueError:
        raise ValueError("相对图片路径不能超出当前 workspace")
    return str(candidate)


async def _collect_image_url(
    context: ContextWrapper[AstrAgentContext], image_url: Any = None
) -> Tuple[str, Optional[str]]:
    event = _get_event_from_context(context)
    try:
        image = _parse_image_url(image_url)
    except Exception as e:
        return "", str(e)

    if image:
        parsed = urlparse(image)
        if parsed.scheme in ("http", "https"):
            return image, None
        try:
            path = _resolve_image_local_path(event, image)
            if not os.path.isfile(path):
                raise FileNotFoundError("图片文件不存在")
            return path, None
        except Exception as e:
            return "", f"图片失败：{str(e)[:200]}"

    if not event:
        return "", "必须提供 image_url"

    msgs = event.get_messages()
    img_segs = [m for m in msgs if isinstance(m, Image)]
    reply = next((s for s in msgs if isinstance(s, Reply)), None)
    if not img_segs and reply and reply.chain:
        img_segs = [s for s in reply.chain if isinstance(s, Image)]
    if not img_segs:
        return "", "未找到图片，请提供 image_url 或在当前消息/回复消息中附带图片"

    try:
        path = await img_segs[0].convert_to_file_path()
        if not os.path.isfile(path):
            raise FileNotFoundError("文件下载失败")
        return path, None
    except Exception as e:
        return "", f"图片失败：{str(e)[:200]}"


async def _prepare_image_url_kwarg(
    context: ContextWrapper[AstrAgentContext], kwargs: dict
) -> Optional[str]:
    image_url = kwargs.get("image_url")
    image, err = await _collect_image_url(context, image_url)
    if err:
        return err
    kwargs["image_url"] = image
    return None


@dataclass
class KVGetTool(FunctionTool[AstrAgentContext]):
    name: str = "nkit_kv_get"
    description: str = "根据键名获取存储的值"
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "要获取的键名",
                },
            },
            "required": ["key"],
        }
    )

    _kv_tool: KVStoreTool = None

    @classmethod
    def create_with_tool(cls, kv_tool: KVStoreTool) -> "KVGetTool":
        tool = cls()
        tool._kv_tool = kv_tool
        return tool

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        if not self._kv_tool:
            return "KVStoreTool 未初始化"

        self._kv_tool.set_context(context)
        try:
            result: ToolResult = await self._kv_tool.execute(action="get", **kwargs)
            if result.success:
                return json.dumps(result.to_dict(), ensure_ascii=False)
            else:
                return result.message
        except Exception as e:
            logger.error(f"[KVGetTool] 执行失败: {e}")
            return f"执行失败: {str(e)}"


@dataclass
class KVSetTool(FunctionTool[AstrAgentContext]):
    name: str = "nkit_kv_set"
    description: str = "设置或更新键值对"
    parameters: dict = field(
        default_factory=lambda: {
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
        }
    )

    _kv_tool: KVStoreTool = None

    @classmethod
    def create_with_tool(cls, kv_tool: KVStoreTool) -> "KVSetTool":
        tool = cls()
        tool._kv_tool = kv_tool
        return tool

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        if not self._kv_tool:
            return "KVStoreTool 未初始化"

        self._kv_tool.set_context(context)
        try:
            result: ToolResult = await self._kv_tool.execute(action="set", **kwargs)
            if result.success:
                return result.message
            else:
                return result.message
        except Exception as e:
            logger.error(f"[KVSetTool] 执行失败: {e}")
            return f"执行失败: {str(e)}"


@dataclass
class KVDeleteTool(FunctionTool[AstrAgentContext]):
    name: str = "nkit_kv_delete"
    description: str = "根据键名删除存储的值"
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "要删除的键名",
                },
            },
            "required": ["key"],
        }
    )

    _kv_tool: KVStoreTool = None

    @classmethod
    def create_with_tool(cls, kv_tool: KVStoreTool) -> "KVDeleteTool":
        tool = cls()
        tool._kv_tool = kv_tool
        return tool

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        if not self._kv_tool:
            return "KVStoreTool 未初始化"

        self._kv_tool.set_context(context)
        try:
            result: ToolResult = await self._kv_tool.execute(action="delete", **kwargs)
            if result.success:
                return result.message
            else:
                return result.message
        except Exception as e:
            logger.error(f"[KVDeleteTool] 执行失败: {e}")
            return f"执行失败: {str(e)}"


@dataclass
class KVListTool(FunctionTool[AstrAgentContext]):
    name: str = "nkit_kv_list"
    description: str = "列出当前作用域下的所有键"
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
        }
    )

    _kv_tool: KVStoreTool = None

    @classmethod
    def create_with_tool(cls, kv_tool: KVStoreTool) -> "KVListTool":
        tool = cls()
        tool._kv_tool = kv_tool
        return tool

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        if not self._kv_tool:
            return "KVStoreTool 未初始化"

        self._kv_tool.set_context(context)
        try:
            result: ToolResult = await self._kv_tool.execute(action="list", **kwargs)
            if result.success:
                return json.dumps(result.to_dict(), ensure_ascii=False)
            else:
                return result.message
        except Exception as e:
            logger.error(f"[KVListTool] 执行失败: {e}")
            return f"执行失败: {str(e)}"


@dataclass
class CateyeOCRTool(FunctionTool[AstrAgentContext]):
    name: str = "nkit_ce_ocr"
    description: str = (
        "使用 RapidOCR 引擎提取图片中的文字，返回纯文本。默认支持中文和英文。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "image_url": {
                    "type": "string",
                    "description": IMAGE_URL_DESCRIPTION,
                },
            },
            "required": [],
        }
    )

    _ocr_tool: OCRTool = None

    @classmethod
    def create_with_tool(cls, ocr_tool: OCRTool) -> "CateyeOCRTool":
        tool = cls()
        tool._ocr_tool = ocr_tool
        return tool

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        if not self._ocr_tool:
            return "OCRTool 未初始化"
        try:
            err = await _prepare_image_url_kwarg(context, kwargs)
            if err:
                return err
            result: ToolResult = await self._ocr_tool.execute(**kwargs)
            if result.success:
                return json.dumps(result.to_dict(), ensure_ascii=False)
            else:
                return result.message
        except Exception as e:
            logger.error(f"[nekokit.cateye] OCR 执行失败: {e}")
            return f"OCR 执行失败: {str(e)}"


@dataclass
class CateyeSearchTool(FunctionTool[AstrAgentContext]):
    name: str = "nkit_ce_search"
    description: str = (
        "以图搜图工具，支持华为云、trace.moe、SauceNAO 等多个供应商，根据场景自动选择。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "image_url": {
                    "type": "string",
                    "description": IMAGE_URL_DESCRIPTION,
                },
                "scene": {
                    "type": "string",
                    "description": (
                        "场景类型，用于供应商选择："
                        "auto（尝试所有已启用的）、anime（番剧）、moe（萌系）、illustration（插画）、general（通用）"
                    ),
                    "enum": ["auto", "anime", "moe", "illustration", "general"],
                },
                "provider": {
                    "type": "string",
                    "description": "强制指定供应商：huawei、tracemoe、saucenao",
                    "enum": ["huawei", "tracemoe", "saucenao"],
                },
            },
            "required": [],
        }
    )

    _search_tool: ImageSearchTool = None

    @classmethod
    def create_with_tool(cls, search_tool: ImageSearchTool) -> "CateyeSearchTool":
        tool = cls()
        tool._search_tool = search_tool
        return tool

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        if not self._search_tool:
            return "ImageSearchTool 未初始化"
        try:
            err = await _prepare_image_url_kwarg(context, kwargs)
            if err:
                return err
            result: ToolResult = await self._search_tool.execute(**kwargs)
            if result.success:
                return json.dumps(result.to_dict(), ensure_ascii=False)
            else:
                return result.message
        except Exception as e:
            logger.error(f"[nekokit.cateye] Search 执行失败: {e}")
            return f"Search 执行失败: {str(e)}"


@dataclass
class CateyeVisionTool(FunctionTool[AstrAgentContext]):
    name: str = "nkit_ce_vision"
    description: str = (
        "调用多模态大模型对图片进行理解、描述或推理，支持日常模式和专业模式。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "image_url": {
                    "type": "string",
                    "description": IMAGE_URL_DESCRIPTION,
                },
                "prompt": {
                    "type": "string",
                    "description": "对图片的理解或描述需求",
                },
                "mode": {
                    "type": "string",
                    "description": (
                        "模式：daily（日常任务，如表情包、日常场景）"
                        "或 professional（专业任务，如复杂图表分析、习题解答）"
                    ),
                    "enum": ["daily", "professional"],
                },
            },
            "required": ["prompt"],
        }
    )

    _vision_tool: VisionTool = None

    @classmethod
    def create_with_tool(cls, vision_tool: VisionTool) -> "CateyeVisionTool":
        tool = cls()
        tool._vision_tool = vision_tool
        return tool

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        if not self._vision_tool:
            return "VisionTool 未初始化"
        try:
            err = await _prepare_image_url_kwarg(context, kwargs)
            if err:
                return err
            result: ToolResult = await self._vision_tool.execute(**kwargs)
            if result.success:
                return json.dumps(result.to_dict(), ensure_ascii=False)
            else:
                return result.message
        except Exception as e:
            logger.error(f"[nekokit.cateye] Vision 执行失败: {e}")
            return f"Vision 执行失败: {str(e)}"


@dataclass
class CateyeSceneTool(FunctionTool[AstrAgentContext]):
    name: str = "nkit_ce_scene"
    description: str = (
        "场景预设工具。根据场景编码返回工具组合策略，"
        "指导按步骤调用 cateye 工具集。"
        "支持查看预设列表、获取具体方案、自定义修改方案。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "操作类型：list（列出所有预设）、"
                        "get（获取指定预设的方案）、"
                        "update（更新预设方案，需提供 scene_code 和 preset_json）"
                    ),
                    "enum": ["list", "get", "update"],
                },
                "scene_code": {
                    "type": "string",
                    "description": "场景编码，如 extract_text、identify_character、find_anime_source 等",
                },
                "preset_json": {
                    "type": "string",
                    "description": (
                        "预设方案的 JSON 字符串（update 操作时必填）。"
                        '格式: {"name": "场景名", "description": "描述", '
                        '"steps": [{"tool": "工具名", "params": {参数}}]}'
                    ),
                },
            },
            "required": ["action"],
        }
    )

    _scene_tool: ScenePresetTool = None

    @classmethod
    def create_with_tool(cls, scene_tool: ScenePresetTool) -> "CateyeSceneTool":
        tool = cls()
        tool._scene_tool = scene_tool
        return tool

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        if not self._scene_tool:
            return "ScenePresetTool 未初始化"
        try:
            result: ToolResult = await self._scene_tool.execute(**kwargs)
            if result.success:
                return json.dumps(result.to_dict(), ensure_ascii=False)
            else:
                return result.message
        except Exception as e:
            logger.error(f"[nekokit.cateye] Scene 执行失败: {e}")
            return f"Scene 执行失败: {str(e)}"


class Main(star.Star):
    """NekoKit 插件主类"""

    NEKOKIT_MANAGED_DATA = {
        "cateye_context": {
            "description": "CatEye 图片认知上下文",
            "bridge_class": "AngelMemoryBridge",
            "module": "nekokit.tools.image_analyzer.angel_memory_bridge",
        }
    }

    def __init__(self, context: star.Context, config: AstrBotConfig = None) -> None:
        super().__init__(context)
        self.context = context

        self.data_dir = str(StarTools.get_data_dir("nekokit"))

        self._kv_tool = KVStoreTool()
        self._kv_tool.initialize(self.data_dir)

        if config:
            kv_store = config.get("kv_store", {})
            kvstore_config = {}
            if kv_store.get("ai_isolation") is not None:
                kvstore_config["ai_isolation"] = kv_store["ai_isolation"]
            if kv_store.get("session_scope") is not None:
                kvstore_config["session_scope"] = kv_store["session_scope"]
            self._kv_tool.set_config(kvstore_config)
            logger.info(f"[NekoKit] 已加载配置: {kvstore_config}")

        self.image_context_manager = None

        self._init_cateye_tools(config)

        self._register_tools()

        logger.info("[NekoKit] 插件已加载，已注册 KV 存储工具和 Cateye 图片识别工具集")

    def _init_cateye_tools(self, config: AstrBotConfig = None) -> None:
        cateye_config = self._build_cateye_config(config)
        proxy_config = self._build_proxy_config(config)

        self._preprocess_tool = PreprocessTool()
        self._preprocess_tool.initialize(self.data_dir, cateye_config)

        self._cache_tool = CacheTool()
        self._cache_tool.initialize(self.data_dir, cateye_config, kv_tool=self._kv_tool)

        context_backend = cateye_config.get("context_backend", "internal")
        bridge = None
        if context_backend == "angel_memory":
            bridge = self._create_angel_memory_bridge()
            if bridge is None:
                logger.warning("[nekokit] 天使之魂插件未加载，降级为内部 context")

        self.image_context_manager = ImageContextManager(
            self._kv_tool, self.data_dir, bridge=bridge
        )

        services = CateyeServices(
            preprocess=self._preprocess_tool,
            cache=self._cache_tool,
            context=self.image_context_manager,
        )

        self._ocr_tool = OCRTool()
        self._ocr_tool.initialize(self.data_dir, cateye_config, services=services)

        self._search_tool = ImageSearchTool()
        self._search_tool.initialize(
            self.data_dir, cateye_config, proxy_config=proxy_config, services=services
        )

        self._vision_tool = VisionTool()
        self._vision_tool.initialize(
            self.data_dir,
            cateye_config,
            star_context=self.context,
            services=services,
        )

        self._scene_tool = ScenePresetTool()
        self._scene_tool.initialize(self.data_dir, cateye_config, kv_tool=self._kv_tool)

    def _create_angel_memory_bridge(self):
        try:
            from astrbot_plugin_angel_memory.core.memory_runtime import MemoryRuntime

            for plugin in self.context.get_all_stars():
                if hasattr(plugin, "memory_runtime") and isinstance(
                    plugin.memory_runtime, MemoryRuntime
                ):
                    return AngelMemoryBridge(plugin.memory_runtime, plugin.context)
        except ImportError:
            logger.warning("[nekokit] 天使之魂记忆插件未安装")
        except Exception as e:
            logger.warning(f"[nekokit] 创建天使之魂桥接失败: {e}")
        return None

    def _build_cateye_config(self, config: AstrBotConfig = None) -> dict:
        cateye_config = {}
        if not config:
            return cateye_config

        general = config.get("cateye_general", {})
        cateye_config["log_level"] = general.get("log_level", "INFO")
        cateye_config["custom_prompt_enabled"] = general.get(
            "custom_prompt_enabled", False
        )
        cateye_config["custom_prompt"] = general.get("custom_prompt", "")
        cateye_config["context_backend"] = general.get("context_backend", "internal")

        ocr = config.get("cateye_ocr", {})
        cateye_config["ocr_text_score"] = ocr.get("text_score", 0.5)

        search = config.get("cateye_search", {})
        tracemoe = search.get("tracemoe", {})
        saucenao = search.get("saucenao", {})
        huawei = search.get("huawei", {})
        search_providers = {
            "tracemoe_enabled": tracemoe.get("enabled", True),
            "tracemoe_api_key": tracemoe.get("api_key", ""),
            "saucenao_enabled": saucenao.get("enabled", True),
            "saucenao_api_key": saucenao.get("api_key", ""),
            "huawei_enabled": huawei.get("enabled", False),
            "huawei_api_key": huawei.get("api_key", ""),
            "huawei_project_id": huawei.get("project_id", ""),
            "custom_providers": search.get("custom_providers", ""),
        }
        cateye_config["search_providers"] = search_providers

        vision = config.get("cateye_vision", {})
        vision_models = {
            "daily_model": vision.get("daily_model", ""),
            "professional_model": vision.get("professional_model", ""),
        }
        cateye_config["vision_models"] = vision_models

        logger.info("[nekokit.cateye] 已加载图片识别配置")
        return cateye_config

    def _build_proxy_config(self, config: AstrBotConfig = None) -> dict:
        proxy_config = {}
        if not config:
            return proxy_config

        network_proxy = config.get("network_proxy", {})
        proxy_config["proxy_url"] = network_proxy.get("proxy_url", "")

        proxy_auth = network_proxy.get("proxy_auth", {})
        proxy_config["proxy_username"] = proxy_auth.get("username", "")
        proxy_config["proxy_password"] = proxy_auth.get("password", "")

        proxy_config["search_use_proxy"] = network_proxy.get("search_use_proxy", True)

        proxy_rules = network_proxy.get("proxy_rules", {})
        proxy_config["custom_rules"] = proxy_rules.get("custom_rules", "[]")
        proxy_config["custom_rules_url"] = proxy_rules.get("custom_rules_url", "")

        logger.info("[nekokit] 已加载网络代理配置")
        return proxy_config

    def _register_tools(self):
        tools = [
            KVGetTool.create_with_tool(self._kv_tool),
            KVSetTool.create_with_tool(self._kv_tool),
            KVDeleteTool.create_with_tool(self._kv_tool),
            KVListTool.create_with_tool(self._kv_tool),
            CateyeOCRTool.create_with_tool(self._ocr_tool),
            CateyeSearchTool.create_with_tool(self._search_tool),
            CateyeVisionTool.create_with_tool(self._vision_tool),
            CateyeSceneTool.create_with_tool(self._scene_tool),
        ]
        self.context.add_llm_tools(*tools)

    async def terminate(self):
        if hasattr(self, "_kv_tool") and self._kv_tool:
            logger.info("[NekoKit] 插件卸载，清理资源")
