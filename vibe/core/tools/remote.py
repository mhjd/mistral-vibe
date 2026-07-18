from __future__ import annotations

from typing import Annotated, Any, ClassVar

from pydantic import (
    AliasChoices,
    AnyUrl,
    BaseModel,
    ConfigDict,
    Field,
    UrlConstraints,
    ValidationError,
    field_validator,
)

from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState
from vibe.core.tools.ui import ToolUIData


class _OpenArgs(BaseModel):
    model_config = ConfigDict(extra="allow")


class MCPAppUI(BaseModel):
    model_config = ConfigDict(extra="ignore")

    resource_uri: Annotated[AnyUrl, UrlConstraints(host_required=False)] = Field(
        validation_alias="resourceUri"
    )


class MCPAppMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ui: MCPAppUI


def _parse_mcp_app_metadata(metadata: dict[str, Any] | None) -> MCPAppMetadata | None:
    if metadata is None:
        return None
    try:
        return MCPAppMetadata.model_validate(metadata)
    except ValidationError:
        return None


class MCPToolResult(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    ok: bool = True
    server: str
    tool: str
    text: str | None = None
    structured: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("_meta", "meta"),
        serialization_alias="_meta",
    )

    @field_validator("metadata", mode="before")
    @classmethod
    def _normalize_metadata(cls, value: Any) -> dict[str, Any] | None:
        return value if isinstance(value, dict) else None


class MCPTool(
    BaseTool[_OpenArgs, MCPToolResult, BaseToolConfig, BaseToolState],
    ToolUIData[_OpenArgs, MCPToolResult],
):
    _server_name: ClassVar[str] = ""
    _remote_name: ClassVar[str] = ""
    _is_connector: ClassVar[bool] = False
    _metadata: ClassVar[dict[str, Any] | None] = None
    _mcp_app: ClassVar[MCPAppMetadata | None] = None

    @classmethod
    def get_server_name(cls) -> str | None:
        return cls._server_name or None

    @classmethod
    def get_remote_name(cls) -> str:
        return cls._remote_name or cls.get_name()

    @classmethod
    def is_connector(cls) -> bool:
        return cls._is_connector

    @classmethod
    def get_mcp_metadata(cls) -> dict[str, Any] | None:
        return dict(cls._metadata) if cls._metadata is not None else None

    @classmethod
    def get_mcp_app(cls) -> MCPAppMetadata | None:
        return cls._mcp_app

    @classmethod
    def has_mcp_app(cls) -> bool:
        return cls._mcp_app is not None

    @classmethod
    def get_ui_resource_uri(cls) -> str | None:
        if cls._mcp_app is None:
            return None
        return str(cls._mcp_app.ui.resource_uri)


class RemoteTool(BaseModel):
    model_config = ConfigDict(
        extra="ignore", from_attributes=True, populate_by_name=True
    )

    name: str
    description: str | None = None
    input_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}},
        validation_alias="inputSchema",
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("_meta", "meta"),
        serialization_alias="_meta",
    )

    @property
    def mcp_app(self) -> MCPAppMetadata | None:
        return _parse_mcp_app_metadata(self.metadata)

    @property
    def has_mcp_app(self) -> bool:
        return self.mcp_app is not None

    @property
    def ui_resource_uri(self) -> str | None:
        if (app := self.mcp_app) is None:
            return None
        return str(app.ui.resource_uri)

    @field_validator("name")
    @classmethod
    def _non_empty_name(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("MCP tool missing valid 'name'")
        return v

    @field_validator("input_schema", mode="before")
    @classmethod
    def _normalize_schema(cls, v: Any) -> dict[str, Any]:
        if v is None:
            return {"type": "object", "properties": {}}
        if isinstance(v, dict):
            return v
        dump = getattr(v, "model_dump", None)
        if callable(dump):
            try:
                v = dump()
            except Exception:
                raise ValueError(
                    "inputSchema must be a dict or have a valid model_dump method"
                )
        if not isinstance(v, dict):
            raise ValueError("inputSchema must be a dict")
        return v

    @field_validator("metadata", mode="before")
    @classmethod
    def _normalize_metadata(cls, value: Any) -> dict[str, Any] | None:
        return value if isinstance(value, dict) else None
