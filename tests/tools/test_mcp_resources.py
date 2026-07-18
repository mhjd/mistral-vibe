from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import contextlib
import os
from pathlib import Path
import re
import sys
from typing import Any
from unittest.mock import AsyncMock, patch

from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, ReadResourceResult
import pytest

from vibe.core.config import MCPStdio
from vibe.core.tools.base import BaseToolConfig, BaseToolState, InvokeContext
from vibe.core.tools.mcp import (
    MCPAppResource,
    MCPConnectionPool,
    MCPRegistry,
    MCPResourceContentError,
    MCPResourceEmptyError,
    MCPResourceNotFoundError,
    MCPResourceReadError,
    MCPResourceSessionError,
    MCPResourceURIError,
    MCPServerNotFoundError,
)
from vibe.core.tools.mcp.tools import _OpenArgs
from vibe.core.tools.remote import MCPTool

PROJECT_DIR = Path(__file__).parents[2]
STUDIO_URI = "ui://job-application-studio/main"


def _tool_result() -> Any:
    return {"structuredContent": {"ok": True}, "content": []}


def _text_result(
    text: str = "<html>Application Studio</html>",
    *,
    uri: str = STUDIO_URI,
    mime_type: str | None = "text/html;profile=mcp-app",
    metadata: dict[str, Any] | None = None,
    result_metadata: dict[str, Any] | None = None,
) -> ReadResourceResult:
    content: dict[str, Any] = {"uri": uri, "text": text}
    if mime_type is not None:
        content["mimeType"] = mime_type
    if metadata is not None:
        content["_meta"] = metadata
    payload: dict[str, Any] = {"contents": [content]}
    if result_metadata is not None:
        payload["_meta"] = result_metadata
    return ReadResourceResult.model_validate(payload)


class _FakeSession:
    def __init__(
        self,
        *,
        resource_result: ReadResourceResult | None = None,
        resource_error: Exception | None = None,
        read_side_effect: Any = None,
    ) -> None:
        self.call_tool = AsyncMock(return_value=_tool_result())
        if read_side_effect is not None:
            self.read_resource = AsyncMock(side_effect=read_side_effect)
        elif resource_error is not None:
            self.read_resource = AsyncMock(side_effect=resource_error)
        else:
            self.read_resource = AsyncMock(
                return_value=resource_result or _text_result()
            )


@contextlib.asynccontextmanager
async def _pool_with_session(
    session: _FakeSession,
) -> AsyncIterator[tuple[MCPConnectionPool, AsyncMock]]:
    enter = AsyncMock(return_value=session)
    pool = MCPConnectionPool()
    with patch("vibe.core.tools.mcp.pool.enter_stdio_session", enter):
        await pool.call_tool(
            command=["studio-server"],
            tool_name="open",
            arguments={},
            server_alias="studio",
        )
        try:
            yield pool, enter
        finally:
            await pool.aclose()


class TestMCPAppResource:
    @pytest.mark.asyncio
    async def test_preserves_uri_mime_text_and_metadata(self):
        session = _FakeSession(
            resource_result=_text_result(
                metadata={"content-id": "main"},
                result_metadata={"request-id": "read-1"},
            )
        )

        async with _pool_with_session(session) as (pool, enter):
            resource = await pool.read_resource(
                server_alias="studio", resource_uri=STUDIO_URI
            )

        assert isinstance(resource, MCPAppResource)
        assert resource.server_alias == "studio"
        assert str(resource.uri) == STUDIO_URI
        assert resource.mime_type == "text/html;profile=mcp-app"
        assert resource.text == "<html>Application Studio</html>"
        assert resource.metadata == {"request-id": "read-1"}
        assert resource.contents[0].metadata == {"content-id": "main"}
        assert enter.await_count == 1

    @pytest.mark.asyncio
    async def test_preserves_missing_mime_type(self):
        session = _FakeSession(resource_result=_text_result(mime_type=None))

        async with _pool_with_session(session) as (pool, _):
            resource = await pool.read_resource(
                server_alias="studio", resource_uri=STUDIO_URI
            )

        assert resource.mime_type is None
        assert resource.contents[0].mime_type is None

    @pytest.mark.asyncio
    async def test_combines_multiple_text_contents_without_losing_blocks(self):
        result = ReadResourceResult.model_validate({
            "contents": [
                {
                    "uri": STUDIO_URI,
                    "mimeType": "text/html",
                    "text": "<header>Studio</header>",
                    "_meta": {"part": 1},
                },
                {
                    "uri": "ui://job-application-studio/body",
                    "mimeType": "text/html;fragment=body",
                    "text": "<main>Applications</main>",
                    "_meta": {"part": 2},
                },
            ]
        })
        session = _FakeSession(resource_result=result)

        async with _pool_with_session(session) as (pool, _):
            resource = await pool.read_resource(
                server_alias="studio", resource_uri=STUDIO_URI
            )

        assert resource.text == ("<header>Studio</header>\n<main>Applications</main>")
        assert resource.mime_type is None
        assert [str(content.uri) for content in resource.contents] == [
            STUDIO_URI,
            "ui://job-application-studio/body",
        ]
        assert [content.mime_type for content in resource.contents] == [
            "text/html",
            "text/html;fragment=body",
        ]


class TestMCPResourceValidation:
    @pytest.mark.asyncio
    async def test_rejects_unknown_server_alias(self):
        pool = MCPConnectionPool()
        try:
            with pytest.raises(MCPServerNotFoundError, match="missing"):
                await pool.read_resource(
                    server_alias="missing", resource_uri=STUDIO_URI
                )
        finally:
            await pool.aclose()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("resource_uri", "message"),
        [
            (None, "required"),
            ("", "required"),
            ("not a uri", "Invalid MCP resource URI"),
        ],
    )
    async def test_rejects_missing_or_invalid_uri(
        self, resource_uri: str | None, message: str
    ):
        async with _pool_with_session(_FakeSession()) as (pool, _):
            with pytest.raises(MCPResourceURIError, match=message):
                await pool.read_resource(
                    server_alias="studio", resource_uri=resource_uri
                )

    @pytest.mark.asyncio
    async def test_rejects_non_ui_uri(self):
        async with _pool_with_session(_FakeSession()) as (pool, _):
            with pytest.raises(MCPResourceURIError, match="scheme: 'https'"):
                await pool.read_resource(
                    server_alias="studio", resource_uri="https://example.com/app"
                )

    @pytest.mark.asyncio
    async def test_rejects_empty_response(self):
        session = _FakeSession(
            resource_result=ReadResourceResult.model_validate({"contents": []})
        )

        async with _pool_with_session(session) as (pool, _):
            with pytest.raises(MCPResourceEmptyError, match="no contents"):
                await pool.read_resource(server_alias="studio", resource_uri=STUDIO_URI)

    @pytest.mark.asyncio
    async def test_rejects_empty_text(self):
        session = _FakeSession(resource_result=_text_result("  \n"))

        async with _pool_with_session(session) as (pool, _):
            with pytest.raises(MCPResourceEmptyError, match="empty text"):
                await pool.read_resource(server_alias="studio", resource_uri=STUDIO_URI)

    @pytest.mark.asyncio
    async def test_rejects_non_text_content(self):
        result = ReadResourceResult.model_validate({
            "contents": [
                {"uri": STUDIO_URI, "mimeType": "image/png", "blob": "aW1hZ2U="}
            ]
        })
        session = _FakeSession(resource_result=result)

        async with _pool_with_session(session) as (pool, _):
            with pytest.raises(MCPResourceContentError, match="non-text"):
                await pool.read_resource(server_alias="studio", resource_uri=STUDIO_URI)

    @pytest.mark.asyncio
    async def test_maps_unknown_resource_error(self):
        error = McpError(ErrorData(code=-32002, message="Unknown resource: ui://x"))
        session = _FakeSession(resource_error=error)

        async with _pool_with_session(session) as (pool, _):
            with pytest.raises(MCPResourceNotFoundError, match="Unknown MCP resource"):
                await pool.read_resource(server_alias="studio", resource_uri=STUDIO_URI)

    @pytest.mark.asyncio
    async def test_wraps_other_mcp_errors(self):
        error = McpError(ErrorData(code=-32603, message="read exploded"))
        session = _FakeSession(resource_error=error)

        async with _pool_with_session(session) as (pool, _):
            with pytest.raises(MCPResourceReadError, match="read exploded") as raised:
                await pool.read_resource(server_alias="studio", resource_uri=STUDIO_URI)

        assert raised.value.__cause__ is error

    @pytest.mark.asyncio
    async def test_rejects_reads_after_pool_close(self):
        pool = MCPConnectionPool()
        await pool.aclose()

        with pytest.raises(MCPResourceSessionError, match="closed"):
            await pool.read_resource(server_alias="studio", resource_uri=STUDIO_URI)

    @pytest.mark.asyncio
    async def test_rejects_unavailable_registered_session(self):
        async with _pool_with_session(_FakeSession()) as (pool, _):
            pool._conns.clear()
            with pytest.raises(MCPResourceSessionError, match="unavailable"):
                await pool.read_resource(server_alias="studio", resource_uri=STUDIO_URI)


class TestMCPResourceLifecycle:
    @pytest.mark.asyncio
    async def test_serializes_two_concurrent_reads(self):
        active = 0
        max_active = 0

        async def read_resource(_uri: Any) -> ReadResourceResult:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return _text_result()

        session = _FakeSession(read_side_effect=read_resource)
        async with _pool_with_session(session) as (pool, enter):
            first = asyncio.create_task(
                pool.read_resource(server_alias="studio", resource_uri=STUDIO_URI)
            )
            second = asyncio.create_task(
                pool.read_resource(server_alias="studio", resource_uri=STUDIO_URI)
            )
            resources = [await first, await second]

        assert max_active == 1
        assert len(resources) == 2
        assert enter.await_count == 1

    @pytest.mark.asyncio
    async def test_cancelling_read_keeps_session_available(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def read_resource(_uri: Any) -> ReadResourceResult:
            started.set()
            await release.wait()
            return _text_result()

        session = _FakeSession(read_side_effect=read_resource)
        async with _pool_with_session(session) as (pool, enter):
            task = asyncio.create_task(
                pool.read_resource(server_alias="studio", resource_uri=STUDIO_URI)
            )
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            release.set()
            resource = await pool.read_resource(
                server_alias="studio", resource_uri=STUDIO_URI
            )

        assert resource.text == "<html>Application Studio</html>"
        assert enter.await_count == 1


_STUDIO_WRAPPER = """
import os
from pathlib import Path
import sys

Path(sys.argv[1]).write_text(str(os.getpid()))

from examples.mcp_apps.job_application_studio.server import main

main()
"""


class TestApplicationStudioResourceIntegration:
    @pytest.mark.asyncio
    async def test_reads_real_studio_resource_from_discovered_server(
        self, tmp_path: Path
    ):
        wrapper = tmp_path / "studio_server.py"
        pid_file = tmp_path / "studio.pid"
        wrapper.write_text(_STUDIO_WRAPPER)
        server = MCPStdio(
            name="application_studio",
            transport="stdio",
            command=[sys.executable, str(wrapper), str(pid_file)],
            cwd=str(PROJECT_DIR),
            startup_timeout_sec=30,
            tool_timeout_sec=30,
        )
        registry = MCPRegistry()
        tools = await registry.get_tools_async([server])
        tool_class = tools["application_studio_open_application_studio"]
        assert issubclass(tool_class, MCPTool)
        assert tool_class.get_server_name() == server.name
        assert tool_class.get_ui_resource_uri() == STUDIO_URI

        pool = MCPConnectionPool()
        tool = tool_class(lambda: BaseToolConfig(), BaseToolState())
        try:
            results = [
                event
                async for event in tool.run(
                    _OpenArgs(), InvokeContext(tool_call_id="open", mcp_pool=pool)
                )
            ]
            with pytest.raises(MCPResourceNotFoundError):
                await pool.read_resource(
                    server_alias=server.name,
                    resource_uri="ui://job-application-studio/missing",
                )
            resource = await pool.read_resource(
                server_alias=server.name, resource_uri=STUDIO_URI
            )
            pid = int(pid_file.read_text())

            assert results
            assert resource.server_alias == server.name
            assert str(resource.uri) == STUDIO_URI
            assert resource.mime_type == "text/html;profile=mcp-app"
            assert resource.text.strip()
            assert "Application Studio" in resource.text
            assert len(pool._conns) == 1
        finally:
            await pool.aclose()

        assert pool._conns == {}
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail(f"Application Studio subprocess pid={pid} still active")

        assert re.fullmatch(r"\d+", str(pid))
