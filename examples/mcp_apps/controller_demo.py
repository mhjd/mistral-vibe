from __future__ import annotations

import asyncio
from pathlib import Path

from vibe.cli.mcp_apps import (
    MCPAppController,
    MCPAppOpenRequest,
    MCPAppResource,
    MCPAppToolDescriptor,
)
from vibe.cli.mcp_apps.models import UserMessageContext
from vibe.core.utils.io import read_safe_async

_RESOURCE_URI = "ui://job-application-studio/main"
_STUDIO_HTML = (
    Path(__file__).parent / "job_application_studio" / "ui" / "index.html"
)


async def _load_resource(server_name: str, resource_uri: str) -> MCPAppResource:
    html = await read_safe_async(_STUDIO_HTML, raise_on_error=True)
    print(f"Loaded {resource_uri} from fake server {server_name}")
    return MCPAppResource(
        uri=resource_uri,
        mime_type="text/html;profile=mcp-app",
        text=html.text,
    )


async def _call_tool(tool_name: str, arguments: dict[str, object]) -> object:
    print(f"Fake tool call: {tool_name} {arguments}")
    return {"ok": True, "tool_name": tool_name, "arguments": arguments}


async def _send_user_message(
    message: str, context: UserMessageContext
) -> object:
    print(f"Fake user message: {message}\nContext: {context}")
    return {"queued": True}


async def _run() -> None:
    controller = MCPAppController(
        resource_loader=_load_resource,
        call_tool=_call_tool,
        send_user_message=_send_user_message,
    )
    request = MCPAppOpenRequest(
        tool=MCPAppToolDescriptor(
            server_name="application-studio-demo",
            tool_name="application-studio-demo_open_application_studio",
            remote_tool_name="open_application_studio",
            resource_uri=_RESOURCE_URI,
        ),
        arguments={"demo": True},
        result={"title": "Application Studio", "ready": True},
    )
    try:
        active = await controller.open(request)
        print(f"Browser host opened at {active.host_session.url}")
        await asyncio.to_thread(input, "Press Enter to close the demo... ")
    finally:
        await controller.aclose()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
