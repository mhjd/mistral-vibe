from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_app
from tests.stubs.fake_mcp_app import FakeMCPAppHostFactory, FakeMCPAppResourceLoader
from tests.stubs.fake_tool import FakeTool, FakeToolArgs, FakeToolResult
from vibe.cli.mcp_apps import MCPAppController, MCPAppResource
from vibe.cli.mcp_apps.models import ContextualSendUserMessage
from vibe.cli.textual_ui.app import _build_mcp_app_controller
from vibe.core.tools.mcp.tools import create_mcp_stdio_proxy_tool_class
from vibe.core.tools.remote import RemoteTool
from vibe.core.types import BaseEvent, ToolCallEvent, ToolResultEvent, UserMessageEvent

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
async def test_default_controller_uses_app_event_sink_and_existing_loop() -> None:
    loop = build_test_agent_loop()
    app = build_test_vibe_app(agent_loop=loop)
    controller = _build_mcp_app_controller(loop, app)
    app.set_mcp_app_controller(controller)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.event_handler is not None
        with patch.object(
            app.event_handler,
            "handle_event",
            AsyncMock(wraps=app.event_handler.handle_event),
        ) as handle_event:
            send_message = cast(
                ContextualSendUserMessage, controller._send_user_message
            )
            result = await send_message(
                "Review this paragraph", {"paragraph_id": "paragraph-1"}
            )
        await pilot.pause()

    assert cast(dict[str, object], result)["status"] == "completed"
    assert any(message.role == "user" for message in loop.messages)
    user_events = [
        call.args[0]
        for call in handle_event.await_args_list
        if isinstance(call.args[0], UserMessageEvent)
    ]
    assert len(user_events) == 1
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


@pytest.mark.asyncio
async def test_textual_stays_responsive_and_ignores_ordinary_tool() -> None:
    loader = FakeMCPAppResourceLoader(
        MCPAppResource(
            uri=_RESOURCE_URI, mime_type="text/html", text="<main>Studio</main>"
        )
    )
    loader.wait_until = asyncio.Event()
    factory = FakeMCPAppHostFactory()
    controller = MCPAppController(
        resource_loader=loader,
        call_tool=AsyncMock(return_value={}),
        send_user_message=AsyncMock(return_value=None),
        host_factory=factory,
    )
    app = build_test_vibe_app()
    app.set_mcp_app_controller(controller)
    ordinary_call = ToolCallEvent(
        tool_call_id="ordinary",
        tool_name="stub_tool",
        tool_class=FakeTool,
        args=FakeToolArgs(text="ordinary"),
    )
    ordinary_result = ToolResultEvent(
        tool_call_id="ordinary",
        tool_name="stub_tool",
        tool_class=FakeTool,
        result=FakeToolResult(),
    )
    call, result = _tool_events()

    async with app.run_test() as pilot:
        await pilot.pause()
        await app._handle_agent_loop_events(_events(ordinary_call, ordinary_result))
        assert loader.calls == []
        await app._handle_agent_loop_events(_events(call, result))
        responsive = asyncio.Event()
        asyncio.get_running_loop().call_soon(responsive.set)
        await asyncio.wait_for(responsive.wait(), timeout=0.1)
        assert controller.pending_task_count == 1
        loader.wait_until.set()
        await _wait_for_controller(controller)

    assert controller.active_session is not None
    await controller.aclose()
