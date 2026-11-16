# retimer-api

An API that synchronizes a subtitle file to a reference (audio, an in-sync
subtitle, or both). An AI agent diagnoses the file and chooses the operations;
deterministic tools do all the timing math.

The project is built in milestones. This repository holds the first three: the
subtitle core, the benchmark harness, and the fixed pipeline that syncs a file
without an agent. The agent and the HTTP API come next.

## What the subtitle core does

- Reads and writes SRT and ASS, including legacy encodings such as Shift-JIS.
- Labels every line: dialogue, sign, song, sound description, credit, comment.
- Produces an inspection report that summarizes a file without quoting all of it.
- Turns request options (remove hearing-impaired text, karaoke, symbols,
  formatting) into content operations.
- Builds the match view: dialogue only, reduced to the spoken words.
- Validates and applies an operation plan. The plan is the only thing that
  changes a file.

## What the fixed pipeline does

It runs the tools the agent will use, in one set order, and always takes the
best-scoring answer. It is the floor of the service, and the benchmark's
measure of what the agent adds.

1. **Reference.** Audio is transcribed with word timestamps (Whisper large v3
   turbo, through OpenRouter, pinned to one upstream) and grouped into
   utterances. A reference subtitle is reduced to its dialogue. With both, the
   subtitle is first checked against the audio, and the audio wins.
2. **Matching.** Target lines and reference events are compared by meaning with
   a multilingual embedding model, so the two may be in different languages.
   Pairs that stand out from everything else, in the same order in both files,
   become anchors.
3. **Fitting.** The offsets of all plausible pairs are fitted as one shift, a
   frame rate change, block shifts, or rate plus blocks. Breaks only go where
   the lines stay in order.
4. **Plan.** The best fit becomes a plan. A file that is already in sync is left
   alone, another film is rejected, and trailing lines are trimmed when the
   reference confirms they are absent.
5. **Score.** The anchors are checked against the retimed file, per block. A
   block with too little behind it makes the outcome `partial`.

On the 194 seed cases the pipeline syncs 86% of the files, against 43% for the
best existing tool. The figures and what is behind them are in
[bench/baselines/2026-11-16-fixed-pipeline.md](bench/baselines/2026-11-16-fixed-pipeline.md).

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
retimer sync episode.ja.srt --audio episode.ogg -o synced.srt
retimer sync episode.ja.srt --reference episode.en.srt -o synced.srt --plan plan.json
```

`sync` takes audio, a reference subtitle, or both. It prints the outcome, the
blocks it moved and any warnings, and can write the plan and the full report
next to the result. It exits with 3 and writes no file when the reference is
other content.

`sync` is the only command that calls a hosted service. It needs
`OPENROUTER_API_KEY` in the environment and `ffmpeg` for audio. Transcripts and
embeddings are cached by content under `data/cache` (or `RETIMER_CACHE_DIR`),
so a second subtitle for the same audio costs no second transcription. A
quarter hour of audio costs about one cent to transcribe and takes about 20
seconds; the embeddings cost a small fraction of a cent.

## Benchmark

The benchmark turns in-sync subtitles into mistimed ones in known ways, runs
every system on them, and measures how close each line lands to its true time.
It is how every later choice (speech model, embedding model, agent model) gets
decided. Details are in [bench/README.md](bench/README.md).

```bash
retimer bench fetch-seeds     # download the audio of the two seed films (once)
retimer bench run             # generate the cases, run every system, print the summary
retimer bench run --systems pipeline --only tears-of-steel.ja --repeats 3
retimer bench report data/bench/results/<run>
retimer bench systems         # which systems can run on this machine
```

`ffsubsync` and `alass-cli` are the baselines. Install them to include them
(`pipx install ffsubsync`, `cargo install alass-cli`). `pipeline` is this
project's fixed pipeline and needs `OPENROUTER_API_KEY`. A system that cannot
run is skipped and named in the summary.

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
| `retimer.providers` | The OpenRouter client for speech-to-text and embeddings |
| `retimer.cache` | Cache by content hash for transcripts and embeddings |
| `retimer.audio` | Probing audio and cutting long files at quiet stretches |
| `retimer.transcribe` | Transcripts with word timestamps, chunked and cached |
| `retimer.reference` | The reference timeline, from a transcript or a subtitle |
| `retimer.embed` | Cached text embeddings |
| `retimer.match` | Anchors and the table of how alike every line and event are |
| `retimer.fit` | Offset fits: constant, rate, blocks, rate plus blocks |
| `retimer.speech` | Text-free alignment of line starts and speech onsets |
| `retimer.score` | The verification score, per block |
| `retimer.pipeline` | The fixed pipeline: reference, matching, fit, plan, report |
| `retimer.cli` | Command line entry point |
| `retimer.bench.distort` | Distortion generator with ground truth and the ideal plan |
| `retimer.bench.cases` | Case format, seed recipes and case generation |
| `retimer.bench.metrics` | Line error, tolerance shares, file success, decisions, stability |
| `retimer.bench.runners` | The systems under test: ffsubsync, alass, the pipeline, input and oracle |
| `retimer.bench.harness` | Runs systems over cases and stores the results |
| `retimer.bench.report` | Summary tables |
| `bench/seeds` | Seed material: two open films with English and Japanese subtitles |
