"""The operation plan: the only thing that changes a subtitle file.

A plan has content operations (what is in the output) and timing operations
(when it appears). Timing is deliberately small: one optional `scale` for a rate
difference, and `shift` operations that move a time range by a fixed amount.

Every time range in a plan refers to line start times in the *original* file.
`scale` is applied first, then each line receives the shift of the range it
started in. A plan therefore reads the same no matter in which order its
operations are listed.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

_TIMESTAMP = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[.,](\d{1,3}))?$")


def parse_timestamp(value: object) -> int | None:
    """Accept milliseconds, `H:MM:SS.mmm`, or an open end (`None`, "start", "end")."""
    if value is None or isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("start", "end", ""):
            return None
        match = _TIMESTAMP.match(text)
        if match:
            hours, minutes, seconds, fraction = match.groups()
            millis = int((fraction or "0").ljust(3, "0"))
            return (int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)) * 1000 + millis
    raise ValueError(f"not a timestamp: {value!r}")


Timestamp = Annotated[int | None, BeforeValidator(parse_timestamp)]


class RemoveLines(BaseModel):
    """Drop lines from the output."""

    op: Literal["remove_lines"] = "remove_lines"
    reason: str
    ids: list[int]


class StripText(BaseModel):
    """Remove one kind of content from the text of lines. `ids` of None means every line."""

    op: Literal["strip_text"] = "strip_text"
    what: Literal["hearing_impaired", "symbols", "formatting"]
    ids: list[int] | None = None


class Scale(BaseModel):
    """Multiply every time by `factor`. Corrects a frame rate difference."""

    op: Literal["scale"] = "scale"
    factor: float = Field(gt=0)
    reason: str | None = None


class Shift(BaseModel):
    """Move the lines that start in [from, to) by `ms`. An open end is None."""

    model_config = ConfigDict(populate_by_name=True)

    op: Literal["shift"] = "shift"
    from_ms: Timestamp = Field(default=None, alias="from")
    to_ms: Timestamp = Field(default=None, alias="to")
    ms: int
    reason: str | None = None

    def contains(self, start_ms: int) -> bool:
        after_start = self.from_ms is None or start_ms >= self.from_ms
        before_end = self.to_ms is None or start_ms < self.to_ms
        return after_start and before_end


ContentOp = Annotated[RemoveLines | StripText, Field(discriminator="op")]
TimingOp = Annotated[Scale | Shift, Field(discriminator="op")]
Outcome = Literal["synced", "already_in_sync", "partial", "rejected_no_match"]


class Plan(BaseModel):
    outcome: Outcome = "synced"
    content_ops: list[ContentOp] = Field(default_factory=list)
    timing_ops: list[TimingOp] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)

    def to_json(self) -> str:
        return self.model_dump_json(by_alias=True, exclude_none=True, indent=2)
