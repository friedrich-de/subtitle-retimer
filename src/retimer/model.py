"""The in-memory subtitle model.

Text is kept in ASS form for every input format: styling lives in override blocks
(`{\\i1}`), and line breaks are the two characters `\\N`. SRT files are converted to
this form on load and back on save, so every tool works on one representation.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

import pysubs2


@dataclass(frozen=True)
class Line:
    """One subtitle event. `id` is its position in the original file and never changes."""

    id: int
    start_ms: int
    end_ms: int
    text: str
    style: str
    is_comment: bool

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


class Subtitle:
    """A parsed subtitle file.

    Line ids are assigned once, when the file is loaded. A subtitle produced by the
    plan engine keeps the ids of the lines that survived, so reports about the result
    can always be traced back to the original file.
    """

    def __init__(
        self,
        ssa: pysubs2.SSAFile,
        fmt: str,
        encoding: str = "utf-8",
        ids: list[int] | None = None,
    ) -> None:
        self.ssa = ssa
        self.format = fmt
        self.encoding = encoding
        self.ids = list(ids) if ids is not None else list(range(len(ssa.events)))
        if len(self.ids) != len(ssa.events):
            raise ValueError("ids must match the events one to one")

    @property
    def lines(self) -> list[Line]:
        return [
            Line(
                id=line_id,
                start_ms=event.start,
                end_ms=event.end,
                text=event.text,
                style=event.style,
                is_comment=event.is_comment,
            )
            for line_id, event in zip(self.ids, self.ssa.events, strict=True)
        ]

    def __len__(self) -> int:
        return len(self.ids)

    def copy(self) -> Subtitle:
        return Subtitle(copy.deepcopy(self.ssa), self.format, self.encoding, self.ids)
