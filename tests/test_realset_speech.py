"""The transcribing step with its spending limit, and the systems that need it or a model."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from retimer.audio import Chunk
from retimer.cli import main
from retimer.io import save
from retimer.providers import ProviderError, Transcription
from retimer.realset import run as run_module
from retimer.realset.embedding import CachedOnly, CompactCache, fill_cache
from retimer.realset.judge import judge_run
from retimer.realset.manifest import (
    CACHE_DIR,
    AudioFile,
    Manifest,
    Pair,
    Target,
    Video,
    plain_path,
    save_manifest,
)
from retimer.realset.report import render, write_report
from retimer.realset.run import load_results, run_systems, select_pairs
from retimer.realset.speech import (
    SPEECH_FILE,
    USD_PER_SECOND,
    load_speech,
    sample_videos,
    transcribe_sample,
)
from retimer.realset.systems import DEFAULT_SYSTEMS, SYSTEMS, make_runner, resolve
from retimer.transcribe import cached_transcript

from .sync_helpers import SENTENCES, FakeClient, make_subtitle, make_transcript, spread
from .test_realset_run import OFFSET_MS, stored_track

# What a video of a thousand seconds costs at the provider's list price.
LIST_PRICE = 1_000 * USD_PER_SECOND


def said(starts, texts) -> dict:
    """What the speech provider answers for audio in which these sentences are spoken."""
    transcript = make_transcript(starts, texts)
    return {
        "language": "Japanese",
        "duration": transcript.duration_ms / 1000,
        "segments": [
            {
                "start": segment.start_ms / 1000,
                "end": segment.end_ms / 1000,
                "text": segment.text + ".",
                "avg_logprob": segment.avg_logprob,
            }
            for segment in transcript.segments
        ],
        "words": [
            {"word": word.text, "start": word.start_ms / 1000, "end": word.end_ms / 1000}
            for word in transcript.words
        ],
    }


class Speaker:
    """Stands in for the speech provider. It knows what is spoken in each audio file."""

    def __init__(self, spoken: dict[bytes, dict], usd: float = 0.004) -> None:
        self.spoken = spoken
        self.usd = usd
        self.asked: list[tuple] = []
        self.down: set[bytes] = set()

    def transcribe(self, audio, *, filename, model, provider, language):
        self.asked.append((audio, model, provider, language))
        if audio in self.down:
            raise ProviderError("the provider is down")
        return Transcription(body=self.spoken[audio], cost_usd=self.usd)


def video(root, video_id, folder, seconds, texts=None, audio=True, starts=None):
    """A video of the set. Its audio file only holds its name, which the stand-in knows."""
    tracks = []
    if texts is not None:
        starts = starts if starts is not None else spread(len(texts))
        tracks = [stored_track(root, f"{video_id}s1", make_subtitle(starts, texts), "main")]
    described = None
    if audio:
        path = root / "audio" / f"{video_id}.ogg"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(f"the audio of {video_id}".encode())
        described = AudioFile(file=f"audio/{video_id}.ogg", stream=1, sha256="0" * 64, bytes=1)
    return Video(
        id=video_id,
        source=f"{folder}/{video_id}.mkv",
        folder=folder,
        duration_s=seconds,
        audio=described,
        tracks=tracks,
    )


@pytest.fixture
def heard_set(tmp_path, monkeypatch):
    """Two episodes with audio, a target for the first, and the stand-in that hears them."""
    root = tmp_path / "set"
    (root / "targets").mkdir(parents=True)
    starts = spread(20)
    save(
        make_subtitle([start + OFFSET_MS for start in starts], SENTENCES[:20]),
        root / "targets/t1.srt",
    )
    videos = [
        video(root, "v1", "1 - Show", 1_000, SENTENCES[:20]),
        video(root, "v2", "1 - Show", 1_000, SENTENCES[20:], starts=spread(10)),
    ]
    target = Target(
        id="t1",
        entry=7,
        name="[Group] Show - 01 (AT-X 1440x1080).srt",
        file="targets/t1.srt",
        sha256="0" * 64,
        bytes=1,
        format="srt",
        type="AT-X",
        videos=["v1"],
    )
    pairs = [
        Pair(id="t1.v1s1", kind="main", target="t1", video="v1", track="v1s1", expected="placed"),
        Pair(id="t1.v2s1", kind="wrong", target="t1", video="v2", track="v2s1",
             expected="refused", note="the next episode"),
        # The second video is also judged on, so that it is in the sample.
        Pair(id="t1.v2s1.main", kind="main", target="t1", video="v2", track="v2s1",
             expected="placed"),
    ]  # fmt: skip
    manifest = Manifest(built="2026-01-01", videos=videos, targets=[target], pairs=pairs)
    save_manifest(root, manifest)
    fill_cache(
        [root / "targets/t1.srt"],
        [plain_path(root, track.file) for one in videos for track in one.tracks],
        CompactCache(root / CACHE_DIR),
        client=FakeClient(),
    )
    # No ffmpeg: the audio goes to the provider as it is, in one piece.
    monkeypatch.setattr("retimer.transcribe.split_audio", lambda path, *_: [Chunk(path, 0, 1_000)])
    speaker = Speaker(
        {
            b"the audio of v1": said(starts, SENTENCES[:20]),
            b"the audio of v2": said(spread(10), SENTENCES[20:]),
        }
    )
    return root, manifest, speaker


def transcribe(root, speaker, **limits):
    return transcribe_sample(root, client=speaker, embeddings=FakeClient(), **limits)


def test_the_sample_is_spread_over_the_series_and_fits_the_hours(tmp_path):
    videos = [video(tmp_path, f"a{index}", "1 - Long Show", 1_500) for index in range(6)]
    videos += [video(tmp_path, f"b{index}", "2 - Short Show", 1_500) for index in range(2)]
    videos += [
        video(tmp_path, "film", "3 - Film", 6_000),
        video(tmp_path, "silent", "4 - No Audio", 1_500, audio=False),
        video(tmp_path, "tuned", "5 - Tuning Only", 1_500),
        video(tmp_path, "unpaired", "6 - Nothing To Judge", 1_500),
    ]
    pairs = [
        Pair(
            id=f"t.{one.id}",
            kind="main",
            target="t",
            video=one.id,
            track="x",
            expected="placed",
            tuning=one.id == "tuned",
        )
        for one in videos
        if one.id != "unpaired"
    ]
    manifest = Manifest(videos=videos, pairs=pairs)

    drawn = [one.id for one in sample_videos(manifest, hours=3.0)]

    # One of each series by turns. The film takes its turn while it still fits.
    assert {one[0] for one in drawn[:2]} == {"a", "b"} and drawn[2] == "film"
    assert sorted(one[0] for one in drawn) == ["a", "a", "b", "f"]
    assert {"silent", "tuned", "unpaired"}.isdisjoint(drawn)
    assert sum(manifest.video(one).duration_s for one in drawn) <= 3.0 * 3600
    # The same videos every time, and a smaller sample is the beginning of a larger one.
    assert [one.id for one in sample_videos(manifest, hours=3.0)] == drawn
    assert [one.id for one in sample_videos(manifest, hours=1.0)] == drawn[:2]
    # A film that does not fit is passed over, and the shorter videos after it are taken.
    assert "film" not in [one.id for one in sample_videos(manifest, hours=1.5)]
    assert len(sample_videos(manifest, hours=1.5)) == 3


def test_transcripts_go_into_the_cache_with_their_embeddings(heard_set):
    root, manifest, speaker = heard_set

    speech = transcribe(root, speaker, hours=1.0, limit_usd=0.5)

    assert speech.transcribed() == {"v1", "v2"}
    # The speech model is asked as the pipeline asks it, with the language named.
    assert {call[1:] for call in speaker.asked} == {("openai/whisper-large-v3-turbo", "groq", "ja")}
    heard = speech.videos["v1"]
    assert (heard.seconds, heard.reported_usd, heard.utterances) == (1_000, 0.004, 20)
    # The provider reported less than the list price, so the list price counts.
    assert heard.counted_usd == pytest.approx(LIST_PRICE)
    assert speech.spent_usd == pytest.approx(2 * LIST_PRICE)
    assert load_speech(root) == speech
    cache = CompactCache(root / CACHE_DIR)
    transcript = cached_transcript(
        root / "audio/v1.ogg", cache, model=speech.model, provider=speech.provider, language="ja"
    )
    assert transcript.from_cache and len(transcript.segments) == 20
    # Asked without the language, as the benchmark asks, it is another transcript.
    assert cached_transcript(root / "audio/v1.ogg", cache) is None

    # Started again, nothing is asked and nothing is spent.
    again = transcribe(root, speaker, hours=1.0, limit_usd=0.5)
    assert len(speaker.asked) == 2 and again == speech


def test_no_request_is_made_that_could_pass_the_limit(heard_set):
    root, manifest, speaker = heard_set

    speech = transcribe(root, speaker, hours=1.0, limit_usd=1.5 * LIST_PRICE)

    assert len(speaker.asked) == 1 and len(speech.transcribed()) == 1
    assert speech.spent_usd == pytest.approx(LIST_PRICE)
    # The limit holds over every start of the step, and what is there is kept.
    transcribe(root, speaker, hours=1.0, limit_usd=1.5 * LIST_PRICE)
    assert len(speaker.asked) == 1
    speech = transcribe(root, speaker, hours=1.0, limit_usd=2.5 * LIST_PRICE)
    assert len(speaker.asked) == 2 and speech.transcribed() == {"v1", "v2"}


def test_a_provider_that_charges_more_than_its_list_price_is_believed(heard_set):
    root, manifest, speaker = heard_set
    speaker.usd = 5 * LIST_PRICE

    speech = transcribe(root, speaker, hours=1.0, limit_usd=9 * LIST_PRICE)

    # The first video cost five times the list price, so the second is priced
    # at that too and no longer fits.
    assert len(speaker.asked) == 1
    assert speech.spent_usd == pytest.approx(5 * LIST_PRICE)
    # The rate is remembered with the record.
    transcribe(root, speaker, hours=1.0, limit_usd=9 * LIST_PRICE)
    assert len(speaker.asked) == 1


def test_a_failed_attempt_counts_ends_the_step_and_is_tried_again(heard_set):
    root, manifest, speaker = heard_set
    speaker.down = {b"the audio of v1", b"the audio of v2"}

    speech = transcribe(root, speaker, hours=1.0, limit_usd=0.5)

    # One failure ends the step: a wrong key would fail on every video, and each would count.
    assert len(speaker.asked) == 1 and speech.transcribed() == set()
    (failed,) = speech.videos.values()
    assert "the provider is down" in failed.error
    assert speech.spent_usd == pytest.approx(LIST_PRICE)
    assert run_systems(root, "none", ["retimer-audio"], workers=1) and not load_results(
        root / "runs/none"
    )

    speaker.down = set()
    (root / "audio/v2.ogg").rename(root / "audio/elsewhere.ogg")
    speech = transcribe(root, speaker, hours=1.0, limit_usd=0.5)
    # A video whose audio is not in the set is passed over and costs nothing.
    assert speech.transcribed() == {"v1"} and speech.videos["v1"].error is None
    (root / "audio/elsewhere.ogg").rename(root / "audio/v2.ogg")
    speech = transcribe(root, speaker, hours=1.0, limit_usd=0.5)
    assert speech.transcribed() == {"v1", "v2"}
    # What the failed attempt counted stays counted.
    assert speech.spent_usd == pytest.approx(3 * LIST_PRICE)


def test_the_model_for_the_embeddings_is_loaded_before_anything_is_paid(heard_set):
    root, manifest, speaker = heard_set

    with pytest.raises(ProviderError, match="embedding cache"):
        transcribe_sample(root, client=speaker, embeddings=CachedOnly())

    assert speaker.asked == [] and not (root / SPEECH_FILE).exists()


def test_the_retimer_runs_against_the_audio_that_was_transcribed(heard_set):
    root, manifest, speaker = heard_set
    # Only the first video fits the limit.
    transcribe(root, speaker, hours=1.0, limit_usd=1.5 * LIST_PRICE)
    first = next(iter(load_speech(root).transcribed()))
    assert first == "v1" or first == "v2"
    transcribe(root, speaker, hours=1.0, limit_usd=0.5)

    run_dir = run_systems(root, "heard", ["retimer", "retimer-audio"], workers=1)

    records = {(record["pair"], record["system"]): record for record in load_results(run_dir)}
    assert len(records) == 6
    heard = records[("t1.v1s1", "retimer-audio")]
    assert heard["outcome"] == "synced" and heard["cost_usd"] == 0.0
    assert heard["details"]["plan"]["timing_ops"] == [{"op": "shift", "ms": -OFFSET_MS}]
    assert heard["details"]["report"]["reference"]["kind"] == "audio"
    assert records[("t1.v2s1", "retimer-audio")]["outcome"] == "rejected_no_match"

    judged = judge_run(root, run_dir, workers=1)
    by = {(entry["pair"], entry["system"]): entry for entry in judged}
    assert by[("t1.v1s1", "retimer-audio")]["verdict"] == "verified"
    assert by[("t1.v2s1", "retimer-audio")]["verdict"] == "refused"
    text = write_report(root, run_dir)
    assert "## Against the audio, on the videos that were transcribed" in text
    assert "2 videos, 0.6 hours of audio" in text
    assert "| retimer-audio | 2 | 50% |" in text
    assert "Head to head with `retimer-audio` on those pairs" in text
    assert "Deliberately wrong pairs against the audio of those videos." in text
    assert "| retimer-audio | 1 | 1 | 0 | 0 | 0 | 0 | 0 |" in text
    assert "wrote the transcripts of 2 videos, 0.6 hours of audio" in text
    # The tables over all pairs leave out a system that ran on a sample.
    by_kind = text.split("## Main pairs by kind of file: share verified")[1].split("##")[0]
    assert "| Kind of file | Pairs | input | retimer |\n" in by_kind
    outcomes = text.split("## What the retimer reported")[1].split("##")[0]
    assert "| Kind of pair | Outcome | retimer | retimer-audio |" in outcomes
    assert "| main | synced | 1 | 1 |" in outcomes


def test_a_video_without_a_transcript_is_not_run_against_the_audio(heard_set):
    root, manifest, speaker = heard_set
    transcribe(root, speaker, hours=1.0, limit_usd=1.5 * LIST_PRICE)
    done = load_speech(root).transcribed()
    assert len(done) == 1

    run_dir = run_systems(root, "part", ["retimer-audio"], workers=1)

    assert {manifest.video(pair.video).id for pair in manifest.pairs} == {"v1", "v2"}
    ran = {record["pair"] for record in load_results(run_dir)}
    assert ran == {pair.id for pair in manifest.pairs if pair.video in done}
    # Asked all the same, the cache-only client says what is missing and asks nobody.
    with pytest.raises(ProviderError, match="realset transcribe"):
        CachedOnly().transcribe(b"audio", filename="a.ogg", model="m", provider=None, language="ja")


def test_the_agent_is_run_when_it_is_named_and_needs_a_key(heard_set, monkeypatch):
    root, manifest, speaker = heard_set
    assert "retimer-agent" not in DEFAULT_SYSTEMS and "retimer-audio" in DEFAULT_SYSTEMS
    assert [system.name for system in SYSTEMS if system.agent] == [
        "retimer-agent",
        "retimer-agent-audio",
    ]
    agent, plain = (
        make_runner(system, root) for system in resolve(["retimer-agent", "retimer"], root)
    )
    assert (agent.model, agent.consult) == ("haiku", "auto")
    # Embeddings and transcripts come from the set, for the agent as for the pipeline.
    assert isinstance(agent.client, CachedOnly) and isinstance(plain.client, CachedOnly)
    assert agent.names_audio_language and plain.names_audio_language

    main_pairs = select_pairs(manifest, only="t1.v1s1")
    with pytest.raises(ValueError, match="asks a model: set OPENROUTER_API_KEY"):
        run_systems(root, "agent", ["retimer-agent"], pairs=main_pairs, workers=1)

    # This pair leaves nothing open, so the model is never asked and the key never used.
    monkeypatch.setenv("OPENROUTER_API_KEY", "not a key")
    run_dir = run_systems(root, "agent", ["retimer", "retimer-agent"], pairs=main_pairs, workers=1)
    records = {record["system"]: record for record in load_results(run_dir)}
    assert records["retimer-agent"]["outcome"] == "synced"
    assert records["retimer-agent"]["cost_usd"] == 0.0
    assert json.loads((run_dir / "run.json").read_text())["versions"]["retimer"].endswith(
        "effort medium, consult auto"
    )


def test_what_the_model_would_be_asked_is_recorded_without_asking_it(heard_set, monkeypatch):
    root, manifest, speaker = heard_set
    assert "retimer-questions" not in DEFAULT_SYSTEMS
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    pairs = select_pairs(manifest, only="t1.v1s1")
    run_dir = run_systems(root, "asked", ["retimer", "retimer-questions"], pairs=pairs, workers=1)

    records = {record["system"]: record for record in load_results(run_dir)}
    dry, plain = records["retimer-questions"], records["retimer"]
    assert dry["error"] is None
    assert dry["outcome"] == plain["outcome"] == "synced" and dry["cost_usd"] == 0.0
    assert dry["details"]["plan"] == plain["details"]["plan"]
    trace = dry["details"]["report"]["agent"]
    assert trace["questions"] == [] and trace["part_asked"] is None
    assert not trace["consulted"] and trace["model"] == "none"


def test_the_run_stops_asking_the_agent_at_its_limit(heard_set, monkeypatch):
    root, manifest, speaker = heard_set
    monkeypatch.setenv("OPENROUTER_API_KEY", "not a key")
    turn = threading.Lock()

    def costly(root, system, pair, out_dir):
        with turn:
            time.sleep(0.05)
            return {
                "pair": pair.id,
                "system": system.name,
                "kind": pair.kind,
                "outcome": "synced",
                "cost_usd": 0.6 if system.agent else 0.0,
            }

    monkeypatch.setattr(run_module, "_work", costly)
    monkeypatch.setattr(
        run_module, "ProcessPoolExecutor", lambda max_workers, mp_context: ThreadPoolExecutor(1)
    )
    told = []

    run_dir = run_systems(
        root, "dear", ["retimer-agent", "retimer"], agent_limit_usd=1.0, say=told.append
    )

    records = load_results(run_dir)
    asked = [record for record in records if record["system"] == "retimer-agent"]
    # Two jobs reach the limit. A third may have begun before the rest was dropped.
    assert 2 <= len(asked) <= 3
    assert any("its jobs that have not begun are dropped" in message for message in told)
    # Started again with the same limit, the agent is not asked at all. The other system is run.
    run_systems(root, "dear", ["retimer-agent", "retimer"], agent_limit_usd=1.0, say=told.append)
    assert any("none of its jobs are run" in message for message in told)
    records = load_results(run_dir)
    assert len([r for r in records if r["system"] == "retimer-agent"]) == len(asked)
    assert len([r for r in records if r["system"] == "retimer"]) == 3
    # With a higher limit the run is continued.
    run_systems(root, "dear", ["retimer-agent"], agent_limit_usd=5.0)
    assert len([r for r in load_results(run_dir) if r["system"] == "retimer-agent"]) == 3


def test_the_report_says_what_the_agent_changed_and_what_was_billed(heard_set):
    root, manifest, speaker = heard_set

    def record(pair, system, outcome, cost=0.0, kind="main", **trace):
        details = {"report": {"agent": trace or None}}
        return {
            "pair": pair,
            "system": system,
            "kind": kind,
            "outcome": outcome,
            "cost_usd": cost,
            "seconds": 1.0,
            "details": details,
        }

    results = [
        record("t1.v1s1", "retimer", "synced"),
        record("t1.v1s1", "retimer-agent", "synced", finished="not_needed", consulted=False),
        record("t1.v2s1", "retimer", "rejected_no_match", kind="wrong"),
        record("t1.v2s1", "retimer-agent", "partial", 0.0125, kind="wrong",
               finished="submitted", consulted=True, changed=True, cover=[4, 90]),
        record("t1.v2s1.main", "retimer-agent", "synced", finished="error", consulted=True),
    ]  # fmt: skip
    judgments = [
        {"pair": r["pair"], "system": r["system"], "kind": r["kind"], "expected": "placed",
         "verdict": "verified"}
        for r in results
    ]  # fmt: skip

    text = render(manifest, judgments, results, {}, {}, "made-up")

    changed = text.split("## Where `retimer-agent` came to another outcome than `retimer`")[1]
    assert "| wrong | rejected_no_match | partial | 1 |" in changed.split("##")[0]
    did = text.split("## What the agent did")[1].split("##")[0]
    assert "| System | Jobs | Model asked | not_needed | submitted |" in did
    assert "| retimer-agent | 3 | 2 | 1 | 1 | 0 | 0 | 0 | 1 | 1 | 1 |" in did
    assert "| declined | error | Plan changed | Cut down |" in did
    billed = text.split("## What was billed")[1]
    assert "| retimer-agent | 3 | 1 | 0.01 |" in billed
    assert "| retimer |" not in billed
    assert "## Against the audio" not in text


def test_the_transcribe_command(heard_set, monkeypatch, capsys, tmp_path):
    root, manifest, speaker = heard_set
    empty = tmp_path / "empty.key"
    empty.write_text("\n")
    assert main(["realset", "transcribe", str(root), "--key-file", str(empty)]) == 1
    assert "empty.key is empty" in capsys.readouterr().err
    missing = ["realset", "run", str(root), "--key-file", str(tmp_path / "no.key")]
    assert main(missing) == 1
    assert "the key file cannot be read" in capsys.readouterr().err

    key = tmp_path / "provider.key"
    key.write_text("a key from a file\n")
    monkeypatch.setattr("retimer.realset.speech.OpenRouter", lambda: speaker)
    monkeypatch.setattr("retimer.realset.speech.LocalEmbeddings", FakeClient)
    command = ["realset", "transcribe", str(root), "--hours", "1", "--key-file", str(key)]
    assert main([*command, "--limit-usd", "0.5"]) == 0
    shown = capsys.readouterr()
    assert "2 videos have a transcript, 0.6 hours of audio." in shown.out
    assert "0.0222 USD are counted against the limit of 0.50." in shown.out
    assert "a key from a file" not in shown.out + shown.err
