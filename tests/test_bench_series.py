import json
import re
from itertools import pairwise
from types import SimpleNamespace

import pytest

from retimer import load, loads
from retimer.bench.cases import generate_cases, load_seeds
from retimer.bench.series import (
    PARTS,
    SCRIPT_FILE,
    Beat,
    Series,
    SeriesError,
    Writer,
    _wrap,
    captions,
    lay_out,
    load_series,
    morae,
    parse_beats,
    save_series,
    write_outline,
    write_scene,
    write_series,
)
from retimer.cli import main

from .bench_helpers import write_seeds

WRITTEN = """JA: 最終便、いきまーす！
EN: Last crossing of the day, here we go!

JA: まだ決めてない。 || でも、必ず残す。
EN: I haven't decided yet, but we're keeping it.

Here is a remark the model was not asked for.
JA: 相手のいない行
JA： 東の空、尾根から二十度ほど上。
EN: East, about twenty degrees above the ridge. || It seems to be getting brighter.
EN: A subtitle without its caption.
"""

SAID = [
    "えっ？",
    "待って！",
    "今日は川、機嫌がいいね。",
    "根っこが腐ってたら、水をやっても意味がないんだよ。",
]


def beat(number: int) -> Beat:
    said = SAID[number % len(SAID)]
    if number % 5 == 3:
        return Beat(ja=[said], en=[f"First half of {number},", "and the second half of it."])
    if number % 5 == 4:
        return Beat(
            ja=[said, "それでも行く。"], en=[f"Two captions, one subtitle, number {number}."]
        )
    return Beat(ja=[said], en=[f"This is what line {number} says."])


def outline(episodes: int = 2) -> dict:
    return {
        "slug": "made-up",
        "title_ja": "作り話",
        "title_en": "Made Up",
        "premise": "A show that exists for the tests.",
        "characters": [{"name_ja": "春", "name_en": "Haru", "role": "Talks."}],
        "narration": [
            {"ja": ["屋上に、今日も風が吹きます。"], "en": ["The wind blows on the roof again."]},
            {"ja": ["トマトは、まだ赤くありません。"], "en": ["The tomatoes are not red yet."]},
            {"ja": ["さあ、始めましょう。"], "en": ["Well then, let us begin."]},
        ],
        "sign_off": {"ja": ["つづきは、また屋上で。"], "en": ["Continued on the roof."]},
        "episodes": [
            {
                "number": number,
                "title_ja": f"第{number}話",
                "title_en": f"Part {number}",
                "scenes": [
                    {"part": part, "summary": f"Scene {index} of the {part} part."}
                    for part, count, _ in PARTS
                    for index in range(count)
                ],
            }
            for number in range(1, episodes + 1)
        ],
    }


def make_series(episodes: int = 2, beats: int = 12) -> Series:
    series = Series.model_validate(outline(episodes))
    number = 0
    for episode in series.episodes:
        for scene in episode.scenes:
            scene.beats = [beat(number + index) for index in range(beats)]
            number += beats
    return series


class FakeWriter:
    """Answers as the model would: an outline as JSON, a scene as beats."""

    def __init__(self, beats: int | None = None, outlines: list[str] | None = None) -> None:
        self.beats = beats
        self.outlines = outlines
        self.asked: list[str] = []

    def ask(self, prompt: str, max_tokens: int = 16_000) -> str:
        self.asked.append(prompt)
        if "Invent the series" in prompt:
            if self.outlines:
                return self.outlines.pop(0)
            return f"Here it is.\n```json\n{json.dumps(outline(), ensure_ascii=False)}\n```"
        wanted = int(re.search(r"Write it as (\d+) beats", prompt).group(1))
        count = self.beats if self.beats is not None else wanted
        return "\n\n".join(
            f"JA: {number}番目のセリフ。\nEN: Line {number}." for number in range(count)
        )


def test_beats_are_read_as_the_model_writes_them():
    beats = parse_beats(WRITTEN)
    assert [(len(item.ja), len(item.en)) for item in beats] == [(1, 1), (2, 1), (1, 2)]
    assert beats[1].ja == ["まだ決めてない。", "でも、必ず残す。"]
    assert beats[2].ja == ["東の空、尾根から二十度ほど上。"]
    assert beats[2].en[1] == "It seems to be getting brighter."
    assert parse_beats("nothing of the kind") == []


def test_a_kanji_takes_longer_to_say_than_a_kana():
    assert morae("うん") == 2
    assert morae("今日は") == 5
    assert morae("きょうは") == 3
    assert morae("……。") == 0


def test_an_episode_is_laid_out_like_a_broadcast():
    series = make_series()
    episode = series.episodes[0]
    spoken = lay_out(series, episode)
    assert len(spoken) == 3 + 12 * 12 + 1
    assert spoken[-1].beat == series.sign_off
    assert all(earlier.end_ms < later.start_ms for earlier, later in pairwise(spoken))
    quiet = sorted(later.start_ms - earlier.end_ms for earlier, later in pairwise(spoken))
    # The opening song and the ending song, and the card between the two halves.
    assert quiet[-3] >= 12_500 and quiet[-2] >= 92_500
    assert quiet[-4] < 90_000
    assert spoken == lay_out(series, episode)
    assert spoken != lay_out(series, episode, seed=1)


def test_the_narration_has_the_same_times_in_every_episode():
    series = make_series()
    first, second = (lay_out(series, episode) for episode in series.episodes)
    times = [[(item.start_ms, item.end_ms) for item in spoken] for spoken in (first, second)]
    assert times[0][:3] == times[1][:3]
    assert times[0][3:] != times[1][3:]


def test_both_languages_follow_the_same_speech_and_cut_it_their_own_way():
    series = make_series()
    spoken = lay_out(series, series.episodes[0])
    ja, en = captions(spoken, "ja"), captions(spoken, "en")
    assert len(ja) == sum(len(item.beat.ja) for item in spoken)
    assert len(en) == sum(len(item.beat.en) for item in spoken)
    assert len(ja) != len(en) or [line.start_ms for line in ja.lines] != [
        line.start_ms for line in en.lines
    ]
    for subtitle, language, scatter in ((ja, "ja", 0), (en, "en", 80)):
        lines = iter(subtitle.lines)
        for item in spoken:
            pieces = [next(lines) for _ in getattr(item.beat, language)]
            assert abs(pieces[0].start_ms - item.start_ms) <= scatter
            assert pieces[-1].end_ms >= item.end_ms
            assert all(piece.duration_ms >= 300 for piece in pieces)
        assert all(a.end_ms <= b.start_ms for a, b in pairwise(subtitle.lines))
    assert captions(spoken, "en").lines == en.lines
    assert captions(spoken, "en", seed=1).lines != en.lines


def test_a_long_line_is_shown_in_two_rows():
    assert _wrap("短い行", "ja") == "短い行"
    long = "月の川には、まだ渡し舟が一艘だけ残っている。"
    assert _wrap(long, "ja") == "月の川には、\\Nまだ渡し舟が一艘だけ残っている。"
    assert _wrap("切れ目のない長い長い長い長い長い長い行", "ja").count("\\N") == 1
    said = "On the Tsuki River, one ferry boat is still left."
    assert _wrap(said, "en") == "On the Tsuki River, one\\Nferry boat is still left."
    assert _wrap("  Short   one. ", "en") == "Short one."


def test_a_series_is_saved_as_seeds_with_its_script(tmp_path):
    seeds_dir = write_seeds(tmp_path / "seeds")
    series = make_series(episodes=3)
    seeds = save_series(series, seeds_dir)
    assert [seed.id for seed in seeds] == ["made-up-01", "made-up-02", "made-up-03"]
    listed = load_seeds(seeds_dir).seeds
    assert [seed.id for seed in listed] == ["alpha", "beta", *(seed.id for seed in seeds)]
    assert all(seed.series == "made-up" and seed.audio is None for seed in seeds)
    assert "written by a language model" in seeds[0].attribution
    # English comes first: it is the reference that the Japanese is synced to.
    assert list(seeds[0].subtitles) == ["en", "ja"]
    japanese = load(seeds_dir / seeds[0].subtitles["ja"])
    assert japanese.format == "srt" and len(japanese) > 100
    assert load_series(seeds_dir / "made-up" / SCRIPT_FILE) == series

    # Saving it again replaces it. The films keep their place and their fields.
    save_series(series, seeds_dir)
    again = json.loads((seeds_dir / "seeds.json").read_text())["seeds"]
    assert len(again) == 5 and "series" not in again[0]

    ids = generate_cases(seeds_dir, tmp_path / "media", tmp_path / "cases", recipes=[])
    assert "made-up-03.en.subtitle.wrong-content-made-up-01" in ids
    assert "beta.en.subtitle.wrong-content-alpha" in ids


def test_the_series_is_written_scene_by_scene():
    writer = FakeWriter()
    series = write_series(writer, episodes=2)
    assert [episode.number for episode in series.episodes] == [1, 2]
    wanted = [beats for _, count, beats in PARTS for _ in range(count)]
    assert [len(scene.beats) for scene in series.episodes[0].scenes] == wanted

    scenes = [prompt for prompt in writer.asked if "Write scene" in prompt]
    assert len(scenes) == 2 * len(wanted)
    first = next(
        prompt for prompt in scenes if "This is episode 1" in prompt and "scene 1:" in prompt
    )
    second = next(
        prompt for prompt in scenes if "This is episode 1" in prompt and "scene 2:" in prompt
    )
    # A scene is shown how the one before it ended, and who is in the series.
    assert "<previous>" not in first and "<previous>" in second
    assert "19番目のセリフ。" in second and "春 (Haru)" in second
    preview = next(prompt for prompt in scenes if "scene 12:" in prompt)
    assert "Do not write the closing line" in preview and "つづきは、また屋上で。" in preview


def test_narration_given_as_one_beat_of_three_lines_is_taken_apart():
    told = outline()
    lines = told["narration"]
    told["narration"] = [
        {"ja": [line["ja"][0] for line in lines], "en": [line["en"][0] for line in lines]}
    ]
    series = write_outline(FakeWriter(outlines=[json.dumps(told)]), episodes=2)
    assert [item.ja for item in series.narration] == [line["ja"] for line in lines]


def test_an_outline_that_does_not_fit_is_asked_for_again():
    short = outline()
    short["episodes"][0]["scenes"].pop()
    named = outline()
    named["slug"] = "Not A Slug"
    good = json.dumps(outline())
    writer = FakeWriter(outlines=["I would rather not.", json.dumps(short), good])
    assert write_outline(writer, episodes=2).slug == "made-up"
    assert len(writer.asked) == 3
    with pytest.raises(SeriesError, match="slug"):
        write_outline(FakeWriter(outlines=[json.dumps(named)] * 3), episodes=2)
    with pytest.raises(SeriesError, match="3 episodes"):
        write_outline(FakeWriter(outlines=[json.dumps(outline(3))] * 3), episodes=2)


def test_a_scene_that_stays_too_short_is_an_error():
    series = Series.model_validate(outline())
    writer = FakeWriter(beats=4)
    with pytest.raises(SeriesError, match="4 beats instead of 20"):
        write_scene(writer, series, series.episodes[0], 0)
    assert len(writer.asked) == 3
    # A scene that is a little short is kept as it is.
    nearly = FakeWriter(beats=17)
    assert len(write_scene(nearly, series, series.episodes[0], 0)) == 17
    assert len(nearly.asked) == 1


class FakeStream:
    def __init__(self, stop_reason: str) -> None:
        self.stop_reason = stop_reason

    def __enter__(self):
        return self

    def __exit__(self, *error) -> None:
        return None

    def get_final_message(self):
        return SimpleNamespace(
            usage=SimpleNamespace(input_tokens=1_000, output_tokens=2_000),
            stop_reason=self.stop_reason,
            content=[
                SimpleNamespace(type="thinking", thinking="A scene on a roof."),
                SimpleNamespace(type="text", text="JA: うん。\nEN: Yeah."),
            ],
        )


class FakeClient:
    def __init__(self, stop_reason: str = "end_turn") -> None:
        self.sent: list[dict] = []
        self.messages = SimpleNamespace(stream=self.stream)
        self.stop_reason = stop_reason

    def stream(self, **params):
        self.sent.append(params)
        return FakeStream(self.stop_reason)


def test_the_writer_asks_through_the_projects_own_route(monkeypatch):
    monkeypatch.delenv("RETIMER_AGENT_PROVIDER", raising=False)
    client = FakeClient()
    writer = Writer(client=client)
    assert writer.ask("Write a scene.") == "JA: うん。\nEN: Yeah."
    sent = client.sent[0]
    assert sent["model"] == "anthropic/claude-haiku-5.5"
    assert sent["extra_body"] == {"provider": {"only": ["anthropic"], "allow_fallbacks": False}}
    assert sent["messages"] == [{"role": "user", "content": "Write a scene."}]
    assert "your own invention" in sent["system"]
    assert writer.tokens == {"input": 1_000, "output": 2_000}
    assert writer.cost_usd == pytest.approx(0.0011)

    monkeypatch.setenv("RETIMER_AGENT_PROVIDER", "anthropic")
    direct = FakeClient()
    Writer(client=direct).ask("Write a scene.")
    assert direct.sent[0]["model"] == "claude-haiku-5-5" and "extra_body" not in direct.sent[0]

    with pytest.raises(SeriesError, match="max_tokens"):
        Writer(client=FakeClient("max_tokens")).ask("Write a scene.")


def test_the_times_of_a_series_can_be_drawn_again(tmp_path, capsys):
    seeds_dir = write_seeds(tmp_path / "seeds")
    series = make_series()
    save_series(series, seeds_dir)
    path = seeds_dir / "made-up" / "01" / "ja.srt"
    before = path.read_text(encoding="utf-8")

    assert main(["bench", "time-series", "made-up", "--seeds", str(seeds_dir), "--seed", "3"]) == 0
    assert "made-up-01: 148 beats" in capsys.readouterr().err
    after = load(path)
    assert path.read_text(encoding="utf-8") != before
    assert [line.text for line in after.lines] == [line.text for line in loads(before).lines]
    assert main(["bench", "time-series", "made-up", "--seeds", str(seeds_dir)]) == 0
    assert path.read_text(encoding="utf-8") == before

    assert main(["bench", "time-series", "no-such-series", "--seeds", str(seeds_dir)]) == 1
    assert "error" in capsys.readouterr().err


def test_writing_a_series_needs_the_key(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("RETIMER_AGENT_PROVIDER", raising=False)
    seeds_dir = write_seeds(tmp_path / "seeds")
    assert main(["bench", "write-series", "--seeds", str(seeds_dir)]) == 1
    assert "OPENROUTER_API_KEY is not set" in capsys.readouterr().err
    assert len(load_seeds(seeds_dir).seeds) == 2
