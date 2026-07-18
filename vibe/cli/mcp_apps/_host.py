from __future__ import annotations

import asyncio
from collections.abc import Generator
from contextlib import contextmanager, suppress
import json
import secrets
import socket
import webbrowser

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route
import uvicorn

from vibe.cli.mcp_apps._page import render_host_page
from vibe.cli.mcp_apps._protocol import (
    REQUEST_ADAPTER,
    CallToolRequest,
    SendUserMessageRequest,
)
from vibe.cli.mcp_apps.models import (
    CallTool,
    MCPAppInitialState,
    MCPAppSession,
    SendUserMessage,
)

_LOCALHOST = "127.0.0.1"
_START_TIMEOUT_SECONDS = 5


class MCPAppHostError(RuntimeError):
    pass


class _MCPAppServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self) -> Generator[None, None, None]:
        # Vibe owns process signals; an embedded server must not replace them.
        yield


class MCPAppHost:
    def __init__(
        self,
        *,
        app_html: str,
        initial_state: MCPAppInitialState,
        call_tool: CallTool,
        send_user_message: SendUserMessage,
    ) -> None:
        self._app_html = app_html
        self._initial_state = initial_state
        self._call_tool = call_tool
        self._send_user_message = send_user_message
        self._open_browser = webbrowser.open
        self._page_html = ""
        self._session: MCPAppSession | None = None
        self._server: _MCPAppServer | None = None
        self._serve_task: asyncio.Task[None] | None = None
        self._listener: socket.socket | None = None
        self._app = Starlette(
            routes=[
                Route("/", self._serve_page, methods=["GET"]),
                Route("/api/message", self._handle_message, methods=["POST"]),
            ]
        )

    @property
    def session(self) -> MCPAppSession:
        if self._session is None:
            raise MCPAppHostError("MCP App host has not been started")
        return self._session

    @property
    def is_running(self) -> bool:
        return self._serve_task is not None and not self._serve_task.done()

    async def start(self) -> MCPAppSession:
        if self.is_running:
            raise MCPAppHostError("MCP App host is already running")

        token = secrets.token_urlsafe(32)
        page_html = await asyncio.to_thread(
            render_host_page,
            token=token,
            app_html=self._app_html,
            initial_state=self._initial_state,
        )
        listener = self._create_listener()
        port = int(listener.getsockname()[1])
        session = MCPAppSession(host=_LOCALHOST, port=port, token=token)
        self._listener = listener
        self._page_html = page_html
        self._session = session

        try:
            server = _MCPAppServer(
                uvicorn.Config(
                    self._app,
                    host=_LOCALHOST,
                    port=port,
                    access_log=False,
                    date_header=False,
                    lifespan="off",
                    log_config=None,
                    proxy_headers=False,
                    server_header=False,
                    timeout_graceful_shutdown=2,
                    ws="none",
                )
            )
            task = asyncio.create_task(
                server.serve(sockets=[listener]), name="mcp-app-host"
            )
            self._server = server
            self._serve_task = task
            async with asyncio.timeout(_START_TIMEOUT_SECONDS):
                await self._wait_until_started()
            await asyncio.to_thread(self._open_browser, session.url)
        except Exception as e:
            await self._abort_start()
            raise MCPAppHostError("Failed to start MCP App host") from e

        return session

    async def stop(self) -> None:
        task = self._serve_task
        if task is None:
            return
        if self._server is not None:
            self._server.should_exit = True

        try:
            await task
        finally:
            self._close_listener()
            self._server = None
            self._serve_task = None

    async def __aenter__(self) -> MCPAppHost:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        await self.stop()

    async def _serve_page(self, request: Request) -> Response:
        if not self._has_valid_token(request.query_params.get("token")):
            return Response("Forbidden", status_code=403)
        return HTMLResponse(
            self._page_html,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    async def _handle_message(self, request: Request) -> Response:
        try:
            payload = await request.json()
            message = REQUEST_ADAPTER.validate_python(payload)
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError):
            return self._error_response("", "Invalid message", status_code=400)

        if not self._has_valid_token(message.token):
            return self._error_response(
                message.request_id, "Invalid session token", status_code=403
            )

        try:
            match message:
                case CallToolRequest():
                    result = await self._call_tool(message.tool_name, message.arguments)
                    return JSONResponse({
                        "type": "tool_result",
                        "request_id": message.request_id,
                        "result": result,
                    })
                case SendUserMessageRequest():
                    result = await self._send_user_message(message.message)
                    return JSONResponse({
                        "type": "user_message_result",
                        "request_id": message.request_id,
                        "result": result,
                    })
                case _:
                    return self._error_response(
                        message.request_id, "Unsupported message", status_code=400
                    )
        except Exception as e:
            return self._error_response(
                message.request_id, str(e) or type(e).__name__, status_code=500
            )

    def _has_valid_token(self, token: str | None) -> bool:
        return token is not None and secrets.compare_digest(token, self.session.token)

    async def _wait_until_started(self) -> None:
        task = self._serve_task
        server = self._server
        if task is None or server is None:
            raise MCPAppHostError("MCP App host server is not configured")

        while not server.started:
            if task.done():
                await task
                raise MCPAppHostError("MCP App host stopped during startup")
            await asyncio.sleep(0)

    async def _abort_start(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._serve_task is not None:
            self._serve_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._serve_task
        self._close_listener()
        self._server = None
        self._serve_task = None

    def _create_listener(self) -> socket.socket:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((_LOCALHOST, 0))
            listener.listen()
            listener.setblocking(False)
        except Exception:
            listener.close()
            raise
        return listener

    def _close_listener(self) -> None:
        if self._listener is not None:
            self._listener.close()
            self._listener = None

    @staticmethod
    def _error_response(
        request_id: str, error: str, *, status_code: int
    ) -> JSONResponse:
        return JSONResponse(
            {"type": "error", "request_id": request_id, "error": error},
            status_code=status_code,
        )
