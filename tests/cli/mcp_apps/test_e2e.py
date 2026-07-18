from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
from typing import cast

from anyio import Path as AsyncPath
from pydantic import BaseModel, JsonValue
import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_mcp_app import FakeMCPAppHostFactory
from vibe.cli.mcp_apps import MCPAppController
from vibe.cli.mcp_apps.models import ContextualSendUserMessage
from vibe.core.config import MCPStdio
from vibe.core.mcp_apps import ToolCallStatus, build_mcp_app_callbacks
from vibe.core.tools.mcp.registry import MCPRegistry
from vibe.core.tools.permissions import RequiredPermission
from vibe.core.types import ApprovalResponse, Role

_PROJECT_DIR = Path(__file__).parents[3]
_RESOURCE_URI = "ui://job-application-studio/main"
_SERVER_WRAPPER = """
import os
from pathlib import Path
import sys

Path(sys.argv[1]).write_text(str(os.getpid()))

from examples.mcp_apps.job_application_studio.server import main

main()
"""


async def _wait_for_controller(controller: MCPAppController) -> None:
    async with asyncio.timeout(5):
        while controller.pending_task_count:
            await asyncio.sleep(0)


async def _wait_for_process_exit(pid: int) -> None:
    async with asyncio.timeout(5):
        while True:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_application_studio_vertical_slice_with_real_stdio_server(
    tmp_path: Path,
) -> None:
    wrapper = tmp_path / "studio_server.py"
    pid_file = tmp_path / "studio.pid"
    await AsyncPath(wrapper).write_text(_SERVER_WRAPPER)
    alias = "application_studio"
    server = MCPStdio(
        name=alias,
        transport="stdio",
        command=[sys.executable, str(wrapper), str(pid_file)],
        cwd=str(_PROJECT_DIR),
        startup_timeout_sec=30,
        tool_timeout_sec=30,
    )
    backend = FakeBackend(mock_llm_chunk(content="Revision request received."))
    loop = build_test_agent_loop(
        config=build_test_vibe_config(
            mcp_servers=[server], enabled_tools=[f"{alias}_*"]
        ),
        backend=backend,
        mcp_registry=MCPRegistry(),
        defer_heavy_init=True,
    )
    approvals: list[str] = []

    async def approve(
        tool_name: str,
        _args: BaseModel,
        _call_id: str,
        _permissions: list[RequiredPermission] | None,
    ) -> tuple[ApprovalResponse, str | None]:
        approvals.append(tool_name)
        return ApprovalResponse.YES, None

    loop.set_approval_callback(approve)
    factory = FakeMCPAppHostFactory()
    callbacks = build_mcp_app_callbacks(loop)
    controller = MCPAppController(
        resource_loader=loop.read_mcp_app_resource,
        call_tool=callbacks.call_tool,
        send_user_message=callbacks.send_user_message,
        host_factory=factory,
    )
    pid = 0
    try:
        await loop.wait_until_ready()
        open_tool = f"{alias}_open_application_studio"
        async for event in loop.execute_tool(open_tool, {}):
            controller.observe_event(event)
        await _wait_for_controller(controller)

        active = controller.active_session
        assert active is not None
        assert active.resource.mime_type == "text/html;profile=mcp-app"
        assert str(active.resource.uri) == _RESOURCE_URI
        assert factory.html_documents[0].strip()
        assert "Application Studio" in factory.html_documents[0]
        state = factory.initial_states[0]
        assert state.server_name == alias
        assert state.tool_name == open_tool
        assert state.remote_tool_name == "open_application_studio"
        assert state.tool_arguments == {}
        initial_result = cast(dict[str, object], state.tool_result)
        assert initial_result["tool"] == "open_application_studio"

        tool_result = await factory.call_tool_callbacks[0](
            "get_application", {"application_id": "application-lattice"}
        )
        assert cast(dict[str, object], tool_result)["status"] == ToolCallStatus.SUCCESS
        structured = cast(dict[str, object], tool_result)["structured_content"]
        application = cast(dict[str, object], structured)["application"]
        assert cast(dict[str, object], application)["id"] == "application-lattice"
        assert approvals == [open_tool, f"{alias}_get_application"]

        context: dict[str, JsonValue] = {
            "application_id": "application-lattice",
            "document_type": "resume",
            "paragraph_id": "application-lattice-resume-1",
        }
        send_message = cast(
            ContextualSendUserMessage, factory.send_user_message_callbacks[0]
        )
        message_result = await send_message(
            "Revise the selected paragraph and improve the general rule.", context
        )
        assert cast(dict[str, object], message_result)["status"] == "completed"
        assert len(backend.requests_messages) == 1
        user_messages = [
            message for message in loop.messages if message.role == Role.user
        ]
        assert len(user_messages) == 1
        assert user_messages[0].user_display_content is not None
        assert user_messages[0].user_display_content.content[0]["context"] == context

        pid = int(await AsyncPath(pid_file).read_text())
    finally:
        await controller.aclose()
        await loop.aclose()

    assert factory.hosts[0].stop_calls == 1
    assert not factory.hosts[0].running
    await _wait_for_process_exit(pid)
