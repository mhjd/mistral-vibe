from __future__ import annotations

from urllib.parse import urlsplit

from vibe.cli.mcp_apps.models import MCPAppOpenRequest, MCPAppToolDescriptor
from vibe.core.tools.remote import MCPTool
from vibe.core.types import BaseEvent, ToolCallEvent, ToolResultEvent


class MCPAppToolEventCorrelator:
    def __init__(self) -> None:
        self._pending: dict[str, MCPAppOpenRequest] = {}

    def handle(self, event: BaseEvent) -> MCPAppOpenRequest | None:
        match event:
            case ToolCallEvent(args=args) if args is not None:
                request = self._request_from_call(event)
                if request is not None:
                    self._pending[event.tool_call_id] = request
                return None
            case ToolResultEvent():
                request = self._pending.pop(event.tool_call_id, None)
                if request is None or self._is_failed_result(event):
                    return None
                return MCPAppOpenRequest(
                    tool=request.tool, arguments=request.arguments, result=event.result
                )
            case _:
                return None

    def clear(self) -> None:
        self._pending.clear()

    @staticmethod
    def _request_from_call(event: ToolCallEvent) -> MCPAppOpenRequest | None:
        tool_class = event.tool_class
        if not issubclass(tool_class, MCPTool):
            return None
        resource_uri = tool_class.get_ui_resource_uri()
        server_name = tool_class.get_server_name()
        if (
            resource_uri is None
            or urlsplit(resource_uri).scheme != "ui"
            or server_name is None
            or event.args is None
        ):
            return None
        return MCPAppOpenRequest(
            tool=MCPAppToolDescriptor(
                server_name=server_name,
                tool_name=event.tool_name,
                remote_tool_name=tool_class.get_remote_name(),
                resource_uri=resource_uri,
            ),
            arguments=event.args.model_dump(mode="python"),
            result=None,
        )

    @staticmethod
    def _is_failed_result(event: ToolResultEvent) -> bool:
        return event.error is not None or event.skipped or event.cancelled
