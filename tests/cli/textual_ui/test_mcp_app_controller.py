from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import build_test_vibe_app
from tests.stubs.fake_mcp_app import FakeMCPAppHostFactory, FakeMCPAppResourceLoader
from tests.stubs.fake_tool import FakeToolArgs, FakeToolResult
from vibe.cli.mcp_apps import MCPAppController, MCPAppResource
from vibe.core.tools.mcp.tools import create_mcp_stdio_proxy_tool_class
from vibe.core.tools.remote import RemoteTool
from vibe.core.types import BaseEvent, ToolCallEvent, ToolResultEvent

_RESOURCE_URI = "ui://studio/main"


def _controller(
    *, error: Exception | None = None
) -> tuple[MCPAppController, FakeMCPAppHostFactory]:
    loader = FakeMCPAppResourceLoader(
        MCPAppResource(
            uri=_RESOURCE_URI, mime_type="text/html", text="<main>Studio</main>"
        )
    )
    loader.error = error
    factory = FakeMCPAppHostFactory()
    return (
        MCPAppController(
            resource_loader=loader,
            call_tool=AsyncMock(return_value={}),
            send_user_message=AsyncMock(return_value=None),
            host_factory=factory,
        ),
        factory,
    )


def _tool_events() -> tuple[ToolCallEvent, ToolResultEvent]:
    remote = RemoteTool.model_validate({
        "name": "open",
        "_meta": {"ui": {"resourceUri": _RESOURCE_URI}},
    })
    tool_class = create_mcp_stdio_proxy_tool_class(
        command=["fake-server"], remote=remote, alias="studio"
    )
    return (
        ToolCallEvent(
            tool_call_id="call-1",
            tool_name=tool_class.get_name(),
            tool_class=tool_class,
            args=FakeToolArgs(text="open"),
        ),
        ToolResultEvent(
            tool_call_id="call-1",
            tool_name=tool_class.get_name(),
            tool_class=tool_class,
            result=FakeToolResult(message="ready"),
        ),
    )


async def _events(*events: BaseEvent) -> AsyncGenerator[BaseEvent]:
    for event in events:
        yield event


async def _wait_for_controller(controller: MCPAppController) -> None:
    async with asyncio.timeout(1):
        while controller.pending_task_count:
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_textual_event_stream_opens_injected_controller() -> None:
    controller, factory = _controller()
    app = build_test_vibe_app()
    app.set_mcp_app_controller(controller)
    call, result = _tool_events()

    async with app.run_test() as pilot:
        await pilot.pause()
        await app._handle_agent_loop_events(_events(call, result))
        await _wait_for_controller(controller)

    assert controller.active_session is not None
    assert factory.initial_states[0].tool_arguments == {"text": "open"}
    assert factory.initial_states[0].tool_result == {"message": "ready"}
    await controller.aclose()


@pytest.mark.asyncio
async def test_textual_controller_error_becomes_user_notification() -> None:
    controller, _ = _controller(error=RuntimeError("resource unavailable"))
    app = build_test_vibe_app()
    app.set_mcp_app_controller(controller)
    call, result = _tool_events()

    with patch.object(app, "notify", MagicMock()) as notify:
        async with app.run_test() as pilot:
            await pilot.pause()
            notify.reset_mock()
            await app._handle_agent_loop_events(_events(call, result))
            await _wait_for_controller(controller)

    notify.assert_called_once()
    assert "Could not open MCP App" in notify.call_args.args[0]
    assert notify.call_args.kwargs["severity"] == "error"
    await controller.aclose()


@pytest.mark.asyncio
async def test_textual_shutdown_closes_active_host() -> None:
    controller, factory = _controller()
    app = build_test_vibe_app()
    app.set_mcp_app_controller(controller)
    call, result = _tool_events()
    controller.observe_event(call)
    controller.observe_event(result)
    await _wait_for_controller(controller)

    await app.shutdown_cleanup()

    assert factory.hosts[0].stop_calls == 1
    assert controller.active_session is None
    assert controller.pending_task_count == 0
