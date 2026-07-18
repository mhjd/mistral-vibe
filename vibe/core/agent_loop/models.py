from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from vibe.core.types import UserDisplayContentMetadata


class ExternalUserMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message: str = Field(min_length=1)
    context: dict[str, JsonValue] | None = None
    source: str = Field(default="external", min_length=1)

    @field_validator("message", "source")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    def render(self) -> str:
        if self.context is None:
            return self.message
        rendered_context = json.dumps(
            self.context, ensure_ascii=False, indent=2, sort_keys=True
        )
        return (
            f"{self.message}\n\n"
            f"Context supplied by {self.source}:\n"
            f"```json\n{rendered_context}\n```"
        )

    def display_metadata(self) -> UserDisplayContentMetadata | None:
        if self.context is None:
            return None
        return UserDisplayContentMetadata(
            version="1.0.0",
            host=self.source,
            content=[{"type": "external_context", "context": self.context}],
        )
