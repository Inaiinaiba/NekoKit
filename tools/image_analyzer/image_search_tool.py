import base64
import fnmatch
import json
import os
import time
from typing import Any, Dict, List, Optional

import aiohttp
from astrbot.api import logger

from ...core import BaseTool, ToolResult
from ._internal import download_image, preprocess_image

BUILTIN_PROVIDERS = {
    "huawei": {
        "name": "华为云通用搜索",
        "url": "https://image.cn-north-4.myhuaweicloud.com/v2/{project_id}/images/search",
        "scene": "general",
        "builtin": True,
    },
    "tracemoe": {
        "name": "trace.moe 番剧搜索",
        "url": "https://api.trace.moe/search",
        "scene": "anime",
        "builtin": True,
    },
    "saucenao": {
        "name": "SauceNAO 萌系/插画搜索",
        "url": "https://saucenao.com/search.php",
        "scene": "moe",
        "builtin": True,
    },
}

BUILTIN_SCENE_MAP = {
    "anime": "tracemoe",
    "moe": "saucenao",
    "illustration": "saucenao",
    "general": "huawei",
}

CUSTOM_PROVIDER_SPEC = {
    "key": "供应商唯一标识（小写字母+下划线，如 google_lens）",
    "name": "显示名称（如 Google Lens）",
    "url": "API 端点 URL，支持 {api_key} {project_id} 等占位符",
    "scene": "适用场景：anime / moe / illustration / general / custom",
    "method": "请求方法：POST（默认）或 GET",
    "content_type": "请求格式：form（默认，multipart/form-data）或 json",
    "image_field": "图片字段名（默认 image）。form 模式下为表单字段名，json 模式下为 JSON 体中的图片 Base64 字段路径（用 . 分隔层级）",
    "image_encoding": "图片编码方式：binary（默认，原始二进制）或 base64",
    "headers": "自定义请求头 JSON 对象（可选），支持 {api_key} 占位符",
    "params": "URL 查询参数 JSON 对象（可选），支持 {api_key} 占位符",
    "body": "请求体额外字段 JSON 对象（可选，仅 json 模式），支持 {api_key} 占位符。图片数据自动注入 image_field 路径",
    "api_key": "API Key（可选）",
    "response_path": "结果列表的 JSON 路径（如 result 或 data.results），默认 results",
    "result_mapping": "结果字段映射 JSON 对象，将供应商返回字段映射为标准字段：similarity / title / source / url / thumbnail / description / raw",
}


class ImageSearchTool(BaseTool):
    def __init__(self):
        self._data_dir: str = ""
        self._config: Dict[str, Any] = {}
        self._proxy_config: Dict[str, Any] = {}
        self._providers: Dict[str, Dict[str, Any]] = {}
        self._scene_map: Dict[str, str] = dict(BUILTIN_SCENE_MAP)
        self._cached_remote_rules: Optional[List[Dict[str, Any]]] = None
        self._last_rules_fetch: float = 0.0
        self._rules_cache_ttl: float = 300.0
        self._services = None

    def initialize(
        self,
        data_dir: str,
        config: Dict[str, Any] = None,
        proxy_config: Dict[str, Any] = None,
        services=None,
        **kwargs,
    ) -> None:
        self._data_dir = data_dir
        if config:
            self._config = config
        if proxy_config:
            self._proxy_config = proxy_config
        if services:
            self._services = services

        self._providers = dict(BUILTIN_PROVIDERS)
        self._scene_map = dict(BUILTIN_SCENE_MAP)
        self._merge_custom_providers()

    def _merge_custom_providers(self) -> None:
        search_config = self._config.get("search_providers", {})
        custom_raw = search_config.get("custom_providers", "[]")

        if isinstance(custom_raw, str):
            try:
                custom_list = json.loads(custom_raw)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"[nekokit.cateye] 解析自定义供应商配置失败: {e}")
                return
        elif isinstance(custom_raw, list):
            custom_list = custom_raw
        else:
            return

        if not isinstance(custom_list, list):
            logger.warning("[nekokit.cateye] 自定义供应商配置应为 JSON 数组")
            return

        for prov in custom_list:
            if not isinstance(prov, dict):
                continue
            key = prov.get("key", "")
            if not key or key in self._providers:
                logger.warning(f"[nekokit.cateye] 跳过无效或重复的自定义供应商: {key}")
                continue

            provider_entry = {
                "name": prov.get("name", key),
                "url": prov.get("url", ""),
                "scene": prov.get("scene", "general"),
                "builtin": False,
                "method": prov.get("method", "POST"),
                "content_type": prov.get("content_type", "form"),
                "image_field": prov.get("image_field", "image"),
                "image_encoding": prov.get("image_encoding", "binary"),
                "headers": prov.get("headers", {}),
                "params": prov.get("params", {}),
                "body": prov.get("body", {}),
                "api_key": prov.get("api_key", ""),
                "response_path": prov.get("response_path", "results"),
                "result_mapping": prov.get("result_mapping", {}),
            }

            if not provider_entry["url"]:
                logger.warning(f"[nekokit.cateye] 自定义供应商 {key} 缺少 url，跳过")
                continue

            self._providers[key] = provider_entry
            scene = provider_entry["scene"]
            if scene not in self._scene_map:
                self._scene_map[scene] = key

            logger.info(
                f"[nekokit.cateye] 已加载自定义供应商: {key} ({provider_entry['name']})"
            )

    def get_name(self) -> str:
        return "nkit_ce_search"

    def get_description(self) -> str:
        provider_names = "、".join(p["name"] for p in self._providers.values())
        return (
            f"以图搜图工具。支持供应商：{provider_names}。"
            "根据场景自动选择供应商，默认使用代理服务器。"
        )

    def get_parameters(self) -> Dict[str, Any]:
        provider_keys = list(self._providers.keys())
        scene_keys = list(set(list(self._scene_map.keys()) + ["auto"]))
        return {
            "type": "object",
            "properties": {
                "image_url": {
                    "type": "string",
                    "description": (
                        "Image URL or local path, e.g. https://example.com/a.png or "
                        "/AstrBot/data/temp/a.png. The value can be an http/https "
                        "URL or a local path."
                    ),
                },
                "scene": {
                    "type": "string",
                    "description": (
                        "场景类型，用于供应商选择："
                        "auto（尝试所有已启用的）、"
                        + "、".join(
                            f"{s}（{self._scene_map.get(s, '自定义')}）"
                            for s in self._scene_map
                        )
                    ),
                    "enum": scene_keys,
                },
                "provider": {
                    "type": "string",
                    "description": f"强制指定供应商：{'、'.join(provider_keys)}",
                    "enum": provider_keys,
                },
                "use_proxy": {
                    "type": "bool",
                    "description": "是否使用代理（默认遵循全局配置）",
                    "default": None,
                },
            },
            "required": ["image_url"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        image_url = kwargs.get("image_url", "")
        scene = kwargs.get("scene", "auto")
        provider = kwargs.get("provider")
        use_proxy = kwargs.get("use_proxy")

        if use_proxy is None:
            use_proxy = self._proxy_config.get("search_use_proxy", True)

        if not image_url:
            return ToolResult(success=False, message="必须提供 image_url")

        try:
            if self._services and self._services.cache:
                cache_result = await self._services.cache.execute(
                    action="check", image_url=image_url, task_type="search"
                )
                if cache_result.success and cache_result.data.get("hit"):
                    entry = cache_result.data.get("entry", {})
                    cached_result = entry.get("result", {}).get("search_results")
                    if cached_result is not None:
                        logger.info("[nekokit.cateye] 搜图缓存命中")
                        return ToolResult(
                            success=True,
                            message="搜图完成（缓存）",
                            data={"results": cached_result, "cached": True},
                        )

            img_dir = os.path.join(self._data_dir, "cateye", "images")
            image_path = await download_image(image_url, img_dir)

            if self._services and self._services.preprocess:
                output_dir = os.path.join(self._data_dir, "cateye", "preprocessed")
                image_path = preprocess_image(image_path, "search", output_dir)

            providers_to_try = self._resolve_providers(scene, provider)

            all_results = []
            for prov_key in providers_to_try:
                if not self._is_provider_enabled(prov_key):
                    continue
                try:
                    result = await self._call_provider(prov_key, image_path, use_proxy)
                    if result:
                        all_results.extend(result)
                except Exception as e:
                    logger.warning(f"[nekokit.cateye] 供应商 {prov_key} 失败: {e}")
                    continue

            if self._services and self._services.cache:
                await self._services.cache.execute(
                    action="store",
                    image_url=image_url,
                    task_type="search",
                    result=json.dumps({"search_results": all_results}),
                )

            if self._services and self._services.context and all_results:
                top_result = all_results[0]
                summary_parts = []
                if top_result.get("title"):
                    summary_parts.append(top_result["title"])
                if top_result.get("anime"):
                    summary_parts.append(top_result["anime"])
                if top_result.get("source"):
                    summary_parts.append(top_result["source"])
                summary = (
                    ", ".join(str(p) for p in summary_parts[:3])
                    if summary_parts
                    else "找到结果"
                )
                await self._services.context.add_knowledge(
                    image_url,
                    source="nkit_ce_search",
                    content=summary[:200],
                )

            if not all_results:
                return ToolResult(
                    success=True,
                    message="所有供应商均未找到结果",
                    data={"results": [], "providers_tried": providers_to_try},
                )

            logger.info(f"[nekokit.cateye] 搜图完成，找到 {len(all_results)} 条结果")
            return ToolResult(
                success=True,
                message=f"找到 {len(all_results)} 条结果",
                data={"results": all_results},
            )

        except Exception as e:
            logger.error(f"[nekokit.cateye] 以图搜图失败: {e}")
            return ToolResult(success=False, message=f"以图搜图失败: {str(e)}")

    def _resolve_providers(self, scene: str, provider: Optional[str]) -> List[str]:
        if provider:
            return [provider]

        if scene == "auto":
            enabled = []
            for key in self._providers:
                if self._is_provider_enabled(key):
                    enabled.append(key)
            return enabled if enabled else ["tracemoe"]

        mapped = self._scene_map.get(scene, "tracemoe")
        return [mapped]

    def _is_provider_enabled(self, provider_key: str) -> bool:
        prov = self._providers.get(provider_key, {})
        if prov.get("builtin", False):
            search_config = self._config.get("search_providers", {})
            enabled_key = f"{provider_key}_enabled"
            return search_config.get(enabled_key, True)
        return True

    def _get_api_key(self, provider_key: str) -> str:
        prov = self._providers.get(provider_key, {})
        if prov.get("builtin", False):
            search_config = self._config.get("search_providers", {})
            api_key_key = f"{provider_key}_api_key"
            return search_config.get(api_key_key, "")
        return prov.get("api_key", "")

    def _get_proxy_for_url(self, url: str) -> Optional[str]:
        proxy_url = self._proxy_config.get("proxy_url", "")
        if not proxy_url:
            return None

        all_rules = self._load_local_rules()

        remote_url = self._proxy_config.get("custom_rules_url", "")
        if remote_url:
            remote_rules = self._load_remote_rules_with_cache()
            if remote_rules is not None:
                all_rules.extend(remote_rules)

        for rule in all_rules:
            domain_pattern = rule.get("domain", "")
            proxy_mode = rule.get("proxy", "auto")
            if domain_pattern and fnmatch.fnmatch(url, domain_pattern):
                if proxy_mode == "direct":
                    return None
                elif proxy_mode == "auto":
                    return self._build_proxy_url()
                else:
                    return proxy_mode

        return self._build_proxy_url()

    async def _refresh_remote_rules_if_needed(self) -> None:
        remote_url = self._proxy_config.get("custom_rules_url", "")
        if not remote_url:
            return
        if self._cached_remote_rules is None:
            await self._fetch_remote_rules()

    def _load_local_rules(self) -> List[Dict[str, Any]]:
        custom_rules = self._proxy_config.get("custom_rules", "[]")
        try:
            return json.loads(custom_rules)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"[nekokit.cateye] 解析本地代理规则失败: {e}")
            return []

    def _load_remote_rules_with_cache(self) -> Optional[List[Dict[str, Any]]]:
        now = time.time()
        if (
            self._cached_remote_rules is not None
            and (now - self._last_rules_fetch) < self._rules_cache_ttl
        ):
            return self._cached_remote_rules
        return None

    async def _fetch_remote_rules(self) -> List[Dict[str, Any]]:
        remote_url = self._proxy_config.get("custom_rules_url", "")
        if not remote_url:
            return []

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    remote_url, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        logger.warning(
                            f"[nekokit.cateye] 远程规则获取失败: HTTP {resp.status}"
                        )
                        return []
                    data = await resp.json()

            if isinstance(data, list):
                self._cached_remote_rules = data
                self._last_rules_fetch = time.time()
                logger.info(
                    f"[nekokit.cateye] 远程规则已加载: {len(data)} 条 (缓存 5 分钟)"
                )
                return data
            elif isinstance(data, dict) and "rules" in data:
                rules = data["rules"]
                self._cached_remote_rules = rules
                self._last_rules_fetch = time.time()
                logger.info(
                    f"[nekokit.cateye] 远程规则已加载: {len(rules)} 条 (缓存 5 分钟)"
                )
                return rules
            else:
                logger.warning("[nekokit.cateye] 远程规则格式无效，期望 JSON 数组")
                return []
        except Exception as e:
            logger.warning(f"[nekokit.cateye] 远程规则拉取失败: {e}")
            return []

    def _build_proxy_url(self) -> str:
        proxy_url = self._proxy_config.get("proxy_url", "")
        if not proxy_url:
            return ""

        username = self._proxy_config.get("proxy_username", "")
        password = self._proxy_config.get("proxy_password", "")

        if username and password:
            parts = proxy_url.split("://", 1)
            if len(parts) == 2:
                scheme, rest = parts
                return f"{scheme}://{username}:{password}@{rest}"

        return proxy_url

    async def _call_provider(
        self, provider_key: str, image_path: str, use_proxy: bool
    ) -> List[Dict[str, Any]]:
        if use_proxy:
            await self._refresh_remote_rules_if_needed()

        prov = self._providers.get(provider_key)
        if not prov:
            logger.warning(f"[nekokit.cateye] 未知供应商: {provider_key}")
            return []

        if prov.get("builtin", False):
            if provider_key == "tracemoe":
                return await self._call_tracemoe(image_path, use_proxy)
            elif provider_key == "saucenao":
                return await self._call_saucenao(image_path, use_proxy)
            elif provider_key == "huawei":
                return await self._call_huawei(image_path, use_proxy)
            else:
                logger.warning(f"[nekokit.cateye] 未实现的内置供应商: {provider_key}")
                return []

        return await self._call_custom(provider_key, image_path, use_proxy)

    async def _call_custom(
        self, provider_key: str, image_path: str, use_proxy: bool
    ) -> List[Dict[str, Any]]:
        prov = self._providers[provider_key]
        api_key = prov.get("api_key", "")

        url = prov["url"]
        url = url.replace("{api_key}", api_key)

        project_id = ""
        if provider_key == "huawei":
            project_id = self._config.get("search_providers", {}).get(
                "huawei_project_id", ""
            )
        url = url.replace("{project_id}", project_id)

        method = prov.get("method", "POST").upper()
        content_type = prov.get("content_type", "form")
        image_field = prov.get("image_field", "image")
        image_encoding = prov.get("image_encoding", "binary")

        with open(image_path, "rb") as f:
            image_data = f.read()

        headers = self._resolve_template_dict(prov.get("headers", {}), api_key)
        params = self._resolve_template_dict(prov.get("params", {}), api_key)

        proxy_url = self._get_proxy_for_url(url) if use_proxy else None
        timeout = aiohttp.ClientTimeout(total=30)

        async with aiohttp.ClientSession() as session:
            if method == "GET":
                if image_encoding == "base64":
                    b64 = base64.b64encode(image_data).decode("utf-8")
                    params[image_field] = b64
                else:
                    params[image_field] = image_data

                request_kwargs = {
                    "params": params,
                    "headers": headers,
                    "timeout": timeout,
                }
                if proxy_url:
                    request_kwargs["proxy"] = proxy_url

                async with session.get(url, **request_kwargs) as resp:
                    if resp.status != 200:
                        raise RuntimeError(
                            f"{prov['name']} API 错误: HTTP {resp.status}"
                        )
                    data = await resp.json()
            else:
                if content_type == "json":
                    b64 = base64.b64encode(image_data).decode("utf-8")
                    body = self._resolve_template_dict(prov.get("body", {}), api_key)
                    self._set_nested(body, image_field, b64)
                    if headers.get("Content-Type") is None:
                        headers["Content-Type"] = "application/json"

                    request_kwargs = {
                        "json": body,
                        "headers": headers,
                        "params": params,
                        "timeout": timeout,
                    }
                    if proxy_url:
                        request_kwargs["proxy"] = proxy_url

                    async with session.post(url, **request_kwargs) as resp:
                        if resp.status != 200:
                            raise RuntimeError(
                                f"{prov['name']} API 错误: HTTP {resp.status}"
                            )
                        data = await resp.json()
                else:
                    form = aiohttp.FormData()
                    if image_encoding == "base64":
                        b64 = base64.b64encode(image_data).decode("utf-8")
                        form.add_field(image_field, b64)
                    else:
                        form.add_field(
                            image_field,
                            image_data,
                            filename="image.jpg",
                            content_type="image/jpeg",
                        )

                    extra_body = self._resolve_template_dict(
                        prov.get("body", {}), api_key
                    )
                    for k, v in extra_body.items():
                        form.add_field(k, str(v))

                    request_kwargs = {
                        "data": form,
                        "headers": headers,
                        "params": params,
                        "timeout": timeout,
                    }
                    if proxy_url:
                        request_kwargs["proxy"] = proxy_url

                    async with session.post(url, **request_kwargs) as resp:
                        if resp.status != 200:
                            raise RuntimeError(
                                f"{prov['name']} API 错误: HTTP {resp.status}"
                            )
                        data = await resp.json()

        return self._parse_custom_response(prov, data)

    def _parse_custom_response(
        self, prov: Dict[str, Any], data: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        response_path = prov.get("response_path", "results")
        items = self._get_nested(data, response_path)

        if not isinstance(items, list):
            if isinstance(items, dict):
                items = [items]
            else:
                logger.warning(
                    f"[nekokit.cateye] 供应商 {prov['name']} 返回数据路径 {response_path} 不是列表"
                )
                return []

        result_mapping = prov.get("result_mapping", {})
        default_mapping = {
            "similarity": "similarity",
            "title": "title",
            "source": "source",
            "url": "url",
            "thumbnail": "thumbnail",
            "description": "description",
        }
        mapping = {**default_mapping, **result_mapping}

        results = []
        for item in items[:5]:
            if not isinstance(item, dict):
                continue
            result = {"provider": prov["name"]}
            for std_field, src_field in mapping.items():
                val = self._get_nested(item, src_field)
                if val is not None:
                    result[std_field] = val
            if "raw" not in result:
                result["raw"] = item
            results.append(result)

        return results

    @staticmethod
    def _resolve_template_dict(template: Any, api_key: str) -> Dict[str, Any]:
        if isinstance(template, dict):
            return {
                k: v.replace("{api_key}", api_key) if isinstance(v, str) else v
                for k, v in template.items()
            }
        if isinstance(template, str):
            try:
                parsed = json.loads(template)
                if isinstance(parsed, dict):
                    return ImageSearchTool._resolve_template_dict(parsed, api_key)
            except (json.JSONDecodeError, Exception):
                pass
        return {}

    @staticmethod
    def _set_nested(data: dict, path: str, value: Any) -> None:
        keys = path.split(".")
        current = data
        for key in keys[:-1]:
            if key not in current or not isinstance(current[key], dict):
                current[key] = {}
            current = current[key]
        current[keys[-1]] = value

    @staticmethod
    def _get_nested(data: Any, path: str) -> Any:
        keys = path.split(".")
        current = data
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return None
        return current

    async def _call_tracemoe(
        self, image_path: str, use_proxy: bool
    ) -> List[Dict[str, Any]]:
        url = BUILTIN_PROVIDERS["tracemoe"]["url"]
        with open(image_path, "rb") as f:
            image_data = f.read()

        proxy_url = self._get_proxy_for_url(url) if use_proxy else None

        timeout = aiohttp.ClientTimeout(total=30)
        connector = None
        if proxy_url:
            connector = aiohttp.TCPConnector(ssl=False)

        async with aiohttp.ClientSession(connector=connector) as session:
            if proxy_url:
                async with session.post(
                    url, data={"image": image_data}, proxy=proxy_url, timeout=timeout
                ) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"trace.moe API 错误: HTTP {resp.status}")
                    data = await resp.json()
            else:
                form = aiohttp.FormData()
                form.add_field(
                    "image", image_data, filename="image.jpg", content_type="image/jpeg"
                )
                async with session.post(url, data=form, timeout=timeout) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"trace.moe API 错误: HTTP {resp.status}")
                    data = await resp.json()

        results = []
        for item in data.get("result", [])[:5]:
            results.append(
                {
                    "provider": "trace.moe",
                    "similarity": item.get("similarity", 0),
                    "anime": item.get("anime", ""),
                    "episode": item.get("episode", ""),
                    "from_time": item.get("from", 0),
                    "to_time": item.get("to", 0),
                    "filename": item.get("filename", ""),
                    "video_url": item.get("video", ""),
                }
            )
        return results

    async def _call_saucenao(
        self, image_path: str, use_proxy: bool
    ) -> List[Dict[str, Any]]:
        api_key = self._get_api_key("saucenao")
        url = BUILTIN_PROVIDERS["saucenao"]["url"]

        with open(image_path, "rb") as f:
            image_data = f.read()

        params = {
            "output_type": 2,
            "numres": 5,
        }
        if api_key:
            params["api_key"] = api_key

        proxy_url = self._get_proxy_for_url(url) if use_proxy else None

        async with aiohttp.ClientSession() as session:
            form = aiohttp.FormData()
            form.add_field(
                "file", image_data, filename="image.jpg", content_type="image/jpeg"
            )
            for k, v in params.items():
                form.add_field(k, str(v))

            if proxy_url:
                async with session.post(
                    url,
                    data=form,
                    proxy=proxy_url,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"SauceNAO API 错误: HTTP {resp.status}")
                    data = await resp.json()
            else:
                async with session.post(
                    url, data=form, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"SauceNAO API 错误: HTTP {resp.status}")
                    data = await resp.json()

        results = []
        for item in data.get("results", [])[:5]:
            header = item.get("header", {})
            data_section = item.get("data", {})
            results.append(
                {
                    "provider": "SauceNAO",
                    "similarity": float(header.get("similarity", 0)),
                    "title": data_section.get(
                        "title", data_section.get("material", "")
                    ),
                    "source": data_section.get("source", ""),
                    "ext_urls": data_section.get("ext_urls", []),
                    "thumbnail": header.get("thumbnail", ""),
                }
            )
        return results

    async def _call_huawei(
        self, image_path: str, use_proxy: bool
    ) -> List[Dict[str, Any]]:
        api_key = self._get_api_key("huawei")
        if not api_key:
            logger.warning("[nekokit.cateye] 华为云 API Key 未配置")
            return []

        with open(image_path, "rb") as f:
            image_data = f.read()
        b64_image = base64.b64encode(image_data).decode("utf-8")

        project_id = self._config.get("search_providers", {}).get(
            "huawei_project_id", ""
        )
        if not project_id:
            logger.warning("[nekokit.cateye] 华为云 project_id 未配置")
            return []

        url = BUILTIN_PROVIDERS["huawei"]["url"].format(project_id=project_id)
        headers = {"X-Auth-Token": api_key, "Content-Type": "application/json"}
        body = {"image": b64_image, "limit": 5}

        proxy_url = self._get_proxy_for_url(url) if use_proxy else None

        async with aiohttp.ClientSession() as session:
            if proxy_url:
                async with session.post(
                    url,
                    json=body,
                    headers=headers,
                    proxy=proxy_url,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"华为云 API 错误: HTTP {resp.status}")
                    data = await resp.json()
            else:
                async with session.post(
                    url,
                    json=body,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"华为云 API 错误: HTTP {resp.status}")
                    data = await resp.json()

        results = []
        for item in data.get("results", [])[:5]:
            results.append(
                {
                    "provider": "Huawei Cloud",
                    "similarity": item.get("similarity", 0),
                    "source": item.get("source", ""),
                    "description": item.get("description", ""),
                }
            )
        return results
