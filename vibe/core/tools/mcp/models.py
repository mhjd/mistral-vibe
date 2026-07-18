from __future__ import annotations

from typing import Annotated, Any

from pydantic import AnyUrl, BaseModel, ConfigDict, Field, UrlConstraints

MCPResourceURI = Annotated[AnyUrl, UrlConstraints(host_required=False)]


class MCPAppResourceContent(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    uri: MCPResourceURI
    mime_type: str | None = None
    text: str
    metadata: dict[str, Any] | None = Field(default=None, serialization_alias="_meta")


class MCPAppResource(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    server_alias: str
    uri: MCPResourceURI
    mime_type: str | None = None
    text: str
    metadata: dict[str, Any] | None = Field(default=None, serialization_alias="_meta")
    contents: tuple[MCPAppResourceContent, ...]
