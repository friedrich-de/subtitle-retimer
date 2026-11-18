"""Has the audio of a sample of the set transcribed, once, and within a limit.

The retimer's jobs against audio begin with a transcript, which a speech
model at a provider writes and which is billed by the second. Over a whole
set that adds up, so the kit does it in a step of its own: a sample of the
videos, spread over the series, each transcribed once, and no request that
could take the spending past a limit fixed beforehand. The transcripts go
into the set's cache, where the pipeline looks for them, and what was heard
is embedded with the local model like every other text of the set. A run
against the audio then asks no provider and costs nothing.

What was transcribed and what it cost is on record in the set, so the limit
holds across every time the step is started.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field

from retimer.audio import AudioError
from retimer.embed import Embedder
from retimer.pipeline import Settings
from retimer.providers import OpenRouter, ProviderError
from retimer.realset.embedding import CompactCache, LocalEmbeddings, transcript_texts
from retimer.realset.manifest import CACHE_DIR, Manifest, Video, load_manifest
from retimer.realset.systems import AUDIO_LANGUAGE

SPEECH_FILE = "speech.json"
# What the speech provider charges for a second of audio. Its price of 2026-11-16.
USD_PER_SECOND = 0.0000111
DEFAULT_HOURS = 20.0
DEFAULT_LIMIT_USD = 0.90


class Heard(BaseModel):
    """One video whose audio was sent to the speech model."""

    seconds: float
    # What the provider said the transcript cost, and what was counted against
    # the limit: that, or the list price of the audio when it is more.
    reported_usd: float = 0.0
    counted_usd: float = 0.0
    took_s: float = 0.0
    utterances: int = 0
    error: str | None = None


class Speech(BaseModel):
    """What was transcribed for a set, with which model, and what it cost."""

    model: str = ""
    provider: str | None = None
    language: str = AUDIO_LANGUAGE
    # Everything that was ever counted, also for an attempt that failed. It only grows.
    spent_usd: float = 0.0
    videos: dict[str, Heard] = Field(default_factory=dict)

    def transcribed(self) -> set[str]:
        """The videos a run against the audio can read a transcript of."""
        return {video for video, heard in self.videos.items() if heard.error is None}


def load_speech(root: Path) -> Speech:
    path = root / SPEECH_FILE
    if not path.exists():
        return Speech()
    return Speech.model_validate_json(path.read_text(encoding="utf-8"))


def _save(root: Path, speech: Speech) -> None:
    # Written beside and moved into place: the record of what was spent must
    # not be lost to a step that is cut off while it writes.
    path = root / SPEECH_FILE
    draft = path.with_suffix(".tmp")
    draft.write_text(speech.model_dump_json(indent=1), encoding="utf-8")
    os.replace(draft, path)


def sample_videos(manifest: Manifest, hours: float) -> list[Video]:
    """The videos to transcribe: spread over the series, together no longer than `hours`.

    Only videos count that a main pair outside the tuning tenth is judged on.
    The series take turns, so that a long series does not fill the sample.
    Within a series the order follows from the ids alone, so the same videos
    are drawn every time and it is not always the first episode. A video that
    no longer fits is passed over.
    """
    judged = {pair.video for pair in manifest.pairs if pair.kind == "main" and not pair.tuning}
    series: dict[str, list[Video]] = {}
    for video in manifest.videos:
        if video.id in judged and video.audio is not None and video.duration_s > 0:
            series.setdefault(video.folder, []).append(video)
    queues = [
        sorted(videos, key=lambda video: hashlib.sha256(video.id.encode()).hexdigest())
        for _, videos in sorted(series.items())
    ]
    left = hours * 3600
    drawn = []
    while any(queues):
        for queue in queues:
            if queue:
                video = queue.pop(0)
                if video.duration_s <= left:
                    drawn.append(video)
                    left -= video.duration_s
    return drawn


def transcribe_sample(
    root: Path,
    *,
    hours: float = DEFAULT_HOURS,
    limit_usd: float = DEFAULT_LIMIT_USD,
    client: object | None = None,
    embeddings: object | None = None,
    say: Callable[[str], None] | None = None,
) -> Speech:
    """Transcribe the audio of the sample that has no transcript yet, and embed what was heard.

    No request is made that could take the spending past `limit_usd`. Before
    each video its price is worked out from its length: at the provider's
    list price, or at what the provider charged per second so far when that
    is more. Afterwards the video counts at the larger of that and what the
    provider reported, also when the attempt failed. The step stops at the
    first video it cannot afford, and at the first that fails.
    """
    # Imported here: a run that only asks which videos have a transcript needs no ffmpeg.
    from retimer.transcribe import cached_transcript, transcribe_audio

    say = say or (lambda message: None)
    manifest = load_manifest(root)
    cache = CompactCache(root / CACHE_DIR)
    settings = Settings()
    speech = load_speech(root)
    wanted = (settings.speech_model, settings.speech_provider, AUDIO_LANGUAGE)
    if (speech.model, speech.provider, speech.language) != wanted:
        # The pipeline looks for transcripts of its own speech model. Those of
        # another one are of no use to a run, but what they cost was spent.
        speech = Speech(
            model=wanted[0], provider=wanted[1], language=wanted[2], spent_usd=speech.spent_usd
        )
    request = {"model": speech.model, "provider": speech.provider, "language": speech.language}
    embeddings = embeddings or LocalEmbeddings()
    # The model is loaded before anything is paid for: a transcript whose
    # texts cannot be embedded is of no use to a run.
    embeddings.embed(["a line to load the model with"], model=settings.embedding_model)
    embedder = Embedder(client=embeddings, cache=cache, model=settings.embedding_model)
    client = client or OpenRouter()
    rate = max(
        [USD_PER_SECOND]
        + [heard.reported_usd / heard.seconds for heard in speech.videos.values() if heard.seconds]
    )

    sample = sample_videos(manifest, hours)
    say(
        f"{len(sample)} videos in the sample, "
        f"{sum(video.duration_s for video in sample) / 3600:.1f} hours of audio"
    )
    for index, video in enumerate(sample, start=1):
        path = root / video.audio.file
        if not path.is_file():
            say(f"{video.id} has no audio file in the set: {path}")
            continue
        transcript = cached_transcript(path, cache, **request)
        if transcript is None:
            price = video.duration_s * rate
            if speech.spent_usd + price > limit_usd:
                say(
                    f"stopped at the limit of {limit_usd:.2f} USD: {speech.spent_usd:.4f} are "
                    f"spent, and {video.id} would cost {price:.4f} more"
                )
                break
            try:
                transcript = transcribe_audio(path, client=client, cache=cache, **request)
            except (ProviderError, AudioError, OSError) as error:
                # Some of it may have been transcribed and billed before it failed.
                speech.spent_usd += price
                speech.videos[video.id] = Heard(
                    seconds=video.duration_s, counted_usd=price, error=str(error)[-300:]
                )
                _save(root, speech)
                # A wrong key or a provider that is down would fail on every
                # video, and each would count. So one failure ends the step.
                say(f"stopped: {video.id} could not be transcribed: {error}")
                break
            counted = max(transcript.cost_usd, price)
            speech.spent_usd += counted
            rate = max(rate, transcript.cost_usd / video.duration_s)
            speech.videos[video.id] = Heard(
                seconds=video.duration_s,
                reported_usd=round(transcript.cost_usd, 6),
                counted_usd=round(counted, 6),
                took_s=round(transcript.seconds, 1),
            )
            # On record before anything else is done with it.
            _save(root, speech)
        elif video.id not in speech.transcribed():
            # In the cache from before, without a line in the record.
            speech.videos[video.id] = Heard(seconds=video.duration_s)
        texts = transcript_texts(transcript)
        embedder.embed(list(dict.fromkeys(texts)))
        speech.videos[video.id].utterances = len(texts) // 2
        _save(root, speech)
        if index % 10 == 0 or index == len(sample):
            say(f"[{index}/{len(sample)}] {speech.spent_usd:.4f} USD counted so far")
    _save(root, speech)
    return speech
