from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from vibe.cli.mcp_apps.models import (
    CallTool,
    MCPAppInitialState,
    MCPAppResource,
    MCPAppSession,
    SendUserMessage,
)

type MCPAppResourceLoader = Callable[[str, str], Awaitable[MCPAppResource]]
type MCPAppErrorHandler = Callable[[str], Awaitable[None]]


class MCPAppHostPort(Protocol):
    async def start(self) -> MCPAppSession: ...

    async def stop(self) -> None: ...


class MCPAppHostFactory(Protocol):
    def __call__(
        self,
        *,
        app_html: str,
        initial_state: MCPAppInitialState,
        call_tool: CallTool,
        send_user_message: SendUserMessage,
    ) -> MCPAppHostPort: ...
