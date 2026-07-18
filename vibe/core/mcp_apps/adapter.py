from __future__ import annotations

from contextlib import aclosing
from uuid import uuid4

from pydantic import JsonValue

from vibe.core.agent_loop import AgentLoop, AgentLoopStateError
from vibe.core.mcp_apps.models import (
    MCPAppCallbackResult,
    MCPAppCallbacks,
    MCPAppEventSink,
    ToolCallResult,
    ToolCallStatus,
    UserMessageResult,
    UserMessageStatus,
)
from vibe.core.tools.remote import MCPToolResult
from vibe.core.types import ToolResultEvent


class MCPAppAdapter:
    def __init__(
        self, agent_loop: AgentLoop, *, on_event: MCPAppEventSink | None = None
    ) -> None:
        self._agent_loop = agent_loop
        self._on_event = on_event

    @property
    def callbacks(self) -> MCPAppCallbacks:
        return MCPAppCallbacks(
            call_tool=self.call_tool, send_user_message=self.send_user_message
        )

    async def call_tool(
        self, tool_name: str, arguments: dict[str, object]
    ) -> MCPAppCallbackResult:
        terminal: ToolResultEvent | None = None
        try:
            async with aclosing(
                self._agent_loop.execute_tool(tool_name, arguments)
            ) as events:
                async for event in events:
                    if self._on_event is not None:
                        await self._on_event(event)
                    if isinstance(event, ToolResultEvent):
                        terminal = event
        except AgentLoopStateError as error:
            return ToolCallResult(
                status=ToolCallStatus.ERROR,
                tool_name=tool_name,
                tool_call_id="",
                error=str(error),
            ).model_dump(mode="json")

        if terminal is None:
            return ToolCallResult(
                status=ToolCallStatus.ERROR,
                tool_name=tool_name,
                tool_call_id="",
                error="Tool execution ended without a result",
            ).model_dump(mode="json")

        return self._tool_result(terminal).model_dump(mode="json")

    async def send_user_message(
        self, message: str, context: dict[str, JsonValue] | None = None
    ) -> MCPAppCallbackResult:
        queued = self._agent_loop.operation_active
        message_id = str(uuid4())
        try:
            async with aclosing(
                self._agent_loop.submit_user_message(
                    message,
                    context=context or None,
                    source="mcp_app",
                    client_message_id=message_id,
                )
            ) as events:
                async for event in events:
                    if self._on_event is not None:
                        await self._on_event(event)
        except AgentLoopStateError as error:
            return UserMessageResult(
                status=UserMessageStatus.CLOSED,
                message_id=message_id,
                queued=queued,
                error=str(error),
            ).model_dump(mode="json")

        return UserMessageResult(
            status=UserMessageStatus.COMPLETED, message_id=message_id, queued=queued
        ).model_dump(mode="json")

    @staticmethod
    def _tool_result(event: ToolResultEvent) -> ToolCallResult:
        status = ToolCallStatus.SUCCESS
        error = None
        if event.cancelled:
            status = ToolCallStatus.CANCELLED
            error = event.error or event.skip_reason
        elif event.permission_denied:
            status = ToolCallStatus.PERMISSION_DENIED
            error = event.skip_reason or event.error
        elif event.error is not None:
            status = ToolCallStatus.ERROR
            error = event.error

        content: JsonValue | None = None
        structured_content: JsonValue | None = None
        if event.result is not None:
            content = event.result.model_dump(mode="json", by_alias=True, fallback=str)
            if isinstance(event.result, MCPToolResult) and isinstance(content, dict):
                structured_content = content.get("structured")

        return ToolCallResult(
            status=status,
            tool_name=event.tool_name,
            tool_call_id=event.tool_call_id,
            content=content,
            structured_content=structured_content,
            error=error,
        )


def build_mcp_app_callbacks(
    agent_loop: AgentLoop, *, on_event: MCPAppEventSink | None = None
) -> MCPAppCallbacks:
    return MCPAppAdapter(agent_loop, on_event=on_event).callbacks
