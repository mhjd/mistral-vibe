from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import cast
from unittest.mock import AsyncMock

import pytest

from tests.stubs.fake_mcp_app import (
    FakeMCPAppHost,
    FakeMCPAppHostFactory,
    FakeMCPAppResourceLoader,
)
from tests.stubs.fake_tool import FakeTool, FakeToolArgs, FakeToolResult
from vibe.cli.mcp_apps import (
    MCPAppController,
    MCPAppControllerError,
    MCPAppHostStartError,
    MCPAppOpenRequest,
    MCPAppResource,
    MCPAppResourceError,
    MCPAppToolDescriptor,
)
from vibe.cli.mcp_apps.models import ContextualSendUserMessage
from vibe.core.tools.mcp.tools import create_mcp_stdio_proxy_tool_class
from vibe.core.tools.remote import RemoteTool
from vibe.core.types import ToolCallEvent, ToolResultEvent

_RESOURCE_URI = "ui://studio/main"


@dataclass
class _UnknownResult:
    value: str


def _resource() -> MCPAppResource:
    return MCPAppResource(
        uri=_RESOURCE_URI, mime_type="text/html; charset=utf-8", text="<main>App</main>"
    )


def _request(
    *,
    resource_uri: str = _RESOURCE_URI,
    arguments: dict[str, object] | None = None,
    result: object | None = None,
) -> MCPAppOpenRequest:
    return MCPAppOpenRequest(
        tool=MCPAppToolDescriptor(
            server_name="studio",
            tool_name="studio_open",
            remote_tool_name="open",
            resource_uri=resource_uri,
        ),
        arguments=arguments or {"candidate": "Ada"},
        result=result if result is not None else {"status": "ready"},
    )


def _controller(
    loader: FakeMCPAppResourceLoader,
    factory: FakeMCPAppHostFactory,
    *,
    call_tool: AsyncMock | None = None,
    send_user_message: AsyncMock | None = None,
    error_handler: AsyncMock | None = None,
) -> MCPAppController:
    return MCPAppController(
        resource_loader=loader,
        call_tool=call_tool or AsyncMock(return_value={"ok": True}),
        send_user_message=send_user_message or AsyncMock(return_value={"queued": True}),
        host_factory=factory,
        error_handler=error_handler,
    )


def _mcp_tool_class(*, resource_uri: str | None = _RESOURCE_URI, alias: str = "studio"):
    metadata = {"ui": {"resourceUri": resource_uri}} if resource_uri else None
    remote = RemoteTool.model_validate({"name": "open", "_meta": metadata})
    return create_mcp_stdio_proxy_tool_class(
        command=["fake-mcp-server"], remote=remote, alias=alias
    )


async def _wait_for_observed_open(controller: MCPAppController) -> None:
    async with asyncio.timeout(1):
        while controller.pending_task_count:
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_open_loads_resource_and_builds_initial_state() -> None:
    loader = FakeMCPAppResourceLoader(_resource())
    factory = FakeMCPAppHostFactory()
    controller = _controller(loader, factory)
    request = _request(
        arguments={"candidate": "Ada", "rank": 1},
        result=FakeToolResult(message="accepted"),
    )

    active = await controller.open(request)

    assert loader.calls == [("studio", _RESOURCE_URI)]
    assert factory.html_documents == ["<main>App</main>"]
    assert factory.initial_states[0].model_dump() == {
        "server_name": "studio",
        "tool_name": "studio_open",
        "remote_tool_name": "open",
        "resource_uri": _RESOURCE_URI,
        "tool_arguments": {"candidate": "Ada", "rank": 1},
        "tool_result": {"message": "accepted"},
    }
    assert active.request is request
    assert active.host is factory.hosts[0]
    assert controller.active_session is active

    await controller.aclose()


@pytest.mark.asyncio
async def test_host_receives_callbacks_with_name_alias_and_message_context() -> None:
    loader = FakeMCPAppResourceLoader(_resource())
    factory = FakeMCPAppHostFactory()
    call_tool = AsyncMock(return_value={"echo": True})
    send_user_message = AsyncMock(return_value={"queued": True})
    controller = _controller(
        loader, factory, call_tool=call_tool, send_user_message=send_user_message
    )
    await controller.open(_request())

    result = await factory.call_tool_callbacks[0]("name_alias", {"value": 3})
    context = {"source": "studio", "selection": ["paragraph-2"]}
    send_callback = cast(
        ContextualSendUserMessage, factory.send_user_message_callbacks[0]
    )
    message_result = await send_callback("Review this selection", context)

    assert result == {"echo": True}
    assert message_result == {"queued": True}
    call_tool.assert_awaited_once_with("studio_name_alias", {"value": 3})
    send_user_message.assert_awaited_once_with("Review this selection", context)

    await controller.aclose()


@pytest.mark.asyncio
async def test_replacing_active_session_starts_new_host_then_stops_previous() -> None:
    loader = FakeMCPAppResourceLoader(_resource())
    factory = FakeMCPAppHostFactory()
    next_port = iter((8765, 8766))
    factory.host_builder = lambda: FakeMCPAppHost(port=next(next_port))
    controller = _controller(loader, factory)

    first = await controller.open(_request(arguments={"candidate": "Ada"}))
    second = await controller.open(_request(arguments={"candidate": "Grace"}))

    assert first.host_session.port == 8765
    assert second.host_session.port == 8766
    assert factory.hosts[0].stop_calls == 1
    assert not factory.hosts[0].running
    assert factory.hosts[1].running
    assert controller.active_session is second

    await controller.aclose()


@pytest.mark.asyncio
async def test_close_and_shutdown_are_idempotent() -> None:
    loader = FakeMCPAppResourceLoader(_resource())
    factory = FakeMCPAppHostFactory()
    controller = _controller(loader, factory)
    await controller.open(_request())

    await controller.close()
    await controller.close()
    await controller.aclose()
    await controller.aclose()

    assert factory.hosts[0].stop_calls == 1
    assert controller.active_session is None
    assert controller.pending_task_count == 0
    with pytest.raises(MCPAppControllerError, match="closed"):
        await controller.open(_request())


@pytest.mark.asyncio
async def test_loader_error_keeps_existing_session_active() -> None:
    loader = FakeMCPAppResourceLoader(_resource())
    factory = FakeMCPAppHostFactory()
    controller = _controller(loader, factory)
    active = await controller.open(_request())
    loader.error = RuntimeError("read_resource failed")

    with pytest.raises(MCPAppResourceError, match="Failed to load"):
        await controller.open(_request())

    assert controller.active_session is active
    assert factory.hosts[0].running
    await controller.aclose()


@pytest.mark.asyncio
async def test_invalid_resource_is_rejected_before_host_creation() -> None:
    loader = FakeMCPAppResourceLoader(
        MCPAppResource(uri=_RESOURCE_URI, mime_type="application/json", text="{}")
    )
    factory = FakeMCPAppHostFactory()
    controller = _controller(loader, factory)

    with pytest.raises(MCPAppResourceError, match="HTML text"):
        await controller.open(_request())

    assert factory.hosts == []


@pytest.mark.asyncio
async def test_host_start_error_stops_partial_host_and_keeps_existing_session() -> None:
    loader = FakeMCPAppResourceLoader(_resource())
    factory = FakeMCPAppHostFactory()
    hosts = iter((FakeMCPAppHost(port=8765), FakeMCPAppHost(port=8766)))
    factory.host_builder = lambda: next(hosts)
    controller = _controller(loader, factory)
    active = await controller.open(_request())
    factory.hosts[0].running = True

    failing_host = FakeMCPAppHost(port=8767)
    failing_host.start_error = RuntimeError("bind failed")
    factory.host_builder = lambda: failing_host

    with pytest.raises(MCPAppHostStartError, match="Failed to start"):
        await controller.open(_request())

    assert failing_host.stop_calls == 1
    assert controller.active_session is active
    assert factory.hosts[0].running
    await controller.aclose()


@pytest.mark.asyncio
async def test_cancellation_during_load_has_no_orphan_task() -> None:
    loader = FakeMCPAppResourceLoader(_resource())
    loader.wait_until = asyncio.Event()
    factory = FakeMCPAppHostFactory()
    controller = _controller(loader, factory)
    tool_class = _mcp_tool_class()

    controller.observe_event(
        ToolCallEvent(
            tool_call_id="call-1",
            tool_name=tool_class.get_name(),
            tool_class=tool_class,
            args=FakeToolArgs(text="open"),
        )
    )
    scheduled = controller.observe_event(
        ToolResultEvent(
            tool_call_id="call-1",
            tool_name=tool_class.get_name(),
            tool_class=tool_class,
            result=FakeToolResult(),
        )
    )

    assert scheduled
    await asyncio.sleep(0)
    assert controller.pending_task_count == 1
    await controller.close()

    assert controller.pending_task_count == 0
    assert controller.active_session is None
    assert factory.hosts == []


@pytest.mark.asyncio
async def test_ordinary_tool_events_are_ignored() -> None:
    loader = FakeMCPAppResourceLoader(_resource())
    factory = FakeMCPAppHostFactory()
    controller = _controller(loader, factory)

    controller.observe_event(
        ToolCallEvent(
            tool_call_id="ordinary",
            tool_name="stub_tool",
            tool_class=FakeTool,
            args=FakeToolArgs(text="hello"),
        )
    )
    scheduled = controller.observe_event(
        ToolResultEvent(
            tool_call_id="ordinary",
            tool_name="stub_tool",
            tool_class=FakeTool,
            result=FakeToolResult(),
        )
    )

    assert not scheduled
    assert loader.calls == []
    assert factory.hosts == []


@pytest.mark.asyncio
async def test_mcp_tool_result_is_correlated_by_id_and_opened_non_blockingly() -> None:
    loader = FakeMCPAppResourceLoader(_resource())
    loader.wait_until = asyncio.Event()
    factory = FakeMCPAppHostFactory()
    controller = _controller(loader, factory)
    tool_class = _mcp_tool_class(alias="work")

    controller.observe_event(
        ToolCallEvent(
            tool_call_id="app-call",
            tool_name=tool_class.get_name(),
            tool_class=tool_class,
            args=FakeToolArgs(text="candidate"),
        )
    )
    scheduled = controller.observe_event(
        ToolResultEvent(
            tool_call_id="app-call",
            tool_name=tool_class.get_name(),
            tool_class=tool_class,
            result=FakeToolResult(message="ready"),
        )
    )

    assert scheduled
    assert controller.active_session is None
    loader.wait_until.set()
    await _wait_for_observed_open(controller)

    active = controller.active_session
    assert active is not None
    assert active.request.tool.server_name == "work"
    assert active.request.tool.tool_name == "work_open"
    assert active.request.tool.remote_tool_name == "open"
    assert active.request.arguments == {"text": "candidate"}
    assert factory.initial_states[0].tool_result == {"message": "ready"}
    await controller.aclose()


@pytest.mark.asyncio
async def test_rapid_successive_opens_cancel_partial_host_and_keep_latest() -> None:
    loader = FakeMCPAppResourceLoader(_resource())
    factory = FakeMCPAppHostFactory()
    first_host = FakeMCPAppHost(port=8765)
    first_host.wait_until_started = asyncio.Event()
    second_host = FakeMCPAppHost(port=8766)
    hosts = iter((first_host, second_host))
    factory.host_builder = lambda: next(hosts)
    controller = _controller(loader, factory)
    tool_class = _mcp_tool_class()

    for call_id, message in (("first", "old"), ("second", "latest")):
        controller.observe_event(
            ToolCallEvent(
                tool_call_id=call_id,
                tool_name=tool_class.get_name(),
                tool_class=tool_class,
                args=FakeToolArgs(text=message),
            )
        )
        controller.observe_event(
            ToolResultEvent(
                tool_call_id=call_id,
                tool_name=tool_class.get_name(),
                tool_class=tool_class,
                result=FakeToolResult(message=message),
            )
        )
        if call_id == "first":
            async with asyncio.timeout(1):
                while not factory.hosts:
                    await asyncio.sleep(0)

    await _wait_for_observed_open(controller)

    assert first_host.stop_calls == 1
    assert not first_host.running
    assert second_host.running
    assert controller.active_session is not None
    assert controller.active_session.host_session.port == 8766
    assert controller.active_session.request.arguments == {"text": "latest"}
    assert controller.pending_task_count == 0
    await controller.aclose()


@pytest.mark.asyncio
async def test_failed_tool_result_does_not_open_app() -> None:
    loader = FakeMCPAppResourceLoader(_resource())
    factory = FakeMCPAppHostFactory()
    controller = _controller(loader, factory)
    tool_class = _mcp_tool_class()
    controller.observe_event(
        ToolCallEvent(
            tool_call_id="failed",
            tool_name=tool_class.get_name(),
            tool_class=tool_class,
            args=FakeToolArgs(),
        )
    )

    scheduled = controller.observe_event(
        ToolResultEvent(
            tool_call_id="failed",
            tool_name=tool_class.get_name(),
            tool_class=tool_class,
            error="tool failed",
        )
    )

    assert not scheduled
    assert loader.calls == []


@pytest.mark.asyncio
async def test_non_ui_resource_uri_does_not_schedule_open() -> None:
    loader = FakeMCPAppResourceLoader(_resource())
    factory = FakeMCPAppHostFactory()
    controller = _controller(loader, factory)
    tool_class = _mcp_tool_class(resource_uri="https://example.test/app")
    controller.observe_event(
        ToolCallEvent(
            tool_call_id="invalid-uri",
            tool_name=tool_class.get_name(),
            tool_class=tool_class,
            args=FakeToolArgs(),
        )
    )

    scheduled = controller.observe_event(
        ToolResultEvent(
            tool_call_id="invalid-uri",
            tool_name=tool_class.get_name(),
            tool_class=tool_class,
            result=FakeToolResult(),
        )
    )

    assert not scheduled
    assert loader.calls == []


@pytest.mark.asyncio
async def test_observed_error_is_reported_without_leaking_task_exception() -> None:
    loader = FakeMCPAppResourceLoader(_resource())
    loader.error = RuntimeError("unavailable")
    factory = FakeMCPAppHostFactory()
    error_handler = AsyncMock()
    controller = _controller(loader, factory, error_handler=error_handler)
    tool_class = _mcp_tool_class()
    controller.observe_event(
        ToolCallEvent(
            tool_call_id="error",
            tool_name=tool_class.get_name(),
            tool_class=tool_class,
            args=FakeToolArgs(),
        )
    )
    controller.observe_event(
        ToolResultEvent(
            tool_call_id="error",
            tool_name=tool_class.get_name(),
            tool_class=tool_class,
            result=FakeToolResult(),
        )
    )

    await _wait_for_observed_open(controller)

    assert controller.last_error is not None
    assert "Failed to load" in controller.last_error
    error_handler.assert_awaited_once()
    assert controller.pending_task_count == 0


@pytest.mark.asyncio
async def test_non_json_result_is_converted_to_text() -> None:
    loader = FakeMCPAppResourceLoader(_resource())
    factory = FakeMCPAppHostFactory()
    controller = _controller(loader, factory)

    await controller.open(_request(result={"custom": _UnknownResult("value")}))

    assert factory.initial_states[0].tool_result == {
        "custom": "_UnknownResult(value='value')"
    }
    await controller.aclose()
