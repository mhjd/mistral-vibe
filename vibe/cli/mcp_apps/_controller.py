from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
import json

from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError

from vibe.cli.mcp_apps._events import MCPAppToolEventCorrelator
from vibe.cli.mcp_apps._port import (
    MCPAppErrorHandler,
    MCPAppHostFactory,
    MCPAppHostPort,
    MCPAppResourceData,
    MCPAppResourceLoader,
)
from vibe.cli.mcp_apps.models import (
    CallTool,
    MCPAppInitialState,
    MCPAppOpenRequest,
    MCPAppSession,
    SendUserMessage,
)
from vibe.core.logger import logger
from vibe.core.types import BaseEvent

_MAX_APP_HTML_BYTES = 2 * 1024 * 1024
_JSON_VALUE_ADAPTER = TypeAdapter(JsonValue)


class MCPAppControllerError(RuntimeError):
    pass


class MCPAppResourceError(MCPAppControllerError):
    pass


class MCPAppHostStartError(MCPAppControllerError):
    pass


@dataclass(frozen=True, slots=True)
class MCPAppActiveSession:
    request: MCPAppOpenRequest
    resource: MCPAppResourceData
    host_session: MCPAppSession
    host: MCPAppHostPort


class MCPAppController:
    def __init__(
        self,
        *,
        resource_loader: MCPAppResourceLoader,
        call_tool: CallTool,
        send_user_message: SendUserMessage,
        host_factory: MCPAppHostFactory | None = None,
        error_handler: MCPAppErrorHandler | None = None,
    ) -> None:
        self._resource_loader = resource_loader
        self._call_tool = call_tool
        self._send_user_message = send_user_message
        self._host_factory = host_factory or _create_browser_host
        self._error_handler = error_handler
        self._correlator = MCPAppToolEventCorrelator()
        self._operation_lock = asyncio.Lock()
        self._active_session: MCPAppActiveSession | None = None
        self._open_task: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False
        self._last_error: str | None = None

    @property
    def active_session(self) -> MCPAppActiveSession | None:
        return self._active_session

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def pending_task_count(self) -> int:
        return sum(not task.done() for task in self._tasks)

    def set_error_handler(self, error_handler: MCPAppErrorHandler | None) -> None:
        self._error_handler = error_handler

    def observe_event(self, event: BaseEvent) -> bool:
        if self._closed:
            return False
        request = self._correlator.handle(event)
        if request is None:
            return False
        if self._open_task is not None and not self._open_task.done():
            self._open_task.cancel()
        task = asyncio.create_task(
            self._open_from_event(request), name="mcp-app-controller-open"
        )
        self._open_task = task
        self._tasks.add(task)
        task.add_done_callback(self._discard_task)
        return True

    async def open(self, request: MCPAppOpenRequest) -> MCPAppActiveSession:
        async with self._operation_lock:
            if self._closed:
                raise MCPAppControllerError("MCP App controller is closed")

            resource = await self._load_resource(request)
            initial_state = await asyncio.to_thread(_build_initial_state, request)
            try:
                host = self._host_factory(
                    app_html=resource.text,
                    initial_state=initial_state,
                    call_tool=self._call_tool,
                    send_user_message=self._send_user_message,
                )
            except Exception as e:
                raise MCPAppHostStartError("Failed to create MCP App host") from e

            try:
                host_session = await host.start()
            except asyncio.CancelledError:
                await _stop_after_cancel(host)
                raise
            except Exception as e:
                await _stop_after_error(host)
                raise MCPAppHostStartError("Failed to start MCP App host") from e

            active_session = MCPAppActiveSession(
                request=request, resource=resource, host_session=host_session, host=host
            )
            previous = self._active_session
            self._active_session = active_session
            if previous is not None:
                try:
                    await previous.host.stop()
                except Exception as e:
                    logger.warning("Failed to stop replaced MCP App host", exc_info=e)
            self._last_error = None
            return active_session

    async def close(self) -> None:
        await self._cancel_observed_open()
        self._correlator.clear()
        async with self._operation_lock:
            active = self._active_session
            self._active_session = None
            if active is None:
                return
            try:
                await active.host.stop()
            except Exception as e:
                raise MCPAppControllerError("Failed to stop MCP App host") from e

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.close()

    async def _load_resource(self, request: MCPAppOpenRequest) -> MCPAppResourceData:
        try:
            resource = await self._resource_loader(
                request.tool.server_name, request.tool.resource_uri
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            raise MCPAppResourceError(
                f"Failed to load MCP App resource {request.tool.resource_uri}"
            ) from e
        _validate_resource(resource, request.tool.resource_uri)
        return resource

    async def _open_from_event(self, request: MCPAppOpenRequest) -> None:
        try:
            await self.open(request)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            error = str(e) or type(e).__name__
            self._last_error = error
            logger.warning("Failed to open MCP App: %s", error, exc_info=e)
            if self._error_handler is not None:
                with suppress(Exception):
                    await self._error_handler(error)

    async def _cancel_observed_open(self) -> None:
        tasks = list(self._tasks)
        self._open_task = None
        for task in tasks:
            if not task.done():
                task.cancel()
        for task in tasks:
            if task.done():
                continue
            with suppress(asyncio.CancelledError):
                await task

    def _discard_task(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if self._open_task is task:
            self._open_task = None


def _validate_resource(resource: MCPAppResourceData, expected_uri: str) -> None:
    if str(resource.uri) != expected_uri:
        raise MCPAppResourceError("MCP App resource URI does not match the request")
    media_type = (resource.mime_type or "").partition(";")[0].strip().lower()
    if media_type not in {"text/html", "application/xhtml+xml"}:
        raise MCPAppResourceError("MCP App resource must contain HTML text")
    if not resource.text.strip():
        raise MCPAppResourceError("MCP App resource HTML is empty")
    if len(resource.text.encode("utf-8")) > _MAX_APP_HTML_BYTES:
        raise MCPAppResourceError("MCP App resource exceeds the 2 MiB limit")


def _build_initial_state(request: MCPAppOpenRequest) -> MCPAppInitialState:
    arguments = _json_value(request.arguments)
    if not isinstance(arguments, dict):
        raise MCPAppControllerError("MCP App tool arguments must be an object")
    return MCPAppInitialState(
        server_name=request.tool.server_name,
        tool_name=request.tool.tool_name,
        remote_tool_name=request.tool.remote_tool_name,
        resource_uri=request.tool.resource_uri,
        tool_arguments=arguments,
        tool_result=_json_value(request.result),
    )


def _json_value(value: object) -> JsonValue:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", warnings="none", fallback=str)
    try:
        serialized = json.dumps(value, default=str, ensure_ascii=False, allow_nan=False)
        return _JSON_VALUE_ADAPTER.validate_python(json.loads(serialized))
    except (TypeError, ValueError, ValidationError):
        return str(value)


async def _stop_after_cancel(host: MCPAppHostPort) -> None:
    task = asyncio.create_task(host.stop())
    with suppress(Exception):
        await asyncio.shield(task)


async def _stop_after_error(host: MCPAppHostPort) -> None:
    with suppress(Exception):
        await host.stop()


def _create_browser_host(
    *,
    app_html: str,
    initial_state: MCPAppInitialState,
    call_tool: CallTool,
    send_user_message: SendUserMessage,
) -> MCPAppHostPort:
    from vibe.cli.mcp_apps._host import MCPAppHost

    return MCPAppHost(
        app_html=app_html,
        initial_state=initial_state,
        call_tool=call_tool,
        send_user_message=send_user_message,
    )
