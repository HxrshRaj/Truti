"""Pydantic schema for envelope v1 (see docs/envelope.md)."""
import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
Str = Field(max_length=2000)


class Frame(BaseModel):
    model_config = ConfigDict(extra="ignore")
    filename: str = Field(max_length=1000)
    function: Optional[str] = Field(default=None, max_length=500)
    lineno: Optional[int] = Field(default=None, ge=0, le=10_000_000)
    colno: Optional[int] = Field(default=None, ge=0, le=10_000_000)
    context_line: Optional[str] = Field(default=None, max_length=2000)
    pre_context: list[str] = Field(default_factory=list, max_length=20)
    post_context: list[str] = Field(default_factory=list, max_length=20)
    in_app: Optional[bool] = None


class ExceptionInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: str = Field(max_length=300)
    value: str = Field(default="", max_length=10_000)
    frames: list[Frame] = Field(default_factory=list, max_length=250)


class Breadcrumb(BaseModel):
    model_config = ConfigDict(extra="allow")
    timestamp: Optional[str] = None
    category: Optional[str] = Field(default=None, max_length=100)
    message: Optional[str] = Field(default=None, max_length=2000)
    level: Optional[str] = Field(default=None, max_length=20)


class Event(BaseModel):
    model_config = ConfigDict(extra="ignore")
    event_id: str
    timestamp: str
    platform: Literal["python", "javascript"]
    level: Literal["fatal", "error", "warning", "info", "debug"] = "error"
    message: Optional[str] = Field(default=None, max_length=10_000)
    release: Optional[str] = Field(default=None, max_length=200)
    environment: Optional[str] = Field(default=None, max_length=100)
    exception: Optional[ExceptionInfo] = None
    breadcrumbs: list[Breadcrumb] = Field(default_factory=list, max_length=200)
    tags: dict[str, str] = Field(default_factory=dict)
    user: Optional[dict] = None
    request: Optional[dict] = None
    sdk: Optional[dict] = None

    @field_validator("event_id")
    @classmethod
    def _eid(cls, v):
        if not _HEX32.match(v):
            raise ValueError("event_id must be 32 lowercase hex characters")
        return v

    @field_validator("tags")
    @classmethod
    def _tags(cls, v):
        if len(v) > 50:
            raise ValueError("at most 50 tags")
        return v

    @field_validator("timestamp")
    @classmethod
    def _ts(cls, v):
        import datetime as dt
        try:
            dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("timestamp must be ISO-8601")
        return v

    @model_validator(mode="after")
    def _needs_content(self):
        if self.exception is None and not self.message:
            raise ValueError("event needs either 'exception' or 'message'")
        return self


class Envelope(BaseModel):
    model_config = ConfigDict(extra="ignore")
    envelope_version: Literal[1]
    sent_at: Optional[str] = None
    events: list[Event] = Field(min_length=1, max_length=50)
