from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import ClassVar, cast

from pydantic import BaseModel
import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_tool import FakeTool
from vibe.core.agent_loop import AgentLoop
from vibe.core.config import SessionLoggingConfig
from vibe.core.hooks.manager import HooksManager
from vibe.core.hooks.models import HookInvocation, HookRunStartEvent, HookType
from vibe.core.mcp_apps import (
    MCPAppAdapter,
    ToolCallStatus,
    UserMessageStatus,
    build_mcp_app_callbacks,
)
from vibe.core.session.session_loader import SessionLoader
from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
    ToolPermissionError,
)
from vibe.core.tools.remote import MCPToolResult
from vibe.core.types import (
    ApprovalResponse,
    BaseEvent,
    Role,
    ToolCallEvent,
    ToolResultEvent,
    ToolStreamEvent,
)


def _tool_loop(permission: ToolPermission = ToolPermission.ALWAYS) -> AgentLoop:
    loop = build_test_agent_loop(
        config=build_test_vibe_config(
            enabled_tools=["stub_tool"],
            tools={"stub_tool": {"permission": permission.value}},
        ),
        backend=FakeBackend(),
    )
    loop.tool_manager._all_tools["stub_tool"] = FakeTool
    return loop


async def _events(loop: AgentLoop, **arguments: object) -> list[BaseEvent]:
    return [event async for event in loop.execute_tool("stub_tool", arguments)]


@pytest.mark.asyncio
async def test_known_tool_validates_and_executes_without_orphan_history() -> None:
    loop = _tool_loop()

    events = await _events(loop, text="hello")

    assert [type(event) for event in events] == [ToolCallEvent, ToolResultEvent]
    call, result = events
    assert isinstance(call, ToolCallEvent)
    assert call.args is not None
    assert call.args.model_dump() == {"text": "hello"}
    assert isinstance(result, ToolResultEvent)
    assert result.result is not None
    assert result.result.model_dump() == {"message": "hello"}
    assert not any(message.role == Role.tool for message in loop.messages)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected"),
    [
        ("missing", {}, "Unknown tool"),
        ("stub_tool", {"text": {"nested": True}}, "Invalid arguments"),
    ],
)
async def test_unknown_tool_and_invalid_arguments_return_user_errors(
    tool_name: str, arguments: dict[str, object], expected: str
) -> None:
    adapter = MCPAppAdapter(_tool_loop())

    result = await adapter.call_tool(tool_name, arguments)

    assert result["status"] == ToolCallStatus.ERROR
    assert expected in cast(str, result["error"])


@pytest.mark.asyncio
async def test_permission_always_executes() -> None:
    result = await MCPAppAdapter(_tool_loop()).call_tool("stub_tool", {})

    assert result["status"] == ToolCallStatus.SUCCESS


@pytest.mark.asyncio
async def test_permission_ask_accepted_executes() -> None:
    loop = _tool_loop(ToolPermission.ASK)

    async def approve(*_args) -> tuple[ApprovalResponse, str | None]:
        return ApprovalResponse.YES, None

    loop.set_approval_callback(approve)

    result = await MCPAppAdapter(loop).call_tool("stub_tool", {})

    assert result["status"] == ToolCallStatus.SUCCESS


@pytest.mark.asyncio
async def test_permission_ask_refused_returns_permission_denied() -> None:
    loop = _tool_loop(ToolPermission.ASK)

    async def refuse(*_args) -> tuple[ApprovalResponse, str | None]:
        return ApprovalResponse.NO, "not from this app"

    loop.set_approval_callback(refuse)

    result = await MCPAppAdapter(loop).call_tool("stub_tool", {})

    assert result["status"] == ToolCallStatus.PERMISSION_DENIED
    assert result["error"] == "not from this app"


@pytest.mark.asyncio
async def test_permission_never_returns_permission_denied_without_callback() -> None:
    result = await MCPAppAdapter(_tool_loop(ToolPermission.NEVER)).call_tool(
        "stub_tool", {}
    )

    assert result["status"] == ToolCallStatus.PERMISSION_DENIED
    assert "permanently disabled" in cast(str, result["error"])


class _RecordingHooksManager:
    def __init__(self) -> None:
        self.invocations: list[HookType] = []

    def reset_retry_count(self) -> None:
        return

    async def run(self, invocation: HookInvocation) -> AsyncGenerator[object, None]:
        hook_type = HookType(invocation.hook_event_name)
        self.invocations.append(hook_type)
        yield HookRunStartEvent(scope=hook_type)


@pytest.mark.asyncio
async def test_hooks_run_and_hook_events_are_forwarded() -> None:
    loop = _tool_loop()
    hooks = _RecordingHooksManager()
    loop._hooks_manager = cast(HooksManager, hooks)

    events = await _events(loop)

    assert hooks.invocations == [HookType.PRE_TOOL, HookType.POST_TOOL]
    assert len([event for event in events if isinstance(event, HookRunStartEvent)]) == 2


class _StructuredArgs(BaseModel):
    pass


class _StructuredTool(
    BaseTool[_StructuredArgs, MCPToolResult, BaseToolConfig, BaseToolState]
):
    @classmethod
    def get_name(cls) -> str:
        return "structured"

    async def run(
        self, args: _StructuredArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | MCPToolResult, None]:
        assert ctx is not None
        yield ToolStreamEvent(
            tool_name=self.get_name(), tool_call_id=ctx.tool_call_id, message="halfway"
        )
        yield MCPToolResult(
            server="demo", tool="structured", text="readable", structured={"score": 9}
        )


@pytest.mark.asyncio
async def test_progressive_and_structured_result_is_json_serializable() -> None:
    loop = build_test_agent_loop(
        config=build_test_vibe_config(
            enabled_tools=["structured"], tools={"structured": {"permission": "always"}}
        )
    )
    loop.tool_manager._all_tools["structured"] = _StructuredTool
    observed: list[BaseEvent] = []

    async def observe(event: BaseEvent) -> None:
        observed.append(event)

    result = await MCPAppAdapter(loop, on_event=observe).call_tool("structured", {})

    assert result["status"] == ToolCallStatus.SUCCESS
    assert result["structured_content"] == {"score": 9}
    assert cast(dict, result["content"])["text"] == "readable"
    assert any(isinstance(event, ToolCallEvent) for event in observed)
    assert any(isinstance(event, ToolStreamEvent) for event in observed)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status"),
    [
        (ToolError("user-facing failure"), ToolCallStatus.ERROR),
        (
            ToolPermissionError("tool denied internally"),
            ToolCallStatus.PERMISSION_DENIED,
        ),
        (asyncio.CancelledError(), ToolCallStatus.CANCELLED),
    ],
)
async def test_tool_failures_and_cancellation_are_typed(
    error: BaseException, status: ToolCallStatus
) -> None:
    loop = _tool_loop()
    tool = loop.tool_manager.get("stub_tool")
    assert isinstance(tool, FakeTool)
    tool._exception_to_raise = error

    result = await MCPAppAdapter(loop).call_tool("stub_tool", {})

    assert result["status"] == status
    assert isinstance(result["error"], str)


class _BlockingTool(
    BaseTool[_StructuredArgs, MCPToolResult, BaseToolConfig, BaseToolState]
):
    started: ClassVar[list[asyncio.Event]] = []
    release: ClassVar[list[asyncio.Event]] = []
    order: ClassVar[list[int]] = []
    active: ClassVar[int] = 0
    max_active: ClassVar[int] = 0

    @classmethod
    def get_name(cls) -> str:
        return "blocking"

    async def run(
        self, args: _StructuredArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | MCPToolResult, None]:
        index = len(self.order)
        self.order.append(index)
        type(self).active += 1
        type(self).max_active = max(self.max_active, self.active)
        self.started[index].set()
        try:
            await self.release[index].wait()
            yield MCPToolResult(server="demo", tool="blocking", text=str(index))
        finally:
            type(self).active -= 1


@pytest.mark.asyncio
async def test_two_concurrent_ui_tool_requests_are_serialized() -> None:
    loop = build_test_agent_loop(
        config=build_test_vibe_config(
            enabled_tools=["blocking"], tools={"blocking": {"permission": "always"}}
        )
    )
    loop.tool_manager._all_tools["blocking"] = _BlockingTool
    _BlockingTool.started = [asyncio.Event(), asyncio.Event()]
    _BlockingTool.release = [asyncio.Event(), asyncio.Event()]
    _BlockingTool.order = []
    _BlockingTool.active = 0
    _BlockingTool.max_active = 0
    adapter = MCPAppAdapter(loop)

    first = asyncio.create_task(adapter.call_tool("blocking", {}))
    await _BlockingTool.started[0].wait()
    second = asyncio.create_task(adapter.call_tool("blocking", {}))
    await asyncio.sleep(0)

    assert _BlockingTool.order == [0]
    _BlockingTool.release[0].set()
    await _BlockingTool.started[1].wait()
    _BlockingTool.release[1].set()
    results = await asyncio.gather(first, second)

    assert [result["status"] for result in results] == [
        ToolCallStatus.SUCCESS,
        ToolCallStatus.SUCCESS,
    ]
    assert _BlockingTool.order == [0, 1]
    assert _BlockingTool.max_active == 1


class _BlockingBackend(FakeBackend):
    def __init__(self, calls: int) -> None:
        super().__init__()
        self.started = [asyncio.Event() for _ in range(calls)]
        self.release = [asyncio.Event() for _ in range(calls)]
        self.prompts: list[str] = []
        self.active = 0
        self.max_active = 0

    async def complete(self, **kwargs):
        index = len(self.prompts)
        messages = kwargs["messages"]
        prompt = next(
            message.content
            for message in reversed(messages)
            if message.role == Role.user and not message.injected
        )
        self.prompts.append(prompt)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started[index].set()
        try:
            await self.release[index].wait()
            return mock_llm_chunk(content=f"done {index}")
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_message_outside_active_turn_preserves_explicit_context() -> None:
    loop = build_test_agent_loop(backend=FakeBackend(mock_llm_chunk(content="done")))
    result = await MCPAppAdapter(loop).send_user_message(
        "Review this", {"candidate": "Ada", "paragraph": 2}
    )

    assert result["status"] == UserMessageStatus.COMPLETED
    assert result["queued"] is False
    message = next(message for message in loop.messages if message.role == Role.user)
    assert "Context supplied by mcp_app" in cast(str, message.content)
    assert '"candidate": "Ada"' in cast(str, message.content)
    assert message.user_display_content is not None
    assert message.user_display_content.content[0]["context"] == {
        "candidate": "Ada",
        "paragraph": 2,
    }


@pytest.mark.asyncio
async def test_message_context_round_trips_through_session_resume(
    tmp_path: Path,
) -> None:
    loop = build_test_agent_loop(
        config=build_test_vibe_config(
            session_logging=SessionLoggingConfig(
                enabled=True, save_dir=str(tmp_path), session_prefix="bridge"
            )
        ),
        backend=FakeBackend(mock_llm_chunk(content="done")),
    )

    await MCPAppAdapter(loop).send_user_message("Review", {"selection": "line 4"})

    assert loop.session_logger.session_dir is not None
    messages, _ = SessionLoader.load_session(loop.session_logger.session_dir)
    user_message = next(message for message in messages if message.role == Role.user)
    assert user_message.user_display_content is not None
    assert user_message.user_display_content.content[0]["context"] == {
        "selection": "line 4"
    }
    assert '"selection": "line 4"' in cast(str, user_message.content)


@pytest.mark.asyncio
async def test_messages_during_active_turn_are_fifo_and_never_overlap() -> None:
    backend = _BlockingBackend(3)
    loop = build_test_agent_loop(backend=backend)
    callbacks = build_mcp_app_callbacks(loop)

    first = asyncio.ensure_future(callbacks.send_user_message("first", {}))
    await backend.started[0].wait()
    second = asyncio.ensure_future(callbacks.send_user_message("second", {}))
    third = asyncio.ensure_future(callbacks.send_user_message("third", {}))
    await asyncio.sleep(0)

    assert backend.prompts == ["first"]
    backend.release[0].set()
    await backend.started[1].wait()
    backend.release[1].set()
    await backend.started[2].wait()
    backend.release[2].set()

    results = await asyncio.gather(first, second, third)
    assert backend.prompts == ["first", "second", "third"]
    assert backend.max_active == 1
    assert [result["queued"] for result in results] == [False, True, True]


@pytest.mark.asyncio
async def test_queued_message_runs_after_active_turn_cancellation() -> None:
    backend = _BlockingBackend(2)
    adapter = MCPAppAdapter(build_test_agent_loop(backend=backend))

    first = asyncio.create_task(adapter.send_user_message("cancel me", {}))
    await backend.started[0].wait()
    second = asyncio.create_task(adapter.send_user_message("continue", {}))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    await backend.started[1].wait()
    backend.release[1].set()

    result = await second
    assert result["status"] == UserMessageStatus.COMPLETED
    assert backend.prompts == ["cancel me", "continue"]
    assert backend.max_active == 1


@pytest.mark.asyncio
async def test_closed_session_rejects_new_messages_and_tools() -> None:
    loop = _tool_loop()
    adapter = MCPAppAdapter(loop)
    await loop.aclose()

    message = await adapter.send_user_message("too late", {})
    tool = await adapter.call_tool("stub_tool", {})

    assert message["status"] == UserMessageStatus.CLOSED
    assert "closed" in cast(str, message["error"])
    assert tool["status"] == ToolCallStatus.ERROR
    assert "closed" in cast(str, tool["error"])
