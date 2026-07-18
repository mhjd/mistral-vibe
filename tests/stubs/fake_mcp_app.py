from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from vibe.cli.mcp_apps.models import (
    CallTool,
    MCPAppInitialState,
    MCPAppResource,
    MCPAppSession,
    SendUserMessage,
)


class FakeMCPAppResourceLoader:
    def __init__(self, resource: MCPAppResource) -> None:
        self.resource = resource
        self.error: Exception | None = None
        self.wait_until: asyncio.Event | None = None
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, server_name: str, resource_uri: str) -> MCPAppResource:
        self.calls.append((server_name, resource_uri))
        if self.wait_until is not None:
            await self.wait_until.wait()
        if self.error is not None:
            raise self.error
        return self.resource.model_copy(update={"uri": resource_uri})


class FakeMCPAppHost:
    def __init__(self, *, port: int = 8765) -> None:
        self.session = MCPAppSession(host="127.0.0.1", port=port, token="test-token")
        self.start_error: Exception | None = None
        self.wait_until_started: asyncio.Event | None = None
        self.start_calls = 0
        self.stop_calls = 0
        self.running = False

    async def start(self) -> MCPAppSession:
        self.start_calls += 1
        if self.wait_until_started is not None:
            await self.wait_until_started.wait()
        if self.start_error is not None:
            raise self.start_error
        self.running = True
        return self.session

    async def stop(self) -> None:
        self.stop_calls += 1
        self.running = False


class FakeMCPAppHostFactory:
    def __init__(self) -> None:
        self.hosts: list[FakeMCPAppHost] = []
        self.initial_states: list[MCPAppInitialState] = []
        self.html_documents: list[str] = []
        self.call_tool_callbacks: list[CallTool] = []
        self.send_user_message_callbacks: list[SendUserMessage] = []
        self.host_builder: Callable[[], FakeMCPAppHost] = FakeMCPAppHost

    def __call__(
        self,
        *,
        app_html: str,
        initial_state: MCPAppInitialState,
        call_tool: Callable[[str, dict[str, object]], Awaitable[object]],
        send_user_message: SendUserMessage,
    ) -> FakeMCPAppHost:
        host = self.host_builder()
        self.hosts.append(host)
        self.initial_states.append(initial_state)
        self.html_documents.append(app_html)
        self.call_tool_callbacks.append(call_tool)
        self.send_user_message_callbacks.append(send_user_message)
        return host
