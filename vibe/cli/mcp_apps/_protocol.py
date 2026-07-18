from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, JsonValue, TypeAdapter


class MessageBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1)
    request_id: str = Field(min_length=1)


class CallToolRequest(MessageBase):
    type: Literal["call_tool"]
    tool_name: str = Field(
        min_length=1, validation_alias=AliasChoices("tool_name", "name")
    )
    arguments: dict[str, object]


class SendUserMessageRequest(MessageBase):
    type: Literal["send_user_message"]
    message: str = Field(min_length=1)
    context: dict[str, JsonValue] | None = None


MCPAppRequest = Annotated[
    CallToolRequest | SendUserMessageRequest, Field(discriminator="type")
]
REQUEST_ADAPTER = TypeAdapter(MCPAppRequest)
