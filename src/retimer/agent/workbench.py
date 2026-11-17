"""The agent's workbench: what it can look at, and the few things it can change.

The routine steps have run before the agent starts. The workbench holds their
result as a draft plan and finds the places where that draft rests on too
little: lines the fits could not place, blocks with hardly anything behind
them, lines at the edges that have no counterpart, a doubtful verdict on
whether both sides are the same content at all.

The agent never writes a time. It says which line belongs to which place in
the reference, what happens to a line without a counterpart, which fit to go
with, and whether the two sides are the same content. Every one of these is an
adjustment to the fixed pipeline, which then computes the plan as it always
does. So a plan from the agent passes the same engine checks as any other.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from typing import Literal

import numpy as np

from retimer.fit import FitKind, Pin
from retimer.pipeline import Adjustments, Prepared, Proposal, propose
from retimer.plan import RemoveLines
from retimer.reference import ReferenceTimeline, _is_wide
from retimer.text import to_plain

# How many lines or places one answer shows at most.
MAX_SHOWN = 60
MAX_GAP_SHOWN = 40
MAX_TEXT = 120
# A line with nothing said this close to where it lands has no visible counterpart.
NEAR_MS = 1_500
# How well the start of a clause inside a transcribed utterance is known. A
# subtitle line leads or trails the first word of its clause by up to this.
CLAUSE_SLACK_MS = 1_000
# Two subtitles of one film start the same line this far apart at most, because
# each was timed by its own hand and may split the lines differently.
STYLE_MS = 1_500
# Against a transcript, a way to lay lines on places by their distances is shown
# only when the lines also stand out as alike to those places, this much each.
DISTANCE_MIN_LIKENESS = 1.0
# With so little agreement between the two sides, the verdict deserves a second look.
DOUBT_MIN_IN_PLACE = 6
DOUBT_MIN_SHARE = 0.3

_CLAUSE_END = re.compile(r"[.?!…。？！,、;:，；：]+[\"'”’）)」』]*$")
_SENTENCE_END = re.compile(r"[.?!…。？！]+[\"'”’）)」』]*$")
_MIN_CLAUSE_WORDS = 3


class ToolError(ValueError):
    """A request the workbench cannot carry out. The message is for the agent."""


@dataclass(frozen=True)
class Unit:
    """A place in the reference that a line can be paired with.

    For a reference subtitle it is one of its lines. For audio it is a clause
    of an utterance, because a subtitle line often begins in the middle of one.
    """

    index: int
    event: int
    start_ms: int
    end_ms: int
    text: str
    heard: bool = False


@dataclass
class Group:
    """Certain pairs in a row that agree on one timing: (line, place) each, and the offset."""

    pairs: list[tuple[int, int]]
    offset_ms: float


@dataclass
class Run:
    """Lines in a row that found no counterpart, where the draft may be wrong.

    `before` and `after` are the placed lines around them. `first_unit` and
    `last_unit` bound the stretch of the reference between the counterparts of
    those two: both sides keep their order, so the lines of the run can only
    belong there.
    """

    position: Literal["start", "between", "end"]
    line_ids: list[int]
    before: int | None
    after: int | None
    first_unit: int
    last_unit: int
    open: bool = False


def clock(ms: int | float) -> str:
    """A time for the agent to read: minutes, seconds and tenths."""
    total = round(ms / 100)
    sign = "-" if total < 0 else ""
    total = abs(total)
    minutes, tenths = divmod(total, 600)
    hours, minutes = divmod(minutes, 60)
    head = f"{hours}:{minutes:02d}" if hours else f"{minutes:02d}"
    return f"{sign}{head}:{tenths // 10:02d}.{tenths % 10}"


def seconds(ms: int | float) -> str:
    return f"{ms / 1000:+.1f} s"


def quote(text: str) -> str:
    plain = " / ".join(part.strip() for part in to_plain(text).splitlines() if part.strip())
    plain = plain.replace('"', "'")
    if len(plain) > MAX_TEXT:
        plain = plain[: MAX_TEXT - 1] + "…"
    return f'"{plain}"'


def reference_units(timeline: ReferenceTimeline) -> list[Unit]:
    """The places of a reference, in time order."""
    units: list[Unit] = []
    for event_index, event in enumerate(timeline.events):
        words = event.words
        heard = bool(words)
        if len(words) < 2:
            units.append(
                Unit(len(units), event_index, event.start_ms, event.end_ms, event.text, heard)
            )
            continue
        pieces = [[words[0]]]
        for previous, word in zip(words, words[1:], strict=False):
            done = _SENTENCE_END.search(previous.text) or (
                _CLAUSE_END.search(previous.text) and len(pieces[-1]) >= _MIN_CLAUSE_WORDS
            )
            if done:
                pieces.append([word])
            else:
                pieces[-1].append(word)
        separator = "" if _is_wide(event.text) else " "
        for number, piece in enumerate(pieces):
            end = pieces[number + 1][0].start_ms if number + 1 < len(pieces) else event.end_ms
            units.append(
                Unit(
                    index=len(units),
                    event=event_index,
                    start_ms=piece[0].start_ms,
                    end_ms=max(end, piece[0].start_ms + 1),
                    text=separator.join(word.text for word in piece),
                    heard=heard,
                )
            )
    return units


class Workbench:
    """One job on the agent's desk: the draft, the open questions, and the tools."""

    def __init__(self, prepared: Prepared) -> None:
        self.prepared = prepared
        self.units = reference_units(prepared.timeline)
        self._unit_starts = np.array([unit.start_ms for unit in self.units], dtype=np.int64)
        self._lines = {line.id: line for line in prepared.lines}
        self._position = {line.id: index for index, line in enumerate(prepared.lines)}
        self._row = {line.id: row for row, line in enumerate(prepared.matching.lines)}
        self._event_units: dict[int, list[int]] = {}
        for unit in self.units:
            self._event_units.setdefault(unit.event, []).append(unit.index)
        self._option_removed = {
            line_id
            for op in prepared.views.content_ops
            if isinstance(op, RemoveLines)
            for line_id in op.ids
        }
        self.pairs: dict[int, int] = {}
        # Paired lines whose pair says which block they belong to, and not when they start.
        self._eased: set[int] = set()
        self.trim: set[int] = set()
        self.keep: set[int] = set()
        self.fit_kind: FitKind | None = None
        self.same_content: bool | None = None
        self.edges_reviewed = False
        self.baseline = propose(prepared)
        self.draft = self.baseline
        self._firm = self._firm_lines()
        # Where the routine steps found each line they placed: the place, and the time.
        self._home = {
            line_id: unit
            for line_id in self.baseline.matched
            if line_id in self._lines and (unit := self.landing(line_id)[0]) is not None
        }
        self._held = {
            line_id: start
            for line_id in self.baseline.matched
            if line_id in self._lines and (start := self.new_start(line_id)) is not None
        }
        self._open_at_start = self._open_lines()

    # ----- state

    def _timed(self) -> set[int]:
        """The paired lines whose pair says when they start: the first line of each place.

        Several short lines can be said within one place. Only the first of
        them starts where the place starts. The others follow it and move
        with it.
        """
        first: dict[int, int] = {}
        for line_id, unit in self.pairs.items():
            other = first.get(unit)
            if other is None or self._position[line_id] < self._position[other]:
                first[unit] = line_id
        return set(first.values())

    def _slack(self, unit: int) -> int:
        return CLAUSE_SLACK_MS if self.units[unit].heard else 0

    def _pair_slack(self, line_id: int, unit: int) -> int:
        """How loosely the pair of a line says when the line starts."""
        return max(self._slack(unit), STYLE_MS if line_id in self._eased else 0)

    def _at_home(self, line_id: int, unit: int) -> bool:
        """Whether `unit` is where the routine steps placed the line."""
        home = self._home.get(line_id)
        if home is None:
            return False
        if self.units[unit].event == self.units[home].event:
            return True
        apart = abs(self.units[unit].start_ms - self.units[home].start_ms)
        return apart <= max(self.tolerance_ms, self._slack(unit))

    def _pin(self, line_id: int, unit: int) -> Pin:
        start = self._lines[line_id].start_ms
        if self._at_home(line_id, unit):
            # The reader confirms a line the routine steps placed. Where it was
            # placed is measured, and better known than the start of a place.
            return Pin(line_id=line_id, line_start_ms=start, ref_ms=self._held[line_id])
        return Pin(
            line_id=line_id,
            line_start_ms=start,
            ref_ms=self.units[unit].start_ms,
            slack_ms=self._pair_slack(line_id, unit),
        )

    def adjustments(self) -> Adjustments:
        timed = self._timed()
        return Adjustments(
            fit=self.fit_kind,
            pins=[
                self._pin(line_id, unit)
                for line_id, unit in sorted(self.pairs.items())
                if line_id in timed
            ],
            trim=sorted(self.trim),
            keep=sorted(self.keep | (set(self.pairs) - timed)),
            edges_reviewed=self.edges_reviewed,
            same_content=self.same_content,
            held=self._held if self.pairs else {},
        )

    @property
    def changed(self) -> bool:
        return not self.adjustments().empty

    def refresh(self) -> Proposal:
        adjustments = self.adjustments()
        self.draft = self.baseline if adjustments.empty else propose(self.prepared, adjustments)
        return self.draft

    # ----- reading the draft

    @property
    def tolerance_ms(self) -> int:
        chosen = self.draft.alignment.chosen
        return chosen.tolerance_ms if chosen else 500

    def label(self, line_id: int) -> str:
        return self.prepared.label_of.get(line_id, "dialogue")

    def block_of(self, line_id: int) -> int | None:
        start = self._lines[line_id].start_ms
        for index, block in enumerate(self.draft.blocks):
            if block.to_ms is None or start < block.to_ms:
                return index
        return len(self.draft.blocks) - 1 if self.draft.blocks else None

    def new_start(self, line_id: int) -> int | None:
        times = self.draft.new_times.get(line_id)
        return times[0] if times else None

    def nearest_unit(self, ms: int) -> int | None:
        if not self.units:
            return None
        position = int(np.searchsorted(self._unit_starts, ms))
        around = [index for index in (position - 1, position) if 0 <= index < len(self.units)]
        return min(around, key=lambda index: abs(int(self._unit_starts[index]) - ms))

    def landing(self, line_id: int) -> tuple[int | None, int]:
        """The place nearest to where the line starts in the draft, and how far off it is."""
        start = self.new_start(line_id)
        if start is None:
            return None, 0
        unit = self.nearest_unit(start)
        if unit is None:
            return None, 0
        return unit, start - self.units[unit].start_ms

    def lands(self, line_id: int) -> bool:
        unit, off = self.landing(line_id)
        return unit is not None and abs(off) <= self.tolerance_ms

    def unit_of(self, line_id: int) -> int | None:
        """The place a placed line belongs to."""
        if line_id in self.pairs:
            return self.pairs[line_id]
        if self.unbuilt:
            return next(
                (unit for group in self.groups() for line, unit in group.pairs if line == line_id),
                None,
            )
        return self.landing(line_id)[0]

    @property
    def unbuilt(self) -> bool:
        """Whether the two sides are the same content and still no timing could be built.

        That happens when the lines that found their counterpart agree on
        timings that cannot follow each other: the lines between them, which
        found none, would run into one side or the other.
        """
        alignment = self.draft.alignment
        return (
            not self.draft.blocks
            and alignment.chosen is not None
            and alignment.verdict == "aligned"
            and self.same_content is not False
        )

    def groups(self) -> list[Group]:
        """The certain pairs in file order, as stretches that agree on one timing.

        A pair alone between two stretches is taken for a wrong match. It is all
        there is to go on when no timing could be built.
        """
        chosen = self.draft.alignment.chosen
        if chosen is None:
            return []
        found = sorted(
            (
                anchor
                for anchor in self.draft.alignment.anchors
                if anchor.ref_index >= 0 and anchor.target_id in self._position
            ),
            key=lambda anchor: self._position[anchor.target_id],
        )
        raw: list[tuple[list[tuple[int, int]], list[float]]] = []
        for anchor in found:
            offset = anchor.ref_start_ms - anchor.target_start_ms * chosen.rate
            unit = self.nearest_unit(anchor.ref_start_ms)
            if unit is None:
                continue
            if raw and abs(offset - statistics.median(raw[-1][1])) <= chosen.tolerance_ms:
                raw[-1][0].append((anchor.target_id, unit))
                raw[-1][1].append(offset)
            else:
                raw.append(([(anchor.target_id, unit)], [offset]))
        groups: list[Group] = []
        for pairs, offsets in raw:
            if len(pairs) < 2:
                continue
            offset = statistics.median(offsets)
            if groups and abs(groups[-1].offset_ms - offset) <= chosen.tolerance_ms:
                groups[-1].pairs.extend(pairs)
            else:
                groups.append(Group(pairs=list(pairs), offset_ms=offset))
        return groups

    def _runs_between_groups(self) -> list[Run]:
        """The lines outside every group of certain pairs, when no timing could be built."""
        groups = self.groups()
        if not groups or not self.units:
            return []
        inside: set[int] = set()
        for group in groups:
            low = self._position[group.pairs[0][0]]
            high = self._position[group.pairs[-1][0]]
            inside |= {line.id for line in self.prepared.lines[low : high + 1]}
        edges: list[tuple[int | None, int | None, int, int]] = [
            (None, groups[0].pairs[0][0], 0, groups[0].pairs[0][1] - 1)
        ]
        for left, right in zip(groups, groups[1:], strict=False):
            edges.append(
                (left.pairs[-1][0], right.pairs[0][0], left.pairs[-1][1] + 1, right.pairs[0][1] - 1)
            )
        edges.append(
            (groups[-1].pairs[-1][0], None, groups[-1].pairs[-1][1] + 1, len(self.units) - 1)
        )
        runs = []
        for before, after, first, last in edges:
            low = -1 if before is None else self._position[before]
            high = len(self.prepared.lines) if after is None else self._position[after]
            line_ids = [
                line_id
                for line_id in self._judged()
                if low < self._position[line_id] < high
                and line_id not in inside
                and self.label(line_id) != "credit"
            ]
            if not line_ids:
                continue
            position: Literal["start", "between", "end"] = (
                "start" if before is None else "end" if after is None else "between"
            )
            runs.append(Run(position, line_ids, before, after, first, last, open=True))
        return runs

    def status(self, line_id: int) -> str:
        if line_id in self._option_removed:
            return "removed by the options"
        report = self.draft.result.report
        if line_id in self.trim and line_id in self.draft.removed:
            return "removed, your decision"
        if line_id in report.trimmed:
            return "removed by the rules"
        if line_id in self.draft.removed:
            return "removed, would be over before zero"
        if line_id in self.pairs:
            return "paired by you"
        if line_id in self.draft.matched:
            return "placed"
        if line_id in self.prepared.dialogue_ids or self.label(line_id) == "credit":
            return "no counterpart found"
        return "moves with its block"

    def _judged(self) -> list[int]:
        """Lines that should have a counterpart: dialogue, and credits at the edges."""
        spoken = self.prepared.dialogue_ids | set(self.pairs)
        return [
            line.id
            for line in self.prepared.lines
            if line.id not in self._option_removed
            and (line.id in spoken or self.label(line.id) == "credit")
        ]

    def runs(self) -> list[Run]:
        """The stretches of unplaced lines that the draft does not account for."""
        draft = self.draft
        if self.unbuilt:
            return self._runs_between_groups()
        if not draft.blocks or not self.units:
            return []
        matched = self._placed()
        grouped: list[tuple[int | None, list[int], int | None]] = []
        current: list[int] = []
        before: int | None = None
        for line_id in self._judged():
            if line_id in matched:
                if current:
                    grouped.append((before, current, line_id))
                    current = []
                before = line_id
            else:
                current.append(line_id)
        if current:
            grouped.append((before, current, None))

        runs = []
        for before, line_ids, after in grouped:
            if before is None and after is None:
                continue
            position: Literal["start", "between", "end"] = (
                "start" if before is None else "end" if after is None else "between"
            )
            if position == "between":
                line_ids = [line_id for line_id in line_ids if self.label(line_id) != "credit"]
                # Lines held by placed lines of one block on both sides are where they belong.
                if not line_ids or self.block_of(before) == self.block_of(after):
                    continue
            first = 0 if before is None else self._past(before, self.unit_of(before) or 0, draft)
            last = len(self.units) - 1 if after is None else (self.unit_of(after) or 0) - 1
            run = Run(position, line_ids, before, after, first, last)
            run.open = any(self._line_open(line_id, position) for line_id in line_ids)
            runs.append(run)
        return runs

    def _placed(self) -> set[int]:
        """The lines that have their counterpart in the draft."""
        timed = self._timed()
        first = {self.pairs[line_id]: line_id for line_id in timed}
        return self.draft.matched | {
            line_id
            for line_id, unit in self.pairs.items()
            if line_id not in timed and first[unit] in self.draft.matched
        }

    def _past(self, line_id: int, unit: int, proposal: Proposal) -> int:
        """The first place after `unit` that the line placed there does not cover.

        A line often spans several clauses of one utterance. Those that begin
        while the line is still shown are part of it, not of the next line.
        """
        times = proposal.new_times.get(line_id)
        end = times[1] if times else self.units[unit].end_ms
        following = unit + 1
        while (
            following < len(self.units)
            and self.units[following].event == self.units[unit].event
            and self.units[following].start_ms < end
        ):
            following += 1
        return following

    def _firm_lines(self) -> list[tuple[int, int, int]]:
        """Lines the routine steps placed in blocks that stand: position, id and place."""
        weak = set(self.weak_blocks())
        firm = []
        for line_id in self._judged():
            if line_id in self.baseline.matched and self.block_of(line_id) not in weak:
                unit = self.landing(line_id)[0]
                if unit is not None:
                    firm.append((self._position[line_id], line_id, unit))
        return firm

    def _open_lines(self) -> set[int] | None:
        """The lines a reader may pair. None means any line.

        Open are the lines the draft does not account for: unplaced lines at
        the edges and between blocks, and all lines of a block that rests on
        little. A line between placed lines of one block that stands moves
        with them, whatever the reference says about it elsewhere.
        """
        if self.baseline.alignment.verdict != "aligned" or not self.baseline.blocks:
            return None
        weak = set(self.weak_blocks())
        lines = {line_id for line_id in self._judged() if self.block_of(line_id) in weak}
        for run in self.runs():
            lines |= set(run.line_ids)
        return lines

    def _bounds(self, line_id: int) -> tuple[int, int]:
        """The places a line can be paired with, given the order of the lines that stand."""
        position = self._position[line_id]
        low, high = 0, len(self.units) - 1
        for other_position, other, unit in self._firm:
            if other == line_id or other in self.pairs:
                continue
            if other_position < position:
                low = self._past(other, unit, self.baseline)
            elif other_position > position:
                high = unit - 1
                break
        return low, high

    def _stray(self) -> dict[int, tuple[int, str]]:
        """Paired lines whose pair opens a block that cannot be one: how far it moves them, and why.

        A cut moves everything after it. A block that only pairs stand for,
        between two blocks that move by the same amount, would take two cuts
        that cancel each other out. What happens instead is that the time the
        reference gives for a place is wrong, as in a stretch of a transcript
        that the speech model dated badly.

        The other case is a block that one pair with a heard place stands for
        and nothing else. A single clause of a transcript can be dated wrongly,
        and a short line can be said in more than one place, so one such pair
        is too little to move lines on. With the true pairs of the benchmark
        this rule costs nothing.

        A line of a reference subtitle is timed exactly, so one pair with it
        can be a block. But when that block would differ from its neighbour by
        no more than two subtitles differ on a single line anyway, the pair
        only says which block the line belongs to. It is kept as that, and
        reported here as "style".
        """
        blocks = self.draft.blocks
        timed = self._timed()
        if len(blocks) < 2 or not timed:
            return {}
        members: list[list[int]] = [[] for _ in blocks]
        own = [0] * len(blocks)
        for line_id in self._judged():
            index = self.block_of(line_id)
            if index is None:
                continue
            if line_id in timed:
                members[index].append(line_id)
            elif line_id in self._held:
                # Only what the routine steps placed stands on its own. A line that
                # matches by its words at the timing of a pair proves nothing more
                # than the pair does.
                own[index] += 1
        slack = max(self.tolerance_ms, *(self._slack(self.pairs[line_id]) for line_id in timed))
        stray: dict[int, tuple[int, str]] = {}
        for left in range(len(blocks) - 2):
            for right in range(left + 2, len(blocks)):
                if own[right - 1] or not members[right - 1]:
                    break
                if abs(blocks[left].shift_ms - blocks[right].shift_ms) <= 2 * slack:
                    for index in range(left + 1, right):
                        for line_id in members[index]:
                            stray[line_id] = (
                                blocks[index].shift_ms - blocks[left].shift_ms,
                                "inside",
                            )
        for index, block in enumerate(blocks):
            if own[index] or len(members[index]) != 1 or members[index][0] in stray:
                continue
            line_id = members[index][0]
            around = [blocks[other] for other in (index - 1, index + 1) if 0 <= other < len(blocks)]
            nearest = min(around, key=lambda other: abs(other.shift_ms - block.shift_ms))
            moved = block.shift_ms - nearest.shift_ms
            if self._slack(self.pairs[line_id]):
                stray[line_id] = (moved, "lone")
            elif abs(moved) <= STYLE_MS and line_id not in self._eased:
                stray[line_id] = (moved, "style")
        return stray

    def _beyond_audio(self, line_id: int) -> bool:
        timeline = self.prepared.timeline
        start = self.new_start(line_id)
        return (
            timeline.kind in ("audio", "both")
            and start is not None
            and (start < 0 or (timeline.duration_ms is not None and start >= timeline.duration_ms))
        )

    def _line_open(self, line_id: int, position: str) -> bool:
        """Whether what the draft does with an unplaced line still needs a reader."""
        if position == "between":
            return not self.lands(line_id)
        if line_id in self.trim or line_id in self.keep:
            return False
        if line_id in self.draft.result.report.trimmed:
            # Credits outside the reference and lines past the end of the audio are clear.
            return not (self.label(line_id) == "credit" or self._beyond_audio(line_id))
        if line_id in self.draft.removed:
            return False
        return not self.lands(line_id)

    def edge_lines(self) -> set[int]:
        return {
            line_id for run in self.runs() if run.position != "between" for line_id in run.line_ids
        } | set(self.draft.result.report.trimmed)

    def weak_blocks(self) -> list[int]:
        bar = self.prepared.settings.min_block_confidence
        return [index for index, block in enumerate(self.draft.blocks) if block.confidence < bar]

    def verdict_doubt(self) -> str | None:
        """Why the verdict on same content deserves a second look, if it does."""
        alignment = self.baseline.alignment
        result = alignment.matching.result
        comparable = min(result.eligible_lines, result.eligible_events)
        in_place = len(alignment.chosen.explained(alignment.anchors)) if alignment.chosen else 0
        share = in_place / comparable if comparable else 0.0
        if alignment.verdict == "too_little_text":
            return "There is too little text on one side to tell by matching alone."
        if alignment.verdict == "no_match":
            if in_place >= 2:
                return (
                    f"Matching found {len(alignment.anchors)} pairs that mean the same, but only "
                    f"{in_place} of them agree on one timing. The rules call that other content."
                )
            return None
        if in_place < DOUBT_MIN_IN_PLACE or share < DOUBT_MIN_SHARE:
            return (
                f"Only {in_place} of {comparable} comparable lines found a certain "
                "counterpart. The rules call that the same content, by a small margin."
            )
        return None

    def open_questions(self) -> list[str]:
        """One line per thing the draft leaves open. Empty means the draft stands on its own."""
        questions = []
        doubt = self.verdict_doubt() if self.same_content is None and not self.pairs else None
        if doubt:
            questions.append(f"Same content? {doubt}")
        if self.unbuilt:
            questions.append(
                "No timing could be built from the lines that found their counterpart."
            )
        for run in self.runs():
            if run.open:
                questions.append(self._run_title(run))
        for index in self.weak_blocks():
            block = self.draft.blocks[index]
            questions.append(
                f"Block B{index + 1} rests on little: {block.matched} of {block.lines} lines "
                f"placed, confidence {block.confidence:.2f}."
            )
        return questions

    # ----- rendering

    def _line(self, line_id: int, landing: bool = True) -> str:
        line = self._lines[line_id]
        parts = [f"L{line_id}", clock(line.start_ms), self.label(line_id), quote(line.text)]
        if not landing or self.unbuilt:
            # Without a timing there is nowhere a line lands.
            return " ".join(parts)
        status = self.status(line_id)
        if status.startswith("removed"):
            return " ".join(parts) + f"  [{status}]"
        return " ".join(parts) + f"  [{status}] {self._landing_text(line_id)}"

    def _landing_text(self, line_id: int) -> str:
        unit, off = self.landing(line_id)
        if self._beyond_audio(line_id):
            return "lands past the end of the audio"
        if unit is None or abs(off) > NEAR_MS:
            return "lands where the reference has nothing"
        target = self.units[unit]
        if abs(off) <= self.tolerance_ms:
            return f"lands on R{unit} {quote(target.text)}"
        return f"lands {seconds(off)} from R{unit} {quote(target.text)}"

    def _unit(self, index: int) -> str:
        unit = self.units[index]
        return f"R{index} {clock(unit.start_ms)} {quote(unit.text)}"

    def _units(
        self, first: int, last: int, limit: int = MAX_GAP_SHOWN, near: str = "both"
    ) -> list[str]:
        """The places from `first` to `last`. Of a long stretch, mostly the end named by `near`.

        The lines of a run after the last placed line follow it closely more
        often than not, so most of what is shown is the beginning of the
        stretch, and for a run before the first placed line its end.
        """
        if last < first:
            return []
        indexes = list(range(first, last + 1))
        if len(indexes) <= limit:
            return [self._unit(index) for index in indexes]
        share = {"first": 0.75, "last": 0.25}.get(near, 0.5)
        head, tail = indexes[: round(limit * share)], indexes[-round(limit * (1 - share)) :]
        return [
            *[self._unit(index) for index in head],
            f"… {len(indexes) - len(head) - len(tail)} more places, R{head[-1] + 1} to "
            f"R{tail[0] - 1}; read_reference shows them …",
            *[self._unit(index) for index in tail],
        ]

    def _pair_text(self, line_id: int) -> str:
        unit = self.unit_of(line_id)
        line = self._line(line_id, landing=False)
        return f"{line} = {self._unit(unit)}" if unit is not None else line

    def _run_title(self, run: Run) -> str:
        span = (
            f"L{run.line_ids[0]}"
            if len(run.line_ids) == 1
            else f"L{run.line_ids[0]} to L{run.line_ids[-1]}"
        )
        count = "1 line" if len(run.line_ids) == 1 else f"{len(run.line_ids)} lines"
        if run.position == "start":
            return f"{count} before the first placed line ({span}) found no counterpart."
        if run.position == "end":
            return f"{count} after the last placed line ({span}) found no counterpart."
        sit = "It sits" if len(run.line_ids) == 1 else "They sit"
        if self.unbuilt:
            return (
                f"{count} ({span}) found no counterpart. {sit} between two stretches of "
                "placed lines that have different timings."
            )
        return (
            f"{count} ({span}) found no counterpart. {sit} between block "
            f"B{self.block_of(run.before) + 1} and block B{self.block_of(run.after) + 1}."
        )

    def render_run(self, run: Run) -> str:
        out = [self._run_title(run)]
        if run.before is not None:
            out.append(f"  Placed before them: {self._pair_text(run.before)}")
        if run.after is not None:
            out.append(f"  Placed after them:  {self._pair_text(run.after)}")
        out.append(
            "  The lines:" if self.unbuilt else "  The lines, and where the draft puts them:"
        )
        out += [f"    {self._line(line_id)}" for line_id in run.line_ids[:MAX_SHOWN]]
        if len(run.line_ids) > MAX_SHOWN:
            out.append(f"    … {len(run.line_ids) - MAX_SHOWN} more; read_lines shows them …")
        near = {"start": "last", "end": "first"}.get(run.position, "both")
        places = self._units(run.first_unit, run.last_unit, near=near)
        if run.position == "between":
            where = "between those two"
        elif run.position == "start":
            where = "before the first placed line"
        else:
            where = "after the last placed line"
        if places:
            out.append(f"  What the reference holds {where}:")
            out += [f"    {place}" for place in places]
        else:
            out.append(f"  The reference holds nothing {where}.")
        ways = self.by_distance(run)
        if ways:
            out.append(
                "  Going by the distances between the lines alone, they fit that stretch like "
                "this. Read whether the lines say the same before you pair them:"
            )
            for fitted in ways:
                pairs = ", ".join(f"L{line_id} = R{unit}" for line_id, unit in fitted)
                count = "all" if len(fitted) == len(run.line_ids) else f"{len(fitted)} of"
                out.append(f"    {pairs}  ({count} {len(run.line_ids)} lines)")
        return "\n".join(out)

    def by_distance(self, run: Run, limit: int = 3) -> list[list[tuple[int, int]]]:
        """Ways to lay the lines of a run on the places of its stretch that keep their distances.

        Lines of one block are as far apart in the reference as in the target.
        So one shift has to bring several of them onto the start of a place.
        That looks at when things are said. Among the ways it finds, those
        whose lines also resemble their places come first, and against a
        transcript, where a song gives many places to land on by chance, only
        those are kept. It stays a hint for a reader, who checks the words.
        """
        if len(run.line_ids) < 2 or run.last_unit < run.first_unit:
            return []
        rate = self.draft.rate
        starts = {line_id: self._lines[line_id].start_ms * rate for line_id in run.line_ids}
        units = self.units[run.first_unit : run.last_unit + 1]
        places = np.array([unit.start_ms for unit in units], dtype=np.float64)
        heard = units[0].heard
        reach = max(self.tolerance_ms, CLAUSE_SLACK_MS if heard else 0)
        standing = self.prepared.matching.standing

        def alike(line_id: int, unit: Unit) -> float:
            row = self._row.get(line_id)
            if row is None or unit.event >= standing.shape[1]:
                return 0.0
            return max(0.0, float(standing[row, unit.event]))

        found: dict[tuple[tuple[int, int], ...], tuple[float, float]] = {}
        for line_id in run.line_ids:
            for shift in places - starts[line_id]:
                fitted, off, likeness = [], 0.0, 0.0
                last = -1
                for other in run.line_ids:
                    landing = starts[other] + shift
                    index = int(np.argmin(np.abs(places - landing)))
                    away = abs(float(places[index]) - landing)
                    if away <= reach and index > last:
                        fitted.append((other, units[index].index))
                        off += away
                        likeness += alike(other, units[index])
                        last = index
                if len(fitted) < max(2, (len(run.line_ids) + 1) // 2):
                    continue
                if heard and likeness < DISTANCE_MIN_LIKENESS * len(fitted):
                    continue
                key = tuple(fitted)
                if key not in found or off < found[key][1]:
                    found[key] = (likeness, off)
        ranked = sorted(found.items(), key=lambda item: (-item[1][0], -len(item[0]), item[1][1]))
        ways: list[list[tuple[int, int]]] = []
        for fitted, _ in ranked:
            # A way that is part of a longer one says nothing new.
            if any(set(fitted) <= set(other) for other in ways):
                continue
            ways.append(list(fitted))
            if len(ways) == limit:
                break
        return ways

    def render_block(self, index: int) -> str:
        block = self.draft.blocks[index]
        out = [
            f"Block B{index + 1} rests on little: {block.matched} of {block.lines} lines placed, "
            f"confidence {block.confidence:.2f}. Its lines, and where the draft puts them:"
        ]
        inside = [
            line_id
            for line_id in self._judged()
            if self.block_of(line_id) == index and self.label(line_id) != "credit"
        ]
        out += [f"    {self._line(line_id)}" for line_id in inside[:MAX_SHOWN]]
        landed = [self.landing(line_id)[0] for line_id in inside]
        landed = [unit for unit in landed if unit is not None]
        if landed:
            first, last = max(0, min(landed) - 2), min(len(self.units) - 1, max(landed) + 2)
            out.append("  The reference around there:")
            out += [f"    {place}" for place in self._units(first, last)]
        return "\n".join(out)

    def render_verdict(self) -> str:
        """Material to judge by reading whether both sides are the same content."""
        alignment = self.baseline.alignment
        out = []
        anchors = sorted(alignment.anchors, key=lambda anchor: -anchor.score)[:8]
        if anchors:
            out.append("  The pairs matching is most sure of:")
            for anchor in sorted(anchors, key=lambda anchor: anchor.target_start_ms):
                event = alignment.matching.events[anchor.ref_index]
                line = self._line(anchor.target_id, landing=False)
                out.append(f"    {line} ~ {clock(event.start_ms)} {quote(event.text)}")
        spoken = [line.id for line in self.prepared.lines if line.id in self.prepared.dialogue_ids]
        out.append("  How the target begins:")
        out += [f"    {self._line(line_id, landing=False)}" for line_id in spoken[:10]]
        out.append("  How the reference begins:")
        out += [f"    {place}" for place in self._units(0, min(len(self.units), 12) - 1)]
        return "\n".join(out)

    def render_draft(self) -> str:
        report = self.draft.result.report
        out = [f"Draft plan, outcome {report.outcome}."]
        if report.rate_name:
            out.append(f"  Rate change over the whole file: {report.rate_name}.")
        ordered = [line.id for line in self.prepared.lines]
        for index, block in enumerate(self.draft.blocks):
            inside = [line_id for line_id in ordered if self.block_of(line_id) == index]
            span = f"L{inside[0]} to L{inside[-1]}" if inside else "no lines"
            weak = "  <- rests on little" if index in self.weak_blocks() else ""
            out.append(
                f"  B{index + 1}: {span}, moved {seconds(block.shift_ms)}, {block.matched} of "
                f"{block.lines} dialogue lines placed, confidence {block.confidence:.2f}{weak}"
            )
        if not self.draft.blocks:
            out.append("  No timing is changed.")
        if report.score and report.score.anchors:
            out.append(
                f"  Certain pairs in place: {self.draft.in_place} of {report.score.anchors} "
                f"(the plan you started from: {self.baseline.in_place})."
            )
        if report.trimmed:
            out.append("  Removed at the edges: " + ", ".join(f"L{i}" for i in report.trimmed))
        for warning in report.warnings:
            out.append(f"  Note: {warning}")
        return "\n".join(out)

    def render_fits(self) -> str:
        chosen = self.draft.alignment.chosen
        out = []
        for fit in self.draft.alignment.fits:
            mark = "  <- in use" if chosen is not None and fit.kind == chosen.kind else ""
            rate = f", {fit.rate_name}" if fit.rate_name else ""
            out.append(
                f"  {fit.kind}: {len(fit.blocks)} block(s){rate}, {fit.matched} of {fit.lines} "
                f"lines placed, score {fit.score:.1f}{mark}"
            )
        return "\n".join(out) or "  No fit was possible."

    def render_unbuilt(self) -> str:
        out = [
            "No timing could be built. The lines that found their counterpart agree on "
            "these timings, one stretch of the file each:"
        ]
        for group in self.groups():
            (first, first_unit), (last, last_unit) = group.pairs[0], group.pairs[-1]
            out.append(
                f"    L{first} to L{last}: {len(group.pairs)} pairs, {seconds(group.offset_ms)} "
                f"(L{first} = R{first_unit}, L{last} = R{last_unit})"
            )
        out.append(
            "  The lines between two stretches found no counterpart, and whichever of the two "
            "timings they take, they run into the other stretch. They are usually a block of "
            "their own. Pair them with their places and the timing is built again."
        )
        return "\n".join(out)

    def render_questions(self) -> str:
        parts = []
        if self.unbuilt:
            parts.append(self.render_unbuilt())
        if self.same_content is None and not self.pairs:
            doubt = self.verdict_doubt()
            if doubt:
                parts.append(f"Same content? {doubt}\n{self.render_verdict()}")
        parts += [self.render_run(run) for run in self.runs() if run.open]
        parts += [self.render_block(index) for index in self.weak_blocks()]
        if not parts:
            return "Nothing is open."
        return "\n\n".join(f"Q{number}. {part}" for number, part in enumerate(parts, 1))

    def dossier(self) -> str:
        prepared = self.prepared
        timeline = prepared.timeline
        labels: dict[str, int] = {}
        for line in prepared.lines:
            labels[self.label(line.id)] = labels.get(self.label(line.id), 0) + 1
        counts = ", ".join(f"{count} {label}" for label, count in sorted(labels.items()))
        lines = prepared.lines
        kind = {
            "audio": "a transcript of the audio",
            "subtitle": "a subtitle that is in sync",
            "both": "a subtitle that is in sync, checked against the audio",
        }[timeline.kind]
        language = f", language {timeline.language}" if timeline.language else ""
        result = self.baseline.alignment.matching.result
        options = prepared.options
        out = [
            "JOB",
            f"Target: {len(lines)} lines ({counts}), ids L{lines[0].id} to L{lines[-1].id}, "
            f"from {clock(lines[0].start_ms)} to {clock(lines[-1].end_ms)}."
            if lines
            else "Target: no lines.",
            f"Reference: {kind}{language}. {len(self.units)} places, R0 to "
            f"R{len(self.units) - 1}"
            + (
                f", from {clock(self.units[0].start_ms)} to {clock(self.units[-1].end_ms)}."
                if self.units
                else "."
            )
            + (
                f" The audio is {clock(timeline.duration_ms)} long." if timeline.duration_ms else ""
            ),
            "Lines at the edges that the reference does not contain "
            + ("are to be removed." if options.drop_unmatched else "are to be kept (option)."),
            "",
            "WHAT THE ROUTINE STEPS FOUND",
            f"Matching by meaning: {len(result.anchors)} certain pairs among "
            f"{result.eligible_lines} comparable lines and {result.eligible_events} "
            "comparable reference lines.",
            "Fits:",
            self.render_fits(),
            self.render_draft(),
            "",
            "OPEN QUESTIONS",
            self.render_questions(),
        ]
        return "\n".join(out)

    def _after_change(self, head: list[str]) -> str:
        out = [*head, "", self.render_draft()]
        still = self.open_questions()
        out += ["", "Still open:" if still else "Nothing is open any more."]
        out += [f"  - {question}" for question in still]
        return "\n".join(out)

    # ----- tools

    def _check_line(self, line_id: int) -> int:
        if line_id not in self._lines:
            raise ToolError(f"There is no line L{line_id}.")
        return line_id

    def _check_unit(self, unit: int) -> int:
        if not 0 <= unit < len(self.units):
            raise ToolError(
                f"There is no place R{unit}. The reference has R0 to R{len(self.units) - 1}."
            )
        return unit

    def read_lines(self, first_line: int, last_line: int) -> str:
        low = self._position[self._check_line(first_line)]
        high = self._position[self._check_line(last_line)]
        low, high = min(low, high), max(low, high)
        shown = self.prepared.lines[low : high + 1]
        out = [self._line(line.id) for line in shown[:MAX_SHOWN]]
        if len(shown) > MAX_SHOWN:
            out.append(f"… {len(shown) - MAX_SHOWN} more lines; ask for a smaller range …")
        return "\n".join(out)

    def read_reference(self, first_place: int, last_place: int) -> str:
        low, high = sorted((self._check_unit(first_place), self._check_unit(last_place)))
        out = [self._unit(index) for index in range(low, min(high, low + MAX_SHOWN - 1) + 1)]
        if high - low + 1 > MAX_SHOWN:
            out.append(f"… {high - low + 1 - MAX_SHOWN} more places; ask for a smaller range …")
        return "\n".join(out)

    def find_counterparts(self, lines: list[int]) -> str:
        """For each line, the reference lines closest to it in meaning."""
        standing = self.prepared.matching.standing
        out = []
        for line_id in lines[:12]:
            self._check_line(line_id)
            out.append(self._line(line_id))
            row = self._row.get(line_id)
            if row is None or standing.shape[1] == 0:
                out.append("    was not compared by meaning (not a dialogue line)")
                continue
            best = np.argsort(-standing[row])[:4]
            for event in best:
                if standing[row, event] <= 0:
                    continue
                for unit in self._event_units.get(int(event), []):
                    out.append(f"    {self._unit(unit)}  (stands out {standing[row, event]:.1f})")
        return "\n".join(out)

    def pair_lines(self, pairs: list[dict]) -> str:
        """Say which line belongs to which place. Replaces earlier pairs of the same lines."""
        scope = self._open_at_start
        if scope is not None:
            scope = scope | set(self.pairs) | {i for run in self.runs() for i in run.line_ids}
        wanted = dict(self.pairs)
        head = []
        taken: list[tuple[int, int]] = []
        for pair in pairs:
            line_id = self._check_line(int(pair["line"]))
            unit = self._check_unit(int(pair["place"]))
            if scope is not None and line_id not in scope:
                block = self.block_of(line_id)
                where = f"block B{block + 1}" if block is not None else "its block"
                head.append(
                    f"L{line_id} = R{unit}: not taken. L{line_id} is not open: it sits in {where} "
                    "between lines that are placed there, and moves with them."
                )
                continue
            home = self._home.get(line_id)
            if home is not None and not self._at_home(line_id, unit):
                head.append(
                    f"L{line_id} = R{unit}: not taken. L{line_id} is placed: its words were "
                    f"found at R{home}, at the timing of its block. The same words at "
                    "another place do not move it."
                )
                continue
            low, high = self._bounds(line_id)
            if not low <= unit <= high:
                room = (
                    f"can only be at R{low} to R{high}"
                    if low <= high
                    else "has no place left between them"
                )
                head.append(
                    f"L{line_id} = R{unit}: not taken. By the order of the placed lines around "
                    f"it, L{line_id} {room}."
                )
                continue
            if self.pairs.get(line_id) != unit:
                self._eased.discard(line_id)
            wanted[line_id] = unit
            taken.append((line_id, unit))
        ordered = sorted(wanted.items(), key=lambda item: self._position[item[0]])
        for (line_a, unit_a), (line_b, unit_b) in zip(ordered, ordered[1:], strict=False):
            if unit_b < unit_a:
                raise ToolError(
                    f"L{line_a} = R{unit_a} and L{line_b} = R{unit_b} cross: the later line "
                    "would be said before the earlier one. Both files keep their order, so "
                    "one of the two pairs is wrong. Nothing was changed."
                )
        self.pairs = wanted
        dropped = self._settle()
        timed = self._timed()
        for line_id, unit in taken:
            if line_id in dropped:
                continue
            start = self.new_start(line_id)
            off = None if start is None else start - self.units[unit].start_ms
            if line_id in self.draft.removed:
                head.append(f"L{line_id} = R{unit}: the line is removed in this draft.")
            elif line_id not in timed:
                head.append(f"L{line_id} = R{unit}: taken. It follows another line in that place.")
            elif self._at_home(line_id, unit) and line_id in self.draft.matched:
                head.append(f"L{line_id} = R{unit}: in place, as the draft had it.")
            elif line_id in self._eased:
                block = self.block_of(line_id)
                head.append(
                    f"L{line_id} = R{unit}: taken. The line goes with block B{(block or 0) + 1} "
                    f"and starts {seconds(off or 0)} from the place. Two subtitles time one "
                    "line that far apart, so one pair is too little to move it alone."
                )
            elif off is not None and abs(off) <= max(
                self.tolerance_ms, self._pair_slack(line_id, unit)
            ):
                head.append(f"L{line_id} = R{unit}: in place ({seconds(off)} off).")
            else:
                head.append(
                    f"L{line_id} = R{unit}: NOT in place, the line lands {seconds(off or 0)} "
                    "from it. No block could be built that keeps this pair and the lines "
                    "around it in order."
                )
        head += self._dropped_text(dropped)
        head += self._apart(taken)
        return self._after_change(head)

    def _settle(self) -> dict[int, tuple[int, int, str]]:
        """Rebuild the draft, and take back pairs that open a block that cannot be one."""
        self.refresh()
        dropped: dict[int, tuple[int, int, str]] = {}
        for _ in range(4):
            stray = self._stray()
            if not stray:
                break
            # A pair that stands alone next to pairs on wrongly timed places may
            # be fine once those are gone, so they go first.
            inside = {line_id: found for line_id, found in stray.items() if found[1] == "inside"}
            for line_id, (moved, why) in (inside or stray).items():
                if why == "style":
                    self._eased.add(line_id)
                else:
                    dropped[line_id] = (self.pairs.pop(line_id), moved, why)
            self.refresh()
        self._eased &= set(self.pairs)
        return dropped

    def _dropped_text(self, dropped: dict[int, tuple[int, int, str]]) -> list[str]:
        out = []
        for line_id, (unit, moved, why) in sorted(dropped.items()):
            if why == "inside":
                out.append(
                    f"L{line_id} = R{unit}: not taken. It would move the line {seconds(moved)} "
                    "against the lines before and after it, which are in one block. A cut "
                    f"moves everything after it, so the time the reference gives for R{unit} "
                    "is probably wrong. The line stays with its block."
                )
            else:
                out.append(
                    f"L{line_id} = R{unit}: not taken. It would move this one line "
                    f"{seconds(moved)} against the block next to it, and one line heard in a "
                    "transcript is too little for a block of its own. The line stays with "
                    "its block, unless a line next to it can be paired as well."
                )
        return out

    def _apart(self, taken: list[tuple[int, int]]) -> list[str]:
        """A warning when lines paired in one call ended up in different blocks.

        Lines that follow each other in one scene keep their distances. When
        two of them need different blocks, one of the two places may be the
        wrong instance of a line that is said more than once.
        """
        timed = self._timed()
        lines = [line_id for line_id, _ in taken if line_id in timed and line_id in self.pairs]
        lines.sort(key=lambda line_id: self._position[line_id])
        notes = []
        for first, second in zip(lines, lines[1:], strict=False):
            if self._position[second] - self._position[first] != 1:
                continue
            one, other = self.block_of(first), self.block_of(second)
            if one is None or other is None or one == other:
                continue
            gap = self.draft.blocks[other].shift_ms - self.draft.blocks[one].shift_ms
            notes.append(
                f"Note: L{first} and L{second} follow each other but now sit in different "
                f"blocks, {seconds(gap)} apart. If they are one scene, their places are as far "
                "apart as the lines are, and one of the two pairs is on the wrong place."
            )
        return notes

    def unpair_lines(self, lines: list[int]) -> str:
        for line_id in lines:
            self.pairs.pop(self._check_line(line_id), None)
        dropped = self._settle()
        head = [f"Unpaired: {', '.join(f'L{i}' for i in lines)}.", *self._dropped_text(dropped)]
        return self._after_change(head)

    def set_edge_lines(self, remove: list[int], keep: list[int]) -> str:
        """Decide about lines at the start or end of the file that have no counterpart."""
        allowed = self.edge_lines()
        for line_id in [*remove, *keep]:
            self._check_line(line_id)
            if line_id not in allowed:
                raise ToolError(
                    f"L{line_id} is not a line without counterpart at the start or the end "
                    "of the file. Only those can be removed or kept. Nothing was changed."
                )
        self.trim = (self.trim | set(remove)) - set(keep)
        self.keep = (self.keep | set(keep)) - set(remove)
        self.edges_reviewed = True
        self.refresh()
        head = []
        if remove:
            head.append(f"Removed: {', '.join(f'L{i}' for i in remove)}.")
        if keep:
            head.append(f"Kept: {', '.join(f'L{i}' for i in keep)}.")
        if remove and not self.prepared.options.drop_unmatched:
            head.append("The job's options say to keep such lines, so nothing is removed.")
        return self._after_change(head)

    def choose_fit(self, kind: str) -> str:
        available = [fit.kind for fit in self.baseline.alignment.fits]
        if kind == "best":
            self.fit_kind = None
        elif kind in available:
            self.fit_kind = kind  # type: ignore[assignment]
        else:
            raise ToolError(
                f"There is no fit of kind {kind}. Available: {', '.join(available) or 'none'}."
            )
        self.refresh()
        return self._after_change([f"Fit: {kind}."])
