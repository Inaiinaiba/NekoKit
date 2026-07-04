import json
import re
from typing import Callable, Optional

from astrbot.api.event import AstrMessageEvent
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext

from .core import ToolResult
from .tools import FileStoreTool, KVStoreTool


class NekoKitCommandService:
    """Command-facing adapter for NekoKit tools."""

    HELP_TEXT = """NekoKit 人工命令
/nkit help
/nkit tools
/nkit kv list [prefix]
/nkit kv get <key>
/nkit kv set <key> <value>
/nkit kv delete <key>
/nkit file list [prefix]
/nkit file save <key> <mode:path|text|base64> <payload> [--days N]
/nkit file get_path <key>
/nkit file get_url <key>
/nkit file delete <key>

file save 示例：
/nkit file save report:weekly.md path ./weekly.md --days -1
/nkit file save note:summary.txt text 会议纪要...
/nkit file save image:raw.png base64 iVBORw0KGgo..."""

    def __init__(
        self,
        star_context,
        kv_tool: KVStoreTool,
        file_tool: FileStoreTool,
        llm_tools: list,
        prepare_file_source_kwarg: Callable,
    ) -> None:
        self._star_context = star_context
        self._kv_tool = kv_tool
        self._file_tool = file_tool
        self._llm_tools = llm_tools
        self._prepare_file_source_kwarg = prepare_file_source_kwarg

    def help_text(self) -> str:
        return self.HELP_TEXT

    def tools_text(self) -> str:
        lines = ["NekoKit LLM 工具"]
        for tool in self._llm_tools:
            lines.append(f"- {tool.name}: {tool.description}")
        return "\n".join(lines)

    async def run_kv(self, event: AstrMessageEvent, action: str, **kwargs) -> str:
        self._kv_tool.set_context(self._make_agent_context(event))
        result = await self._kv_tool.execute(action=action, **kwargs)
        if action == "get":
            return self._format_value_result(
                result, "value", "已获取", kwargs.get("key")
            )
        return self._format_result(result)

    async def run_kv_list(self, event: AstrMessageEvent, prefix: str = "") -> str:
        return await self.run_kv(event, "list", prefix=prefix)

    async def run_file(self, event: AstrMessageEvent, **kwargs) -> str:
        context = self._make_agent_context(event)
        self._file_tool.set_context(context)
        result = await self._file_tool.execute(**kwargs)
        action = kwargs.get("action")
        if action == "get_path":
            return self._format_value_result(
                result, "path", "已获取文件路径", kwargs.get("key")
            )
        if action == "get_url":
            return self._format_value_result(
                result, "url", "已获取文件 URL", kwargs.get("key")
            )
        return self._format_result(result)

    async def run_file_save(
        self, event: AstrMessageEvent, key: str, mode: str, payload: str
    ) -> str:
        payload, retention_days, err = self._extract_retention_days(payload)
        if err:
            return err

        mode = mode.lower().strip()
        kwargs = {"action": "save", "key": key, "retention_days": retention_days}
        if mode in {"path", "source", "file"}:
            context = self._make_agent_context(event)
            kwargs["source_path"] = payload
            err = await self._prepare_file_source_kwarg(context, kwargs)
            if err:
                return err
            self._file_tool.set_context(context)
            result = await self._file_tool.execute(**kwargs)
            return self._format_result(result)
        if mode in {"text", "content"}:
            kwargs["content"] = payload
            return await self.run_file(event, **kwargs)
        if mode in {"base64", "b64"}:
            kwargs["content_base64"] = payload.strip()
            return await self.run_file(event, **kwargs)
        return "mode 只支持 path、text、base64"

    def _make_agent_context(
        self, event: AstrMessageEvent
    ) -> ContextWrapper[AstrAgentContext]:
        return ContextWrapper(AstrAgentContext(context=self._star_context, event=event))

    @staticmethod
    def _extract_retention_days(payload: str) -> tuple[str, int, Optional[str]]:
        text = str(payload).strip()
        if not text:
            return "", 7, "payload 不能为空"

        match = re.search(r"\s+--(?:days|retention-days)(?:=|\s+)(-?\d+)\s*$", text)
        if not match:
            return text, 7, None

        try:
            retention_days = int(match.group(1))
        except ValueError:
            return text, 7, "retention days 必须是整数"
        return text[: match.start()].rstrip(), retention_days, None

    @staticmethod
    def _format_result(result: ToolResult) -> str:
        message = NekoKitCommandService._clean_command_message(result.message)
        if result.data is None:
            return message
        return (
            message
            + "\n"
            + json.dumps(result.data, ensure_ascii=False, indent=2)
        )

    @staticmethod
    def _format_value_result(
        result: ToolResult, field: str, notice: str, key: Optional[str] = None
    ) -> str:
        if not result.success:
            return NekoKitCommandService._clean_command_message(result.message)
        if not isinstance(result.data, dict) or field not in result.data:
            return NekoKitCommandService._format_result(result)
        label = f"{notice} {key}" if key else notice
        return f"{label}\n{result.data[field]}"

    @staticmethod
    def _clean_command_message(message: str) -> str:
        text = str(message)
        replacements = {
            "喵~": "",
            "喵": "",
            "😸": "",
            "😺": "",
            "😿": "",
            "📦": "",
            "📋": "",
            "🗑️": "",
            "🔍": "",
            "✨": "",
            "✅": "",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        return re.sub(r"\s+", " ", text).strip()
