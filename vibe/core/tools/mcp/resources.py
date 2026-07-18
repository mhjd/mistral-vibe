from __future__ import annotations

from typing import Any

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
)

from vibe.core.tools.mcp.models import (
    MCPAppResource,
    MCPAppResourceContent,
    MCPResourceURI,
)


class MCPResourceError(Exception):
    pass


class MCPServerNotFoundError(MCPResourceError):
    def __init__(self, server_alias: str) -> None:
        super().__init__(f"Unknown MCP server alias: {server_alias}")


class MCPResourceURIError(MCPResourceError):
    pass


class MCPResourceNotFoundError(MCPResourceError):
    pass


class MCPResourceEmptyError(MCPResourceError):
    pass


class MCPResourceContentError(MCPResourceError):
    pass


class MCPResourceReadError(MCPResourceError):
    pass


class MCPResourceSessionError(MCPResourceError):
    pass


class _MCPResourceContentIn(BaseModel):
    model_config = ConfigDict(extra="ignore", from_attributes=True)

    uri: MCPResourceURI
    mime_type: str | None = Field(default=None, validation_alias="mimeType")
    metadata: dict[str, Any] | None = Field(
        default=None, validation_alias=AliasChoices("_meta", "meta")
    )
    text: str | None = None
    blob: str | None = None


class _MCPReadResourceResultIn(BaseModel):
    model_config = ConfigDict(extra="ignore", from_attributes=True)

    contents: list[_MCPResourceContentIn]
    metadata: dict[str, Any] | None = Field(
        default=None, validation_alias=AliasChoices("_meta", "meta")
    )


_URI_ADAPTER = TypeAdapter(MCPResourceURI)


def validate_ui_resource_uri(resource_uri: str | None) -> MCPResourceURI:
    if not isinstance(resource_uri, str) or not resource_uri.strip():
        raise MCPResourceURIError("MCP resource URI is required")
    try:
        uri = _URI_ADAPTER.validate_python(resource_uri)
    except ValidationError as exc:
        raise MCPResourceURIError(
            f"Invalid MCP resource URI: {resource_uri!r}"
        ) from exc
    if uri.scheme != "ui":
        raise MCPResourceURIError(
            f"Unsupported MCP App resource URI scheme: {uri.scheme!r}"
        )
    return uri


def parse_mcp_app_resource(
    *, server_alias: str, requested_uri: MCPResourceURI, result: Any
) -> MCPAppResource:
    try:
        parsed = _MCPReadResourceResultIn.model_validate(result)
    except ValidationError as exc:
        raise MCPResourceContentError(
            f"Invalid resource response from MCP server {server_alias!r}"
        ) from exc

    if not parsed.contents:
        raise MCPResourceEmptyError(
            f"MCP resource {str(requested_uri)!r} returned no contents"
        )
    if any(content.blob is not None for content in parsed.contents):
        raise MCPResourceContentError(
            f"MCP resource {str(requested_uri)!r} contains non-text content"
        )
    if any(content.text is None for content in parsed.contents):
        raise MCPResourceContentError(
            f"MCP resource {str(requested_uri)!r} contains an unsupported content block"
        )

    contents = tuple(
        MCPAppResourceContent(
            uri=content.uri,
            mime_type=content.mime_type,
            text=content.text,
            metadata=content.metadata,
        )
        for content in parsed.contents
        if content.text is not None
    )
    text = "\n".join(content.text for content in contents)
    if not text.strip():
        raise MCPResourceEmptyError(
            f"MCP resource {str(requested_uri)!r} returned empty text"
        )

    mime_types = {content.mime_type for content in contents}
    mime_type = next(iter(mime_types)) if len(mime_types) == 1 else None
    return MCPAppResource(
        server_alias=server_alias,
        uri=requested_uri,
        mime_type=mime_type,
        text=text,
        metadata=parsed.metadata,
        contents=contents,
    )
