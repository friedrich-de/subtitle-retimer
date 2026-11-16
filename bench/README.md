# Benchmark

A set of cases with known-correct timing, and one harness that runs every
system on the same cases.

## How a case is made

A seed is audio plus subtitles that are in sync with it, ideally in two
languages. The generator distorts one of those subtitles in a known way, so the
true start of every line is known exactly. Each distortion also comes with its
ideal plan, the plan that undoes it in the engine's own vocabulary.

| Family | Distortion | Expected outcome |
| --- | --- | --- |
| `none` | Nothing | `already_in_sync` |
| `constant` | One offset, 0.2 s to 60 s, early and late | `synced` |
| `rate` | 23.976 to 25 fps and back, 24 to 25 fps | `synced` |
| `blocks` | 1 to 5 breaks, each a segment of 10 s to 180 s inserted or cut | `synced` |
| `mixed` | Rate, offset and breaks together | `synced` |
| `noise` | Added sound descriptions, credits, a song block, trailing lines | `synced`, noise handled as the options say |
| `wrong_content` | Another film as the reference | `rejected_no_match` |

A break models two cuts of the same film. An inserted segment pushes every
later line back. A cut removes the lines inside it, because that part does not
exist in the target's version, and pulls every later line forward. Break points
sit in gaps between lines and every block keeps a few lines. On a short file a
cut is shortened to fit.

Every seed is synced against its audio, against a subtitle in another language,
and (for the first language) against both.

## Case format

A case is a directory with the target subtitle and a `case.json`:

```json
{
  "id": "tears-of-steel.ja.subtitle-en.blocks-2",
  "family": "blocks",
  "target": "target.srt",
  "target_language": "ja",
  "reference": {"kind": "subtitle", "subtitle": "../../../../bench/seeds/tears-of-steel/en.srt", "subtitle_language": "en"},
  "options": {"remove_hearing_impaired": false, "drop_unmatched": true},
  "expected_outcome": "synced",
  "truth": [{"id": 0, "kind": "base", "label": "dialogue", "true_start_ms": 23000, "true_end_ms": 24500}]
}
```

`truth` has one entry per line of the target, by position in the file. A line
with no `true_start_ms` has no place in the reference (a trailing ad, say) and
is expected to be trimmed. Paths are relative to the case directory.

A naturally mistimed file becomes a case the same way: write the directory by
hand, with `truth` taken from a hand-checked copy. Generated cases live under
`data/bench/cases`, which is not in the repository; `retimer bench generate`
rebuilds them and always produces the same set.

## Metrics

| Metric | Definition |
| --- | --- |
| Line error | Distance between the start of a dialogue line after syncing and its true start. Median and 90th percentile per file. |
| Within tolerance | Share of dialogue lines within 100 ms, 250 ms and 500 ms. A dialogue line the system removed counts as outside. |
| File success | At least 95% of a file's dialogue lines within 250 ms. For a wrong-content case: the system rejected it. |
| At 500 ms | The same with 500 ms. See the note on audio below. |
| Decisions right | Correct outcome on the in-sync and wrong-content cases, and trailing lines trimmed without losing dialogue. |
| Time and cost | Wall-clock seconds and provider cost per file. |
| Stability | With `--repeats`, how often repeated runs agree and how far their median errors spread. |

The summary reports medians over files for the error columns, so a long file
does not outweigh the rest, and pools all dialogue lines for the tolerance
columns. It breaks every figure down by distortion family and by reference
kind.

The truth is the timing a person gave the seed subtitle, and a person does not
time to the sound exactly. Measured against the sound itself, the Tears of
Steel subtitles appear about a quarter of a second before the voice sets in,
and the Sintel subtitles about a quarter of a second after. A system that
places every line exactly on the voice is therefore up to 250 ms off the truth
on audio cases, right at the edge of the strict tolerance. That is why the
figure at 500 ms is reported next to it. Against a reference subtitle the
question does not arise.

Tools that only retime do not report an outcome. They count as having left a
file alone when no line moved more than 100 ms, and as having synced it
otherwise. Their output is matched back to the target by line text, so a tool
may sort the file.

## Systems

| System | What it is |
| --- | --- |
| `input` | The target, untouched. Shows how far off the cases are to begin with. |
| `oracle` | Applies the ideal plan. Must score 100%; it checks the harness itself. |
| `ffsubsync` | ffsubsync with its defaults: one offset and a frame rate ratio for the whole file. |
| `ffsubsync-split` | ffsubsync with `--split-penalty`, which also looks for breaks. |
| `alass` | alass with its defaults: finds splits and guesses the frame rate ratio. |
| `pipeline` | This project's fixed pipeline: transcript or reference subtitle, anchors by meaning, offset fits, plan. No agent. Needs `OPENROUTER_API_KEY`. |
| `agent-haiku`, `agent-sonnet`, `agent-opus` | The pipeline with a model settling what its draft leaves open, on Claude Haiku 5.5, Sonnet 5.5 or Opus 5.5. Also needs `ANTHROPIC_RETIMER_API_KEY`. |

The external tools take one reference. When a case has both audio and a
reference subtitle they get the audio. A tool is found on the `PATH`, or
through `RETIMER_FFSUBSYNC` and `RETIMER_ALASS`.

The pipeline caches transcripts and embeddings, so a rerun over the same audio
costs nothing and its time and cost columns then only show what was new. Set
`RETIMER_CACHE_DIR` to an empty directory to measure a first run.

The agent systems are not part of the default run, because they cost money on
every run: name them with `--systems`. What a model answers is not cached, so
their cost column is what the model cost on that run, computed from the token
counts and the list prices. The routine steps are the same as the pipeline's,
which makes the difference between `pipeline` and an agent system what the
model adds. The summary has a table of its own for them: how many files the
model was asked about at all, how many turns, seconds and dollars that took,
and how often its changes were kept back. Run them with `--repeats`, since a
model does not answer the same way twice.

A new system is a class with `name`, `unavailable()`, `supports(case)` and
`run(case, work_dir)` that returns a `RunOutput`, added in
`retimer.bench.cli.all_runners`.

## Results

Each run writes a directory under `data/bench/results`:

- `results.jsonl`: one record per case, system and repeat, with all metrics.
- `summary.md` and `summary.json`: the tables.
- `run.json`: when it ran and the tool versions.
- `outputs/<system>/<case>/`: the file each system produced.
