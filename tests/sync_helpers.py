"""Stand-ins for the hosted services, so that matching and the pipeline run offline."""

import hashlib

import numpy as np

from retimer import Subtitle, loads
from retimer.cache import Cache, content_hash
from retimer.providers import DEFAULT_SPEECH_MODEL, DEFAULT_SPEECH_PROVIDER, Embeddings
from retimer.transcribe import DEFAULT_MAX_CHUNK_MS, Segment, Transcript, Word

from .bench_helpers import _stamp

DIMENSIONS = 96


def word_vector(word: str) -> np.ndarray:
    seed = int.from_bytes(hashlib.sha256(word.encode("utf-8")).digest()[:8], "big")
    return np.random.default_rng(seed).normal(size=DIMENSIONS)


def text_vector(text: str) -> list[float]:
    """A bag of words: texts that share words are alike, identical texts are equal."""
    words = [word.strip(".,!?") for word in text.lower().split()]
    words = [word for word in words if word] or ["<empty>"]
    return np.sum([word_vector(word) for word in words], axis=0).tolist()


class FakeClient:
    """Embeds by shared words and counts what it was asked."""

    def __init__(self) -> None:
        self.embedded: list[str] = []
        self.transcriptions = 0

    def embed(self, texts: list[str], *, model: str) -> Embeddings:
        self.embedded += texts
        return Embeddings(vectors=[text_vector(text) for text in texts], cost_usd=0.001)

    def transcribe(self, *args, **kwargs):
        self.transcriptions += 1
        raise AssertionError("the transcript should come from the cache")


SENTENCES = [
    "the harbor was empty when we arrived",
    "nobody told me about the second letter",
    "keep the lantern away from the curtains",
    "she counted every coin on the table",
    "the train leaves before the snow melts",
    "my brother painted that fence last summer",
    "there is a map under the floorboards",
    "we should wait until the bells stop",
    "he never forgave the captain for leaving",
    "bring the ladder around to the orchard",
    "the river froze early that winter",
    "I still remember the smell of the bakery",
    "you promised to return the silver key",
    "the engine failed halfway up the mountain",
    "someone left a candle burning upstairs",
    "they buried the chest behind the chapel",
    "her violin was the only thing she saved",
    "the storm took the roof off the barn",
    "ask the tailor about the missing button",
    "our village has no doctor anymore",
    "the soldiers marched past without a word",
    "a stranger paid for all the bread",
    "the lighthouse keeper refused to answer",
    "wolves came down from the northern ridge",
    "I found your father's watch in the well",
    "the judge will not hear another witness",
    "close the shutters before the tide rises",
    "that melody belongs to my grandmother",
    "the mirror cracked on the wedding day",
    "follow the crows across the marsh",
]


def make_subtitle(
    starts: list[int], texts: list[str] | None = None, length_ms: int = 2_000
) -> Subtitle:
    """An SRT with one line per start time, saying one sentence each."""
    texts = texts if texts is not None else SENTENCES[: len(starts)]
    blocks = [
        f"{index + 1}\n{_stamp(start)} --> {_stamp(start + length_ms)}\n{text}\n"
        for index, (start, text) in enumerate(zip(starts, texts, strict=True))
    ]
    return loads("\n".join(blocks))


def spread(count: int, first_ms: int = 10_000, step_ms: int = 6_000) -> list[int]:
    return [first_ms + index * step_ms for index in range(count)]


def make_transcript(
    starts: list[int], texts: list[str] | None = None, duration_ms: int | None = None
) -> Transcript:
    """A transcript in which every sentence is one segment, its words 300 ms each."""
    texts = texts if texts is not None else SENTENCES[: len(starts)]
    words = []
    segments = []
    for index, (start, text) in enumerate(zip(starts, texts, strict=True)):
        parts = text.split()
        parts[-1] += "."
        for position, part in enumerate(parts):
            words.append(
                Word(
                    start_ms=start + position * 300,
                    end_ms=start + position * 300 + 280,
                    text=part,
                    segment=index,
                )
            )
        segments.append(
            Segment(start_ms=start, end_ms=words[-1].end_ms, text=text, avg_logprob=-0.2)
        )
    return Transcript(
        model=DEFAULT_SPEECH_MODEL,
        provider=DEFAULT_SPEECH_PROVIDER,
        language="en",
        duration_ms=duration_ms or (segments[-1].end_ms + 5_000),
        words=words,
        segments=segments,
    )


def store_transcript(cache: Cache, audio_path, transcript: Transcript) -> None:
    """Put a transcript where `transcribe_audio` looks for the one of this audio file."""
    audio_path.write_bytes(b"not really audio " + audio_path.name.encode())
    key = content_hash(
        audio_path.read_bytes(),
        DEFAULT_SPEECH_MODEL,
        DEFAULT_SPEECH_PROVIDER,
        "",
        str(DEFAULT_MAX_CHUNK_MS),
    )
    cache.put("transcript-v1", key, transcript.model_dump_json().encode("utf-8"))
