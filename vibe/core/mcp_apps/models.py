from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum, auto

from pydantic import BaseModel, ConfigDict, JsonValue

from vibe.core.types import BaseEvent


class ToolCallStatus(StrEnum):
    SUCCESS = auto()
    ERROR = auto()
    PERMISSION_DENIED = auto()
    CANCELLED = auto()


class UserMessageStatus(StrEnum):
    COMPLETED = auto()
    CLOSED = auto()


class ToolCallResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ToolCallStatus
    tool_name: str
    tool_call_id: str
    content: JsonValue | None = None
    structured_content: JsonValue | None = None
    error: str | None = None


class UserMessageResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: UserMessageStatus
    message_id: str
    queued: bool
    error: str | None = None


type MCPAppCallbackResult = dict[str, JsonValue]
type MCPAppCallTool = Callable[
    [str, dict[str, object]], Awaitable[MCPAppCallbackResult]
]
type MCPAppSendUserMessage = Callable[
    [str, dict[str, JsonValue] | None], Awaitable[MCPAppCallbackResult]
]
type MCPAppEventSink = Callable[[BaseEvent], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class MCPAppCallbacks:
    call_tool: MCPAppCallTool
    send_user_message: MCPAppSendUserMessage
