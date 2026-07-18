from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlparse

import httpx
import pytest

from vibe.cli.mcp_apps import MCPAppHost, MCPAppInitialState, load_test_app_html


def _make_host(call_tool: AsyncMock, send_user_message: AsyncMock) -> MCPAppHost:
    return MCPAppHost(
        app_html=load_test_app_html(),
        initial_state=MCPAppInitialState(
            tool_arguments={"query": "initial"}, tool_result={"items": ["first"]}
        ),
        call_tool=call_tool,
        send_user_message=send_user_message,
    )


@pytest.mark.asyncio
async def test_start_serves_host_page_and_opens_browser(monkeypatch):
    browser_open = MagicMock(return_value=True)
    monkeypatch.setattr("vibe.cli.mcp_apps._host.webbrowser.open", browser_open)
    host = _make_host(AsyncMock(return_value={}), AsyncMock(return_value=None))

    try:
        session = await host.start()

        assert host.is_running
        assert session.host == "127.0.0.1"
        assert session.port > 0
        assert len(session.token) >= 32
        assert urlparse(session.url).port == session.port
        browser_open.assert_called_once_with(session.url)

        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(session.url)
            forbidden = await client.get(session.origin)

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert (
            '<iframe id="app" title="MCP App" sandbox="allow-scripts">' in response.text
        )
        assert "MCP App host test" in response.text
        assert 'id=\\"call-tool\\"' in response.text
        assert 'id=\\"send-message\\"' in response.text
        assert 'id=\\"responses\\"' in response.text
        assert '"tool_arguments":{"query":"initial"}' in response.text
        assert '"tool_result":{"items":["first"]}' in response.text
        assert 'postMessage({type: "initial_state"' in response.text
        assert "showError(payload.error)" in response.text
        assert forbidden.status_code == 403
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_call_tool_with_tool_name_returns_result(monkeypatch):
    monkeypatch.setattr(
        "vibe.cli.mcp_apps._host.webbrowser.open", MagicMock(return_value=True)
    )
    call_tool = AsyncMock(return_value={"echo": "hello"})
    host = _make_host(call_tool, AsyncMock(return_value=None))

    try:
        session = await host.start()
        async with httpx.AsyncClient(
            base_url=session.origin, trust_env=False
        ) as client:
            response = await client.post(
                "/api/message",
                json={
                    "type": "call_tool",
                    "token": session.token,
                    "request_id": "tool-1",
                    "tool_name": "echo",
                    "arguments": {"text": "hello"},
                },
            )

        assert response.status_code == 200
        assert response.json() == {
            "type": "tool_result",
            "request_id": "tool-1",
            "result": {"echo": "hello"},
        }
        call_tool.assert_awaited_once_with("echo", {"text": "hello"})
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_call_tool_with_name_alias_returns_result(monkeypatch):
    monkeypatch.setattr(
        "vibe.cli.mcp_apps._host.webbrowser.open", MagicMock(return_value=True)
    )
    call_tool = AsyncMock(return_value={"echo": "alias"})
    host = _make_host(call_tool, AsyncMock(return_value=None))

    try:
        session = await host.start()
        async with httpx.AsyncClient(
            base_url=session.origin, trust_env=False
        ) as client:
            response = await client.post(
                "/api/message",
                json={
                    "type": "call_tool",
                    "token": session.token,
                    "request_id": "tool-alias",
                    "name": "echo_alias",
                    "arguments": {"text": "alias"},
                },
            )

        assert response.status_code == 200
        assert response.json() == {
            "type": "tool_result",
            "request_id": "tool-alias",
            "result": {"echo": "alias"},
        }
        call_tool.assert_awaited_once_with("echo_alias", {"text": "alias"})
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_send_user_message_without_context_returns_result(monkeypatch):
    monkeypatch.setattr(
        "vibe.cli.mcp_apps._host.webbrowser.open", MagicMock(return_value=True)
    )
    send_user_message = AsyncMock(return_value={"accepted": True})
    host = _make_host(AsyncMock(return_value={}), send_user_message)

    try:
        session = await host.start()
        async with httpx.AsyncClient(
            base_url=session.origin, trust_env=False
        ) as client:
            response = await client.post(
                "/api/message",
                json={
                    "type": "send_user_message",
                    "token": session.token,
                    "request_id": "message-1",
                    "message": "hello user",
                },
            )

        assert response.status_code == 200
        assert response.json() == {
            "type": "user_message_result",
            "request_id": "message-1",
            "result": {"accepted": True},
        }
        send_user_message.assert_awaited_once_with("hello user")
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_send_user_message_with_context_returns_result(monkeypatch):
    monkeypatch.setattr(
        "vibe.cli.mcp_apps._host.webbrowser.open", MagicMock(return_value=True)
    )
    context = {"source": "studio", "selection": [1, "two", None]}
    send_user_message = AsyncMock(return_value={"accepted": True})
    host = _make_host(AsyncMock(return_value={}), send_user_message)

    try:
        session = await host.start()
        async with httpx.AsyncClient(
            base_url=session.origin, trust_env=False
        ) as client:
            response = await client.post(
                "/api/message",
                json={
                    "type": "send_user_message",
                    "token": session.token,
                    "request_id": "message-context",
                    "message": "hello with context",
                    "context": context,
                },
            )

        assert response.status_code == 200
        assert response.json() == {
            "type": "user_message_result",
            "request_id": "message-context",
            "result": {"accepted": True},
        }
        send_user_message.assert_awaited_once_with("hello with context", context)
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_send_user_message_with_context_keeps_legacy_callback(monkeypatch):
    monkeypatch.setattr(
        "vibe.cli.mcp_apps._host.webbrowser.open", MagicMock(return_value=True)
    )
    received_messages: list[str] = []

    async def send_user_message(message: str) -> object:
        received_messages.append(message)
        return {"accepted": True}

    host = MCPAppHost(
        app_html=load_test_app_html(),
        initial_state=MCPAppInitialState(),
        call_tool=AsyncMock(return_value={}),
        send_user_message=send_user_message,
    )

    try:
        session = await host.start()
        async with httpx.AsyncClient(
            base_url=session.origin, trust_env=False
        ) as client:
            response = await client.post(
                "/api/message",
                json={
                    "type": "send_user_message",
                    "token": session.token,
                    "request_id": "message-legacy",
                    "message": "legacy callback",
                    "context": {"source": "studio"},
                },
            )

        assert response.status_code == 200
        assert received_messages == ["legacy callback"]
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_send_user_message_rejects_non_object_context(monkeypatch):
    monkeypatch.setattr(
        "vibe.cli.mcp_apps._host.webbrowser.open", MagicMock(return_value=True)
    )
    send_user_message = AsyncMock(return_value={"accepted": True})
    host = _make_host(AsyncMock(return_value={}), send_user_message)

    try:
        session = await host.start()
        async with httpx.AsyncClient(
            base_url=session.origin, trust_env=False
        ) as client:
            response = await client.post(
                "/api/message",
                json={
                    "type": "send_user_message",
                    "token": session.token,
                    "request_id": "message-invalid-context",
                    "message": "invalid context",
                    "context": ["not", "an", "object"],
                },
            )

        assert response.status_code == 400
        assert response.json() == {
            "type": "error",
            "request_id": "",
            "error": "Invalid message",
        }
        send_user_message.assert_not_awaited()
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_callback_error_is_propagated(monkeypatch):
    monkeypatch.setattr(
        "vibe.cli.mcp_apps._host.webbrowser.open", MagicMock(return_value=True)
    )
    call_tool = AsyncMock(side_effect=ValueError("tool exploded"))
    host = _make_host(call_tool, AsyncMock(return_value=None))

    try:
        session = await host.start()
        async with httpx.AsyncClient(
            base_url=session.origin, trust_env=False
        ) as client:
            response = await client.post(
                "/api/message",
                json={
                    "type": "call_tool",
                    "token": session.token,
                    "request_id": "tool-error",
                    "tool_name": "explode",
                    "arguments": {},
                },
            )

        assert response.status_code == 500
        assert response.json() == {
            "type": "error",
            "request_id": "tool-error",
            "error": "tool exploded",
        }
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_invalid_token_is_rejected_before_callback(monkeypatch):
    monkeypatch.setattr(
        "vibe.cli.mcp_apps._host.webbrowser.open", MagicMock(return_value=True)
    )
    call_tool = AsyncMock(return_value={})
    host = _make_host(call_tool, AsyncMock(return_value=None))

    try:
        session = await host.start()
        async with httpx.AsyncClient(
            base_url=session.origin, trust_env=False
        ) as client:
            response = await client.post(
                "/api/message",
                json={
                    "type": "call_tool",
                    "token": "wrong-token",
                    "request_id": "tool-forbidden",
                    "tool_name": "echo",
                    "arguments": {},
                },
            )

        assert response.status_code == 403
        assert response.json() == {
            "type": "error",
            "request_id": "tool-forbidden",
            "error": "Invalid session token",
        }
        call_tool.assert_not_awaited()
    finally:
        await host.stop()


@pytest.mark.asyncio
async def test_stop_is_clean_and_idempotent(monkeypatch):
    monkeypatch.setattr(
        "vibe.cli.mcp_apps._host.webbrowser.open", MagicMock(return_value=True)
    )
    host = _make_host(AsyncMock(return_value={}), AsyncMock(return_value=None))
    session = await host.start()

    await host.stop()
    await host.stop()

    assert not host.is_running
    async with httpx.AsyncClient(base_url=session.origin, trust_env=False) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("/")
