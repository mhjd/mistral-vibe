from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock

import pytest

from tests.conftest import build_test_agent_loop
from tests.stubs.fake_mcp_app import FakeMCPAppHostFactory
from vibe.cli.mcp_apps import MCPAppController, MCPAppOpenRequest, MCPAppToolDescriptor
from vibe.core.mcp_apps import build_mcp_app_callbacks
from vibe.core.tools.mcp import MCPAppResource, MCPAppResourceContent
from vibe.core.tools.mcp.pool import MCPConnectionPool

_RESOURCE_URI = "ui://studio/main"


@pytest.mark.asyncio
async def test_core_reader_and_callbacks_satisfy_controller_contracts() -> None:
    loop = build_test_agent_loop()
    content = MCPAppResourceContent.model_validate({
        "uri": _RESOURCE_URI,
        "mime_type": "text/html;profile=mcp-app",
        "text": "<main>Studio</main>",
    })
    resource = MCPAppResource.model_validate({
        "server_alias": "studio",
        "uri": _RESOURCE_URI,
        "mime_type": "text/html;profile=mcp-app",
        "text": "<main>Studio</main>",
        "contents": (content,),
    })
    pool = AsyncMock(spec=MCPConnectionPool)
    pool.read_resource.return_value = resource
    loop._mcp_pool = cast(MCPConnectionPool, pool)
    callbacks = build_mcp_app_callbacks(loop)
    factory = FakeMCPAppHostFactory()
    controller = MCPAppController(
        resource_loader=loop.read_mcp_app_resource,
        call_tool=callbacks.call_tool,
        send_user_message=callbacks.send_user_message,
        host_factory=factory,
    )

    active = await controller.open(
        MCPAppOpenRequest(
            tool=MCPAppToolDescriptor(
                server_name="studio",
                tool_name="studio_open",
                remote_tool_name="open",
                resource_uri=_RESOURCE_URI,
            ),
            arguments={"candidate": "Ada"},
            result={"status": "ready"},
        )
    )

    pool.read_resource.assert_awaited_once_with(
        server_alias="studio", resource_uri=_RESOURCE_URI
    )
    assert active.resource is resource
    assert factory.html_documents == ["<main>Studio</main>"]
    assert factory.initial_states[0].server_name == "studio"
    assert factory.initial_states[0].tool_arguments == {"candidate": "Ada"}
    assert factory.initial_states[0].tool_result == {"status": "ready"}
    assert factory.call_tool_callbacks == [callbacks.call_tool]
    assert factory.send_user_message_callbacks == [callbacks.send_user_message]

    await controller.aclose()
    await loop.aclose()
