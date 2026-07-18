from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict, Field

CallTool = Callable[[str, dict[str, object]], Awaitable[object]]
SendUserMessage = Callable[[str], Awaitable[object]]


class MCPAppInitialState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_arguments: dict[str, object] = Field(default_factory=dict)
    tool_result: object | None = None


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
