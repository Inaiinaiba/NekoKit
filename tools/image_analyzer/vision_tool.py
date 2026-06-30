import json
import os
from typing import Any, Dict, Optional

from astrbot.api import logger

from ...core import BaseTool, ToolResult
from ._internal import download_image, image_to_base64_url, preprocess_image


class VisionTool(BaseTool):
    def __init__(self):
        self._data_dir: str = ""
        self._config: Dict[str, Any] = {}
        self._star_context = None
        self._services = None

    def initialize(
        self,
        data_dir: str,
        config: Dict[str, Any] = None,
        star_context=None,
        services=None,
        **kwargs,
    ) -> None:
        self._data_dir = data_dir
        if config:
            self._config = config
        if star_context:
            self._star_context = star_context
        if services:
            self._services = services

    def get_name(self) -> str:
        return "nkit_ce_vision"

    def get_description(self) -> str:
        return (
            "视觉理解工具。调用多模态大模型对图片进行理解、描述或推理。"
            "支持日常模式和专业模式。"
        )

    def get_parameters(self) -> Dict[str, Any]:
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
            "required": ["image_url", "prompt"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        image_url = kwargs.get("image_url", "")
        prompt = kwargs.get("prompt", "")
        mode = kwargs.get("mode", "daily")

        if not image_url:
            return ToolResult(success=False, message="必须提供 image_url")
        if not prompt:
            return ToolResult(success=False, message="必须提供 prompt")

        if not self._star_context:
            return ToolResult(success=False, message="AstrBot 上下文不可用")

        try:
            context_injection = ""
            if self._services and self._services.cache:
                cache_result = await self._services.cache.execute(
                    action="check", image_url=image_url, task_type="vision"
                )
                if cache_result.success and cache_result.data.get("hit"):
                    if self._services and self._services.context:
                        context_injection = (
                            await self._services.context.build_vision_prompt_context(
                                image_url, prompt
                            )
                        )

            img_dir = os.path.join(self._data_dir, "cateye", "images")
            image_path = await download_image(image_url, img_dir)

            if self._services and self._services.preprocess:
                output_dir = os.path.join(self._data_dir, "cateye", "preprocessed")
                image_path = preprocess_image(image_path, "vision", output_dir)

            provider_id = self._resolve_provider(mode)
            if not provider_id:
                return ToolResult(
                    success=False,
                    message=f"插件设置中未配置 {mode} 模型",
                )

            kwargs.pop("mode", None)
            system_prompt = self._build_system_prompt(mode, context_injection)
            b64_url = image_to_base64_url(image_path)

            user_prompt = prompt

            llm_resp = await self._star_context.llm_generate(
                chat_provider_id=provider_id,
                prompt=user_prompt,
                image_urls=[b64_url],
                system_prompt=system_prompt,
            )

            analysis = llm_resp.completion_text if llm_resp else ""

            if self._services and self._services.cache:
                await self._services.cache.execute(
                    action="store",
                    image_url=image_url,
                    task_type="vision",
                    result=json.dumps({"vision_analysis": analysis[:500]}),
                )

            if self._services and self._services.context:
                await self._services.context.add_knowledge(
                    image_url,
                    source="nkit_ce_vision",
                    content=analysis[:200] if analysis else "(无分析结果)",
                    mode=mode,
                )

            logger.info(f"[nekokit.cateye] 视觉分析完成（{mode} 模式）")
            return ToolResult(
                success=True,
                message=f"视觉分析完成（{mode} 模式）",
                data={"analysis": analysis, "mode": mode},
            )

        except Exception as e:
            logger.error(f"[nekokit.cateye] 视觉分析失败: {e}")
            return ToolResult(success=False, message=f"视觉分析失败: {str(e)}")

    def _resolve_provider(self, mode: str) -> Optional[str]:
        vision_config = self._config.get("vision_models", {})
        if mode == "professional":
            return vision_config.get("professional_model", "")
        return vision_config.get("daily_model", "")

    def _build_system_prompt(self, mode: str, context_injection: str = "") -> str:
        if mode == "professional":
            base = (
                "你是一个专业的图片分析助手。"
                "请对复杂图片（如图表、技术图纸、学术问题等）提供详细、准确的分析。"
                "分析要全面且精确。"
            )
        else:
            base = (
                "你是一个友好的图片理解助手。"
                "请用自然、对话式的方式描述和分析图片。"
                "简洁但信息丰富。"
            )

        if context_injection:
            return base + "\n\n" + context_injection

        return base
