"""Speech-to-text with word timestamps, chunked and cached.

The provider cuts a request off after a minute, so longer audio is cut at quiet
spots and the chunks are transcribed side by side. The merged transcript is
cached by the hash of the audio, so the same audio is paid for once.
"""

from __future__ import annotations

import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel

from retimer.audio import Chunk, split_audio
from retimer.cache import Cache, content_hash
from retimer.providers import DEFAULT_SPEECH_MODEL, DEFAULT_SPEECH_PROVIDER, OpenRouter

# Twenty minutes take the default provider under ten seconds, well inside the
# one-minute cutoff, and an episode still needs only two requests.
DEFAULT_MAX_CHUNK_MS = 20 * 60 * 1000
_CACHE_NAMESPACE = "transcript-v1"
_LANGUAGE_CODES = {
    "english": "en",
    "japanese": "ja",
    "german": "de",
    "french": "fr",
    "spanish": "es",
    "italian": "it",
    "portuguese": "pt",
    "korean": "ko",
    "chinese": "zh",
    "russian": "ru",
    "dutch": "nl",
}


class Word(BaseModel):
    start_ms: int
    end_ms: int
    text: str
    segment: int


class Segment(BaseModel):
    start_ms: int
    end_ms: int
    text: str
    avg_logprob: float | None = None


class Transcript(BaseModel):
    model: str
    provider: str | None = None
    language: str | None
    duration_ms: int
    words: list[Word]
    segments: list[Segment]
    cost_usd: float = 0.0
    seconds: float = 0.0
    chunks: int = 1
    from_cache: bool = False


def normalize_language(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip().lower()
    return _LANGUAGE_CODES.get(value, value)


def _squash(text: str) -> str:
    return "".join(text.split())


def _assign_segments(words: list[dict], segments: list[dict]) -> list[int]:
    """The index of the segment each word belongs to.

    A segment's text is its words joined, so the words are dealt out in order
    until each segment's text is used up. When the two do not line up, the word
    goes to the segment that contains its start time.
    """
    assigned: list[int] = []
    cursor = 0
    for index, segment in enumerate(segments):
        wanted = len(_squash(segment.get("text", "")))
        taken = 0
        while cursor < len(words) and taken < wanted:
            taken += len(_squash(words[cursor].get("word", "")))
            assigned.append(index)
            cursor += 1
        if taken != wanted:
            break
    else:
        if cursor == len(words):
            return assigned

    def by_time(word: dict) -> int:
        start = float(word.get("start", 0.0))
        best = 0
        for index, segment in enumerate(segments):
            if float(segment.get("start", 0.0)) <= start:
                best = index
        return best

    return [by_time(word) for word in words] if segments else [0] * len(words)


def parse_transcription(body: dict, offset_ms: int = 0, first_segment: int = 0) -> Transcript:
    """Turn one verbose transcription response into a transcript, shifted by `offset_ms`."""
    raw_segments = body.get("segments") or []
    raw_words = body.get("words") or []
    owners = _assign_segments(raw_words, raw_segments)
    segments = [
        Segment(
            start_ms=offset_ms + round(float(segment.get("start", 0.0)) * 1000),
            end_ms=offset_ms + round(float(segment.get("end", 0.0)) * 1000),
            text=str(segment.get("text", "")).strip(),
            avg_logprob=segment.get("avg_logprob"),
        )
        for segment in raw_segments
    ]
    words = [
        Word(
            start_ms=offset_ms + round(float(word.get("start", 0.0)) * 1000),
            end_ms=offset_ms + round(float(word.get("end", 0.0)) * 1000),
            text=str(word.get("word", "")),
            segment=first_segment + owner,
        )
        for word, owner in zip(raw_words, owners, strict=True)
    ]
    return Transcript(
        model="",
        language=normalize_language(body.get("language")),
        duration_ms=round(float(body.get("duration") or 0.0) * 1000),
        words=words,
        segments=segments,
    )


def _transcribe_chunks(
    chunks: list[Chunk],
    client: OpenRouter,
    model: str,
    provider: str | None,
    language: str | None,
    workers: int,
) -> tuple[list[Transcript], float]:
    def one(chunk: Chunk) -> tuple[Transcript, float]:
        result = client.transcribe(
            chunk.path.read_bytes(),
            filename=chunk.path.name,
            model=model,
            provider=provider,
            language=language,
        )
        return parse_transcription(result.body, chunk.offset_ms), result.cost_usd

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        done = list(pool.map(one, chunks))
    return [part for part, _ in done], sum(cost for _, cost in done)


def transcribe_audio(
    path: Path,
    *,
    model: str = DEFAULT_SPEECH_MODEL,
    provider: str | None = DEFAULT_SPEECH_PROVIDER,
    language: str | None = None,
    client: OpenRouter | None = None,
    cache: Cache | None = None,
    max_chunk_ms: int = DEFAULT_MAX_CHUNK_MS,
    workers: int = 4,
) -> Transcript:
    """Transcribe an audio file. A cached transcript costs nothing and takes no time."""
    cache = cache if cache is not None else Cache()
    key = content_hash(path.read_bytes(), model, provider or "", language or "", str(max_chunk_ms))
    stored = cache.get(_CACHE_NAMESPACE, key)
    if stored is not None:
        transcript = Transcript.model_validate_json(stored)
        transcript.from_cache = True
        transcript.cost_usd = 0.0
        transcript.seconds = 0.0
        return transcript

    client = client or OpenRouter()
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as temp:
        chunks = split_audio(path, Path(temp), max_chunk_ms)
        parts, cost = _transcribe_chunks(chunks, client, model, provider, language, workers)
        if language is None and len(parts) > 1:
            # Each chunk guessed its own language. The majority decides, and the
            # chunks that guessed differently are transcribed again with it.
            votes = Counter()
            for part in parts:
                if part.language:
                    votes[part.language] += len(part.words)
            if votes:
                majority = votes.most_common(1)[0][0]
                again = [
                    i for i, part in enumerate(parts) if part.words and part.language != majority
                ]
                if again:
                    redone, extra = _transcribe_chunks(
                        [chunks[i] for i in again], client, model, provider, majority, workers
                    )
                    cost += extra
                    for index, part in zip(again, redone, strict=True):
                        parts[index] = part
                language = majority

    words: list[Word] = []
    segments: list[Segment] = []
    for part in parts:
        base = len(segments)
        words += [word.model_copy(update={"segment": word.segment + base}) for word in part.words]
        segments += part.segments
    transcript = Transcript(
        model=model,
        provider=provider,
        language=normalize_language(language)
        or next((p.language for p in parts if p.language), None),
        duration_ms=chunks[-1].offset_ms + chunks[-1].duration_ms,
        words=words,
        segments=segments,
        cost_usd=cost,
        seconds=time.monotonic() - started,
        chunks=len(chunks),
    )
    cache.put(_CACHE_NAMESPACE, key, transcript.model_dump_json().encode("utf-8"))
    return transcript
