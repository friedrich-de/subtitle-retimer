"""The reference timeline: one list of timed text, whatever the reference was.

Audio becomes utterances through its transcript. A reference subtitle becomes
its dialogue lines. Matching and fitting only ever see the timeline, so they
work the same for audio, a subtitle, or both.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, Field

from retimer.classify import classify_lines
from retimer.model import Subtitle
from retimer.transcribe import Transcript, Word
from retimer.views import build_match_view

# A pause this long between two words starts a new utterance.
UTTERANCE_GAP_MS = 450
MAX_UTTERANCE_MS = 9_000
# Segments the model itself was this unsure about are mostly invented text.
MIN_SEGMENT_LOGPROB = -1.0
# Speech spans closer than this are joined.
SPEECH_JOIN_MS = 300

_SENTENCE_END = re.compile(r"[.?!…。？！]+[\"'”’）)」』]*$")
_MARKUP = re.compile(
    r"^[\s\-–—]*(?:\*[^*]*\*|\[[^\]]*\]|\([^)]*\)|（[^）]*）|［[^］]*］|♪.*♪?|#.*#)[\s\-–—]*$"
)
# Lines the speech model likes to invent over music, silence and end credits.
_INVENTED = re.compile(
    r"thanks? for watching|subtitles? by|amara\.org|please subscribe"
    r"|ご視聴ありがとうございました|チャンネル登録|字幕",
    re.IGNORECASE,
)


class RefWord(BaseModel):
    start_ms: int
    text: str


class RefEvent(BaseModel):
    """A span of the reference with what is said in it.

    An utterance from a transcript also keeps its words and when each one
    starts, so that a reader can point at the place inside it where a subtitle
    line begins.
    """

    start_ms: int
    end_ms: int
    text: str
    words: list[RefWord] = Field(default_factory=list, exclude=True, repr=False)


class ReferenceTimeline(BaseModel):
    """Timed text of the reference, and when anything is spoken at all.

    `events` are what target lines are matched against, and `onsets` the
    moments speech starts after a pause, for the text-free alignment. `speech`
    are the spans in which anything is said, including what was too unclear to
    match; outside them there is silence. `duration_ms` is the length of the
    audio, when there is audio.
    """

    kind: Literal["audio", "subtitle", "both"]
    language: str | None = None
    events: list[RefEvent] = Field(default_factory=list)
    speech: list[tuple[int, int]] = Field(default_factory=list)
    onsets: list[int] = Field(default_factory=list)
    duration_ms: int | None = None
    dropped: dict[str, int] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

    @property
    def span(self) -> tuple[int, int] | None:
        if not self.events:
            return None
        return self.events[0].start_ms, max(event.end_ms for event in self.events)


def _is_wide(text: str) -> bool:
    """Whether the text is written without spaces between words, as Japanese is."""
    letters = [char for char in text if char.isalnum()]
    wide = sum(1 for char in letters if unicodedata.east_asian_width(char) in ("W", "F"))
    return bool(letters) and wide / len(letters) >= 0.5


def _typical_ms(text: str) -> int:
    """About how long it takes to say one word."""
    letters = sum(1 for char in text if char.isalnum())
    if _is_wide(text):
        return max(120, min(500, letters * 110))
    return max(120, min(400, letters * 45))


def repair_word_times(words: list[Word]) -> list[Word]:
    """Pull in word starts that the speech model stretched across a pause.

    After music or silence the model often dates the first word of a sentence
    back to where the pause began. The end of a word is reliable, so a word that
    lasts several times longer than it takes to say is given a start one typical
    word length before its end. Measured against the sound itself on the seed
    films, that lands within about a tenth of a second of where the voice sets in.
    """
    repaired = []
    for word in words:
        typical = _typical_ms(word.text)
        if word.end_ms - word.start_ms > max(500, 3 * typical):
            repaired.append(word.model_copy(update={"start_ms": word.end_ms - typical}))
        else:
            repaired.append(word)
    return repaired


def _join(words: list[Word]) -> str:
    wide = _is_wide("".join(word.text for word in words))
    separator = "" if wide else " "
    return separator.join(word.text.strip() for word in words if word.text.strip()).strip()


def _has_speech(text: str) -> bool:
    return any(char.isalnum() for char in text)


def utterances_from_transcript(transcript: Transcript) -> tuple[list[RefEvent], dict[str, int]]:
    """Group the words of a transcript into utterances and drop what is not speech.

    Returns the utterances and a count of what was dropped, by reason.
    """
    dropped: dict[str, int] = {}

    def drop(reason: str, count: int = 1) -> None:
        dropped[reason] = dropped.get(reason, 0) + count

    confident = []
    for word in transcript.words:
        segment = transcript.segments[word.segment] if transcript.segments else None
        if (
            segment is not None
            and segment.avg_logprob is not None
            and segment.avg_logprob < MIN_SEGMENT_LOGPROB
        ):
            drop("low_confidence_words")
            continue
        confident.append(word)
    words = repair_word_times(confident)

    groups: list[list[Word]] = []
    for word in words:
        current = groups[-1] if groups else None
        if current is None:
            groups.append([word])
            continue
        previous = current[-1]
        new_segment = word.segment != previous.segment
        pause = word.start_ms - previous.end_ms
        sentence_done = bool(_SENTENCE_END.search(previous.text.strip()))
        too_long = word.end_ms - current[0].start_ms > MAX_UTTERANCE_MS
        if (
            pause > UTTERANCE_GAP_MS
            or too_long
            or (sentence_done and (pause > 120 or new_segment))
            or word.start_ms < previous.start_ms
        ):
            groups.append([word])
        else:
            current.append(word)

    events = []
    for group in groups:
        text = _join(group)
        start = group[0].start_ms
        end = max(word.end_ms for word in group)
        if not _has_speech(text) or _MARKUP.match(text):
            drop("non_speech")
            continue
        if _INVENTED.search(text):
            drop("invented_phrase")
            continue
        if end <= start and len(group) > 1:
            drop("no_duration")
            continue
        events.append(
            RefEvent(
                start_ms=start,
                end_ms=max(end, start + 200),
                text=text,
                words=[
                    RefWord(start_ms=word.start_ms, text=word.text.strip())
                    for word in group
                    if word.text.strip()
                ],
            )
        )
    events.sort(key=lambda event: event.start_ms)
    return events, dropped


def merge_spans(
    spans: list[tuple[int, int]], join_ms: int = SPEECH_JOIN_MS
) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start - merged[-1][1] <= join_ms:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def speech_spans(transcript: Transcript) -> list[tuple[int, int]]:
    """Every stretch in which the speech model heard anything at all.

    Words the model was unsure of are no use for matching, but they still say
    that somebody speaks. This is what silence is judged by, so that a line is
    only called absent where the model heard nothing.
    """
    words = repair_word_times(transcript.words)
    return merge_spans(
        [(word.start_ms, max(word.end_ms, word.start_ms + 200)) for word in words] if words else []
    )


def timeline_from_transcript(transcript: Transcript) -> ReferenceTimeline:
    events, dropped = utterances_from_transcript(transcript)
    return ReferenceTimeline(
        kind="audio",
        language=transcript.language,
        events=events,
        speech=speech_spans(transcript),
        onsets=[event.start_ms for event in events],
        duration_ms=transcript.duration_ms,
        dropped=dropped,
    )


def timeline_from_subtitle(subtitle: Subtitle, language: str | None = None) -> ReferenceTimeline:
    """The dialogue lines of an in-sync subtitle, reduced to the spoken words."""
    view = build_match_view(subtitle, classify_lines(subtitle))
    lines = sorted(view.lines, key=lambda line: line.start_ms)
    events = [
        RefEvent(start_ms=line.start_ms, end_ms=line.end_ms, text=line.text) for line in lines
    ]
    return ReferenceTimeline(
        kind="subtitle",
        language=language,
        events=events,
        speech=merge_spans([(event.start_ms, event.end_ms) for event in events], join_ms=0),
        onsets=[event.start_ms for event in events],
        dropped=dict(view.excluded),
    )
