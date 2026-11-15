# retimer-api

An API that synchronizes a subtitle file to a reference (audio, an in-sync
subtitle, or both). An AI agent diagnoses the file and chooses the operations;
deterministic tools do all the timing math.

The project is built in milestones. This repository currently holds the first
two: the subtitle core and the benchmark harness. Neither involves AI or needs
API keys.

## What the subtitle core does

- Reads and writes SRT and ASS, including legacy encodings such as Shift-JIS.
- Labels every line: dialogue, sign, song, sound description, credit, comment.
- Produces an inspection report that summarizes a file without quoting all of it.
- Turns request options (remove hearing-impaired text, karaoke, symbols,
  formatting) into content operations.
- Builds the match view: dialogue only, reduced to the spoken words.
- Validates and applies an operation plan. The plan is the only thing that
  changes a file.

## Setup

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

Python 3.11 or newer.

## Command line

```bash
retimer inspect episode.ass                      # the inspection report as JSON
retimer sample episode.ass --label sign          # look at lines by label, style or pattern
retimer clean episode.srt --remove-hearing-impaired -o cleaned.srt
retimer apply episode.ass plan.json -o synced.srt --format srt
```

## Benchmark

The benchmark turns in-sync subtitles into mistimed ones in known ways, runs
every system on them, and measures how close each line lands to its true time.
It is how every later choice (speech model, embedding model, agent model) gets
decided. Details are in [bench/README.md](bench/README.md).

```bash
retimer bench fetch-seeds     # download the audio of the two seed films (once)
retimer bench run             # generate the cases, run every system, print the summary
retimer bench run --systems alass --only tears-of-steel.ja --repeats 3
retimer bench report data/bench/results/<run>
retimer bench systems         # which systems can run on this machine
```

`ffsubsync` and `alass-cli` are the baselines. Install them to include them
(`pipx install ffsubsync`, `cargo install alass-cli`); a system that is not
installed is skipped and named in the summary.

## The plan

```json
{
  "outcome": "synced",
  "content_ops": [
    {"op": "remove_lines", "reason": "karaoke", "ids": [1, 2, 10]},
    {"op": "strip_text", "what": "hearing_impaired"}
  ],
  "timing_ops": [
    {"op": "scale", "factor": 1.04271, "reason": "25 fps source, 23.976 fps reference"},
    {"op": "shift", "from": "start", "to": "00:11:42.300", "ms": 1210},
    {"op": "shift", "from": "00:11:42.300", "to": "end", "ms": 14830}
  ]
}
```

Rules the engine enforces:

- Line ids are positions in the original file and never change.
- Every time range refers to line start times in the original file. `scale` is
  applied first, then each line gets the shift of the range it started in, so
  the order of operations in the list does not matter.
- `shift` keeps line durations. `scale` is the only operation that changes them,
  and at most one is allowed.
- Ranges may not overlap, no line may start before zero, and no line may move in
  front of a line that precedes it. A plan that breaks a rule is refused with a
  list of issues naming the lines involved.

## Layout

| Module | Purpose |
| --- | --- |
| `retimer.io` | Decode, parse and write subtitle files |
| `retimer.model` | The in-memory subtitle and its lines |
| `retimer.classify` | Rule-based line labels with confidence and reasons |
| `retimer.inspection` | Inspection report and line sampling |
| `retimer.clean` | Text cleaning functions |
| `retimer.views` | Content operations from options, and the match view |
| `retimer.plan` | The plan schema |
| `retimer.engine` | Plan validation and application |
| `retimer.cli` | Command line entry point |
| `retimer.bench.distort` | Distortion generator with ground truth and the ideal plan |
| `retimer.bench.cases` | Case format, seed recipes and case generation |
| `retimer.bench.metrics` | Line error, tolerance shares, file success, decisions, stability |
| `retimer.bench.runners` | The systems under test: ffsubsync, alass, input and oracle |
| `retimer.bench.harness` | Runs systems over cases and stores the results |
| `retimer.bench.report` | Summary tables |
| `bench/seeds` | Seed material: two open films with English and Japanese subtitles |
