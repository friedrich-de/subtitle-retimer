"""Which of several reference subtitles a job is synced against.

A video file often holds more than one subtitle track: the dialogue in full, a
track of signs and songs, the dialogue again in another language. What a track
is called says little, because a title is free text. So every track is read
and matched against the target, and the one in which most of the target's
dialogue finds its counterpart is taken. The labels of a track only decide
between tracks that fit equally well.

The choice is a step before the pipeline: it settles which subtitle the
pipeline gets, and the pipeline then runs as it does for one reference.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from retimer.classify import LineLabel, classify_lines
from retimer.match import Matching
from retimer.model import Subtitle
from retimer.reference import ReferenceTimeline, timeline_from_subtitle

# A job takes this many reference subtitles at most.
MAX_REFERENCES = 6
# Tracks whose count of fitting lines is this close to the best fit equally
# well. Between them the labels decide, and then the order they came in.
CLOSE_LINES = 2
CLOSE_SHARE = 0.05
# A track of signs and songs often has a few lines that pass for dialogue. With
# fewer lines than this, and fewer than this share of the target's, a track
# that does not match is too thin to compare, not other content.
SPARSE_LINES = 30
SPARSE_SHARE = 0.2

Verdict = Literal["chosen", "fits", "duplicate", "too_little_dialogue", "no_match", "unreadable"]


class TrackLabel(BaseModel):
    """What the container says about a track. Free text, so a hint and no more."""

    language: str | None = None
    title: str | None = None
    default: bool = False
    forced: bool = False


@dataclass
class Candidate:
    """One reference subtitle a job came with.

    `subtitle` is None for a file that could not be read. `problem` then says
    why, and the track is reported and takes no part in the choice.
    """

    subtitle: Subtitle | None
    label: TrackLabel = field(default_factory=TrackLabel)
    filename: str | None = None
    problem: str | None = None


class CandidateReport(BaseModel):
    """What was found out about one reference subtitle, and what became of it."""

    # The position among the reference subtitles of the job, from 0.
    index: int
    language: str | None = None
    title: str | None = None
    default: bool = False
    forced: bool = False
    filename: str | None = None
    lines: int = 0
    # The lines of the track that are dialogue, and what the others are.
    dialogue_lines: int = 0
    left_out: dict[str, int] = Field(default_factory=dict)
    # How many of the target's lines that can be told apart found their
    # counterpart in this track.
    matched_lines: int = 0
    comparable_lines: int = 0
    share: float = 0.0
    verdict: Verdict
    reason: str
    # For a duplicate, the track it repeats.
    duplicate_of: int | None = None


class ReferenceChoice(BaseModel):
    """Which reference subtitle a job was synced against, and why not the others."""

    # The index of the track the target was synced against. None when no track fits.
    chosen: int | None = None
    reason: str = ""
    candidates: list[CandidateReport] = Field(default_factory=list)

    def settled(self, used: int | None, placed: bool) -> ReferenceChoice:
        """The choice once the job is decided.

        A track that did not pass the routine check can still be handed to the
        pipeline: the only track of a job, or the closest of several, for a
        reader to judge. `placed` says the target was then placed against it
        by its text after all, and the choice says so.
        """
        if self.chosen is not None or used is None or not placed:
            return self
        candidates = [
            candidate.model_copy(
                update={
                    "verdict": "chosen",
                    "reason": "The routine check found too little in common. Read side by "
                    "side, it is the same content, and the target was placed against it.",
                }
            )
            if candidate.index == used
            else candidate
            for candidate in self.candidates
        ]
        return ReferenceChoice(
            chosen=used,
            reason="No reference subtitle passed the routine check. Read side by side, one "
            "is the same content as the target.",
            candidates=candidates,
        )


@dataclass
class Assessed:
    """A candidate with what reading and matching it gave."""

    index: int
    candidate: Candidate
    labels: list[LineLabel]
    timeline: ReferenceTimeline
    matching: Matching | None = None


@dataclass
class Selection:
    """The outcome of the choice.

    `used` is the track the pipeline gets: the chosen one, or one that did not
    fit and is still the best there is to judge. `refused` says no track can
    serve and nothing else is there to sync against.
    """

    choice: ReferenceChoice
    used: Assessed | None = None
    refused: bool = False


def _fingerprint(timeline: ReferenceTimeline) -> str:
    digest = hashlib.sha256()
    for event in timeline.events:
        digest.update(f"{event.start_ms}\t{event.end_ms}\t{event.text}\n".encode())
    return digest.hexdigest()


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _no_dialogue(report: CandidateReport) -> str:
    if not report.lines:
        return "It has no lines."
    return (
        f"It has no dialogue: its {_plural(report.lines, 'line')} are signs, songs or sound "
        "descriptions."
    )


def choose_reference(
    candidates: list[Candidate],
    match: Callable[[ReferenceTimeline], Matching],
    judge: Callable[[Matching], tuple[str, int, int]],
    *,
    has_audio: bool = False,
) -> Selection:
    """Read every candidate, match the target against it, and take the best.

    `match` matches the target's lines against a timeline. `judge` says what a
    matching amounts to: the verdict ("aligned", "no_match" or
    "too_little_text"), how many of the target's comparable lines found their
    counterpart, and how many comparable lines there are. Both come from the
    pipeline, so that a track is judged here exactly as the pipeline would
    judge it alone.

    With `has_audio` the job can be synced against the audio alone, so a track
    that does not fit is not handed on.

    Of the matchings only the best so far is kept, since each is a table of
    every line against every line.
    """
    reports: list[CandidateReport] = []
    seen: dict[str, int] = {}
    fitting: list[tuple[CandidateReport, Assessed]] = []
    other: list[tuple[CandidateReport, Assessed]] = []
    thin: list[tuple[CandidateReport, Assessed]] = []

    for index, candidate in enumerate(candidates):
        label = candidate.label
        report = CandidateReport(
            index=index,
            language=label.language,
            title=label.title,
            default=label.default,
            forced=label.forced,
            filename=candidate.filename,
            verdict="unreadable",
            reason="",
        )
        reports.append(report)
        if candidate.subtitle is None:
            report.reason = candidate.problem or "It could not be read as a subtitle."
            continue
        labels = classify_lines(candidate.subtitle)
        timeline = timeline_from_subtitle(candidate.subtitle, label.language, labels)
        assessed = Assessed(index=index, candidate=candidate, labels=labels, timeline=timeline)
        report.lines = sum(1 for line in candidate.subtitle.lines if not line.is_comment)
        report.dialogue_lines = len(timeline.events)
        report.left_out = {
            kind: count for kind, count in timeline.dropped.items() if kind != "comment" and count
        }
        if not timeline.events:
            report.verdict = "too_little_dialogue"
            report.reason = _no_dialogue(report)
            thin.append((report, assessed))
            continue
        print_of = _fingerprint(timeline)
        if print_of in seen:
            report.verdict = "duplicate"
            report.duplicate_of = seen[print_of]
            report.reason = "It holds the same dialogue as an earlier track."
            continue
        seen[print_of] = index

        matching = match(timeline)
        verdict, matched, comparable = judge(matching)
        report.matched_lines = matched
        report.comparable_lines = comparable
        report.share = round(matched / comparable, 3) if comparable else 0.0
        result = matching.result
        if verdict == "aligned":
            report.verdict = "fits"
            assessed.matching = matching
            fitting.append((report, assessed))
            # Of the matchings only the best is needed again.
            best = max(found.matched_lines for found, _ in fitting)
            for found, kept in fitting:
                if found.matched_lines < best:
                    kept.matching = None
            continue
        sparse = result.eligible_events < SPARSE_LINES and (
            result.eligible_events < SPARSE_SHARE * max(1, result.eligible_lines)
        )
        if verdict == "no_match" and not sparse:
            report.verdict = "no_match"
            report.reason = (
                f"Only {matched} of {_plural(comparable, 'line')} of the target found a "
                "counterpart in it. It is other content."
            )
            assessed.matching = matching
            other.append((report, assessed))
            # Again only the closest one is needed.
            closest = max(found.matched_lines for found, _ in other)
            for found, kept in other:
                if found.matched_lines < closest:
                    kept.matching = None
            continue
        report.verdict = "too_little_dialogue"
        if verdict == "too_little_text" and result.eligible_lines < result.eligible_events:
            report.reason = (
                f"The target has only {_plural(result.eligible_lines, 'line')} that can be "
                "told apart, too few to compare with this track."
            )
        else:
            report.reason = (
                f"It has only {_plural(report.dialogue_lines, 'line')} of dialogue, too few "
                "to compare with the target."
            )
            if report.left_out:
                report.reason += " Its other lines are signs, songs or sound descriptions."
        # A table against so few lines is small, so it can stay.
        assessed.matching = matching
        thin.append((report, assessed))

    choice = ReferenceChoice(candidates=reports)
    count = len(candidates)
    several = count > 1

    if fitting:
        best = max(report.matched_lines for report, _ in fitting)
        margin = max(CLOSE_LINES, CLOSE_SHARE * best)
        close = [pair for pair in fitting if pair[0].matched_lines >= best - margin]
        # Between tracks that fit equally well the labels decide: a forced
        # track holds only part of the dialogue by its own account, and the
        # default track is the one the file's maker meant to be shown.
        winner, used = min(
            close,
            key=lambda pair: (
                pair[0].forced,
                not pair[0].default,
                -pair[0].matched_lines,
                pair[0].index,
            ),
        )
        winner.verdict = "chosen"
        found = f"{winner.matched_lines} of {_plural(winner.comparable_lines, 'line')}"
        most = "Most of the" if len(fitting) > 1 else "The"
        winner.reason = f"{most} target's dialogue finds its counterpart in it: {found}."
        for report, _ in fitting:
            if report is winner:
                continue
            also = f"{report.matched_lines} of {_plural(report.comparable_lines, 'line')}"
            if report.matched_lines >= best - margin:
                report.reason = (
                    f"It fits as well as the chosen track ({also}). The chosen one "
                    f"{_preferred(winner, report)}."
                )
            else:
                report.reason = f"It fits too ({also}), but the chosen track fits more."
        if used.matching is None:
            used.matching = match(used.timeline)
        choice.chosen = winner.index
        choice.reason = (
            _summary(count, len(fitting), winner)
            if several
            else "The reference subtitle fits the target."
        )
        return Selection(choice=choice, used=used)

    # Nothing fits.
    if several:
        choice.reason = f"None of the {count} reference subtitles fits the target."
    else:
        choice.reason = "The reference subtitle does not fit the target."
    if has_audio:
        return Selection(choice=choice)
    if other:
        # The closest track that is other content goes to the pipeline, which
        # refuses it, unless a reader looks and finds it is the same after all.
        _, used = max(other, key=lambda pair: pair[0].matched_lines)
        if used.matching is None:
            used.matching = match(used.timeline)
        return Selection(choice=choice, used=used)
    if not several and thin:
        # One reference with too little in it is handled as it always was:
        # the pipeline says what it can still do with it.
        return Selection(choice=choice, used=thin[0][1])
    return Selection(choice=choice, refused=True)


def _preferred(winner: CandidateReport, other: CandidateReport) -> str:
    """Why the chosen track went before one that fits as well."""
    if other.forced and not winner.forced:
        return "is not a forced track"
    if winner.default and not other.default:
        return "is the default track"
    if winner.matched_lines > other.matched_lines:
        return "fits a few lines more"
    return "comes first"


def _summary(count: int, fitting: int, winner: CandidateReport) -> str:
    found = (
        f"{winner.matched_lines} of {_plural(winner.comparable_lines, 'line')} of dialogue "
        "have a counterpart in it."
    )
    if fitting == 1:
        name = f'"{winner.title}"' if winner.title else "one"
        return f"Of {count} reference subtitles only {name} fits the target: {found}"
    name = f'"{winner.title}"' if winner.title else "The one that fits most of it"
    return f"Of {count} reference subtitles {fitting} fit the target. {name} was taken: {found}"
