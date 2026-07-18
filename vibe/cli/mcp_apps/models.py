from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict, Field, JsonValue

CallTool = Callable[[str, dict[str, object]], Awaitable[object]]
UserMessageContext = dict[str, JsonValue]
LegacySendUserMessage = Callable[[str], Awaitable[object]]
ContextualSendUserMessage = Callable[[str, UserMessageContext], Awaitable[object]]
SendUserMessage = LegacySendUserMessage | ContextualSendUserMessage


class MCPAppInitialState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    server_name: str | None = None
    tool_name: str | None = None
    remote_tool_name: str | None = None
    resource_uri: str | None = None
    tool_arguments: dict[str, JsonValue] = Field(default_factory=dict)
    tool_result: JsonValue = None


class MCPAppResource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    uri: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)
    text: str


@dataclass(frozen=True, slots=True)
class MCPAppToolDescriptor:
    server_name: str
    tool_name: str
    remote_tool_name: str
    resource_uri: str


@dataclass(frozen=True, slots=True)
class MCPAppOpenRequest:
    tool: MCPAppToolDescriptor
    arguments: dict[str, object]
    result: object | None


@dataclass(frozen=True, slots=True)
class MCPAppSession:
    host: str
    port: int
    token: str

    @property
    def origin(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def url(self) -> str:
        return f"{self.origin}/?{urlencode({'token': self.token})}"
