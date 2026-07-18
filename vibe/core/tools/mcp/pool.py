from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import timedelta
import hashlib
from typing import TYPE_CHECKING, Any

import anyio
from pydantic import AnyUrl

from mcp.shared.exceptions import McpError
from vibe.core.logger import logger
from vibe.core.tools.mcp.models import MCPAppResource
from vibe.core.tools.mcp.resources import (
    MCPResourceError,
    MCPResourceNotFoundError,
    MCPResourceReadError,
    MCPResourceSessionError,
    MCPServerNotFoundError,
    parse_mcp_app_resource,
    validate_ui_resource_uri,
)
from vibe.core.tools.mcp.tools import (
    MCPToolResult,
    _parse_call_result as parse_call_result,
    build_stdio_params,
    enter_stdio_session,
)

if TYPE_CHECKING:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters
    from vibe.core.tools.mcp_sampling import MCPSamplingHandler


# Errors that indicate the stdio transport (subprocess / pipe) is gone and the
# session must be respawned. Tool-level errors and timeouts are deliberately
# excluded: they are legitimate server responses, not dead connections.
_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (
    anyio.ClosedResourceError,
    anyio.BrokenResourceError,
    BrokenPipeError,
    ConnectionError,
    EOFError,
)

# How long aclose waits for a connection's worker to drain and shut its session
# down gracefully before cancelling it.
_CLOSE_TIMEOUT_SEC = 5.0


def stdio_key(command: list[str], env: dict[str, str] | None, cwd: str | None) -> str:
    # \0 and \x01 delimit the three identity fields so distinct inputs cannot
    # collide (e.g. ["a b"] vs ["a", "b"], or command vs env boundaries).
    env_part = "\0".join(f"{k}={v}" for k, v in sorted((env or {}).items()))
    raw = "\0".join(command) + "\x01" + env_part + "\x01" + (cwd or "")
    return hashlib.blake2s(raw.encode("utf-8"), digest_size=16).hexdigest()


@dataclass
class _ToolRequest:
    tool_name: str
    arguments: dict[str, Any]
    call_timeout: timedelta | None
    future: asyncio.Future[Any]


@dataclass
class _ResourceRequest:
    uri: AnyUrl
    future: asyncio.Future[Any]


type _Request = _ToolRequest | _ResourceRequest


class _StdioConnection:
    """A single long-lived stdio MCP session owned by one dedicated task.

    The MCP ``stdio_client`` and ``ClientSession`` context managers open anyio
    task groups bound to the task that enters them, so the session must be
    entered, used, and exited all within the same task. A single worker task
    owns the session for its whole lifetime and services calls from a queue;
    callers submit a request and await its future. Because the worker handles
    one request at a time, calls to the same server are serialized (stateful
    servers never see interleaved requests). On transport death the worker drops
    the session and respawns it once before retrying the call.
    """

    def __init__(
        self,
        params: StdioServerParameters,
        init_timeout: timedelta | None,
        sampling_callback: MCPSamplingHandler | None,
    ) -> None:
        self._params = params
        self._init_timeout = init_timeout
        self._sampling_callback = sampling_callback
        self._requests: asyncio.Queue[_Request | None] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._session: ClientSession | None = None
        self._stack: contextlib.AsyncExitStack | None = None
        self._inflight: _Request | None = None

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run())

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any], call_timeout: timedelta | None
    ) -> Any:
        self._ensure_worker()
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        await self._requests.put(
            _ToolRequest(tool_name, arguments, call_timeout, future)
        )
        return await future

    async def read_resource(self, uri: AnyUrl) -> Any:
        self._ensure_worker()
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        await self._requests.put(_ResourceRequest(uri, future))
        return await future

    async def _run(self) -> None:
        try:
            while True:
                req = await self._requests.get()
                if req is None:
                    return
                self._inflight = req
                try:
                    result = await self._handle(req)
                except Exception as exc:
                    if not req.future.done():
                        req.future.set_exception(exc)
                    self._inflight = None
                else:
                    if not req.future.done():
                        req.future.set_result(result)
                    self._inflight = None
        finally:
            await self._close_session()
            self._fail_pending()

    async def _handle(self, req: _Request) -> Any:
        session = await self._ensure_session()
        try:
            return await self._execute(session, req)
        except _TRANSPORT_ERRORS as exc:
            logger.debug("MCP stdio transport died, reconnecting once: %r", exc)
            await self._close_session()
            session = await self._ensure_session()
            return await self._execute(session, req)

    @staticmethod
    async def _execute(session: ClientSession, req: _Request) -> Any:
        match req:
            case _ToolRequest():
                return await session.call_tool(
                    req.tool_name, req.arguments, read_timeout_seconds=req.call_timeout
                )
            case _ResourceRequest():
                return await session.read_resource(req.uri)

    async def _ensure_session(self) -> ClientSession:
        if self._session is not None:
            return self._session
        stack = contextlib.AsyncExitStack()
        try:
            session = await enter_stdio_session(
                stack,
                self._params,
                init_timeout=self._init_timeout,
                sampling_callback=self._sampling_callback,
            )
        except BaseException:
            await stack.aclose()
            raise
        self._stack = stack
        self._session = session
        return session

    async def _close_session(self) -> None:
        stack, self._stack = self._stack, None
        self._session = None
        if stack is not None:
            with contextlib.suppress(Exception):
                await stack.aclose()

    def _fail_pending(self) -> None:
        err = RuntimeError("MCP stdio connection closed")
        if self._inflight is not None and not self._inflight.future.done():
            self._inflight.future.set_exception(err)
        self._inflight = None
        while not self._requests.empty():
            try:
                req = self._requests.get_nowait()
            except asyncio.QueueEmpty:
                break
            if req is not None and not req.future.done():
                req.future.set_exception(err)

    async def aclose(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is None or worker.done():
            return
        await self._requests.put(None)
        try:
            await asyncio.wait_for(worker, _CLOSE_TIMEOUT_SEC)
        except Exception as exc:
            logger.debug("MCP stdio worker shutdown error: %r", exc)
            if not worker.done():
                worker.cancel()
                with contextlib.suppress(BaseException):
                    await worker


class MCPConnectionPool:
    """Session-scoped pool of persistent stdio MCP connections.

    Owned by an ``AgentLoop`` and created lazily in that loop's event loop on the
    first call (discovery runs in a throwaway loop, so connections must not be
    shared with it). Connections live until ``aclose`` is called at session end.
    """

    def __init__(self) -> None:
        self._conns: dict[str, _StdioConnection] = {}
        self._connection_keys_by_alias: dict[str, str] = {}
        self._creation_lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("MCP connection pool is closed")

    def _bind_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is loop:
            return
        if self._loop is not None:
            # Running under a different loop than the cached connections were
            # created on: those connections (and their worker tasks) are dead
            # here and cannot be closed on a loop that is no longer running.
            logger.debug(
                "MCP pool bound to a new event loop; dropping %d stale connection(s)",
                len(self._conns),
            )
            self._conns.clear()
        self._loop = loop

    async def _get_or_create(
        self,
        key: str,
        command: list[str],
        env: dict[str, str] | None,
        cwd: str | None,
        startup_timeout_sec: float | None,
        sampling_callback: MCPSamplingHandler | None,
    ) -> _StdioConnection:
        if (conn := self._conns.get(key)) is not None:
            return conn
        async with self._creation_lock:
            if (conn := self._conns.get(key)) is not None:
                return conn
            params = build_stdio_params(command, env=env, cwd=cwd)
            init_timeout = (
                timedelta(seconds=startup_timeout_sec) if startup_timeout_sec else None
            )
            conn = _StdioConnection(params, init_timeout, sampling_callback)
            self._conns[key] = conn
            return conn

    async def call_tool(
        self,
        *,
        command: list[str],
        tool_name: str,
        arguments: dict[str, Any],
        server_alias: str | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        startup_timeout_sec: float | None = None,
        tool_timeout_sec: float | None = None,
        sampling_callback: MCPSamplingHandler | None = None,
    ) -> MCPToolResult:
        self._ensure_open()
        self._bind_loop()
        key = stdio_key(command, env, cwd)
        conn = await self._get_or_create(
            key, command, env, cwd, startup_timeout_sec, sampling_callback
        )
        if server_alias:
            self._connection_keys_by_alias[server_alias] = key
        call_timeout = timedelta(seconds=tool_timeout_sec) if tool_timeout_sec else None
        result = await conn.call_tool(tool_name, arguments, call_timeout)
        return parse_call_result("stdio:" + " ".join(command), tool_name, result)

    async def read_resource(
        self, *, server_alias: str, resource_uri: str | None
    ) -> MCPAppResource:
        if self._closed:
            raise MCPResourceSessionError("MCP connection pool is closed")
        self._bind_loop()
        key = self._connection_keys_by_alias.get(server_alias)
        if key is None:
            raise MCPServerNotFoundError(server_alias)
        conn = self._conns.get(key)
        if conn is None:
            raise MCPResourceSessionError(
                f"MCP session for server {server_alias!r} is unavailable"
            )

        uri = validate_ui_resource_uri(resource_uri)
        try:
            result = await conn.read_resource(uri)
        except McpError as exc:
            message = str(exc)
            if "resource" in message.lower() and (
                "unknown" in message.lower() or "not found" in message.lower()
            ):
                raise MCPResourceNotFoundError(
                    f"Unknown MCP resource {resource_uri!r} on server {server_alias!r}"
                ) from exc
            raise MCPResourceReadError(
                f"MCP server {server_alias!r} failed to read {resource_uri!r}: {message}"
            ) from exc
        except _TRANSPORT_ERRORS as exc:
            raise MCPResourceSessionError(
                f"MCP session for server {server_alias!r} is unavailable"
            ) from exc
        except MCPResourceError:
            raise
        except Exception as exc:
            raise MCPResourceReadError(
                f"Failed to read MCP resource {resource_uri!r} "
                f"from server {server_alias!r}"
            ) from exc

        return parse_mcp_app_resource(
            server_alias=server_alias, requested_uri=uri, result=result
        )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        conns = list(self._conns.values())
        self._conns.clear()
        self._connection_keys_by_alias.clear()
        self._loop = None
        await asyncio.gather(*(conn.aclose() for conn in conns), return_exceptions=True)
