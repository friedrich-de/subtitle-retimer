# retimer-api

An API that synchronizes a subtitle file to a reference (audio, an in-sync
subtitle, or both). An AI agent diagnoses the file and chooses the operations;
deterministic tools do all the timing math.

The project is built in milestones. This repository holds the first four: the
subtitle core, the benchmark harness, the fixed pipeline that syncs a file
without an agent, and the agent that settles what the pipeline leaves open.
The HTTP API comes next.

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

It runs the routine steps in one set order and always takes the best-scoring
answer. It is the floor of the service, and the benchmark's measure of what the
agent adds.

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

On the 194 seed cases the pipeline syncs 87% of the files, against 43% for the
best existing tool. How it got there is in
[bench/baselines/2026-11-16-fixed-pipeline.md](bench/baselines/2026-11-16-fixed-pipeline.md).

## What the agent does

The agent starts where the fixed pipeline stops. The routine steps run first,
without a model, and their plan is the draft. A workbench then looks for what
the draft rests on too little:

- lines between two blocks, or at the start or the end of the file, that found
  no counterpart;
- a block with hardly anything certain behind it;
- a verdict on whether both sides are the same content that was reached by a
  small margin;
- no timing at all, because the parts that were placed cannot follow each
  other.

When nothing is open, no model is called. On the benchmark that is two files in
three. Otherwise the model gets a dossier: the job, what the routine steps
found, and every open question with the lines concerned next to the stretch of
the reference they can belong to. It has eight tools and never writes a time.
It says which line begins at which place in the reference, what happens to a
line at the edge of the file, which fit to go with, and whether the two sides
are the same content. Each answer is an adjustment to the pipeline, which then
builds and validates the plan as it always does.

What keeps a wrong answer from doing harm:

- Only open lines can be paired, pairs keep the order of both files, and a line
  the routine steps placed keeps its place.
- Pairs that would take two cuts which cancel each other out are taken back.
  That is what a stretch of a transcript with wrong times looks like. One line
  heard in a transcript is never a block of its own, and one pair with a
  reference subtitle does not move a line by the little that two subtitles
  differ anyway.
- A plan that puts fewer certain lines in place than the draft is dropped for
  the draft. The verdict of the routine steps is overruled only where they were
  in doubt.
- A job has a budget of eight model calls and one dollar. When it runs out, the
  plan so far is returned as `partial`. When the model declines or cannot be
  reached, the job gets the plan of the fixed pipeline.
- The text of subtitles and transcripts is data. The model is told not to
  follow instructions in it, and none of its tools could do harm if it did.

On the 194 seed cases:

| System | Files synced | At 500 ms | Decisions right | Cost per file |
| --- | --- | --- | --- | --- |
| Best existing tool | 43% | 45% | 16% | none |
| Fixed pipeline | 87% | 89% | 66% | none |
| Agent on Claude Haiku 5.5 | 94% | 98% | 100% | 0.03 cents |
| Agent on Claude Opus 5.5 | 95% | 98% | 100% | 0.6 cents |

The model is asked about 31% of the files and takes two turns and six seconds
on those. Haiku 5.5 is the default. What is left are block cases against audio
alone, where the limit is the transcript and not the model. The figures and
what is behind them are in
[bench/baselines/2026-11-16-agent.md](bench/baselines/2026-11-16-agent.md).

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
retimer sync episode.ja.srt --audio episode.ogg -o synced.srt --agent            # with the agent
retimer sync episode.ja.srt --audio episode.ogg -o synced.srt --agent opus --report report.json
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

`--agent` lets a model settle what the routine steps leave open. It takes
`haiku` (the default), `sonnet`, `opus` or a full model id, and needs
`ANTHROPIC_RETIMER_API_KEY` in the environment. `--agent-effort` sets how much
the model may think (default `medium`), and `--agent-always` asks it about
every file. The summary then says whether the model was asked, about what, how
it finished and what it cost, and the report holds every call it made.

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
project's fixed pipeline and needs `OPENROUTER_API_KEY`. `agent-haiku`,
`agent-sonnet` and `agent-opus` are the agent on each model. They also need
`ANTHROPIC_RETIMER_API_KEY` and run only when named, because every run costs
money: `retimer bench run --systems pipeline,agent-haiku --repeats 3`. A system
that cannot run is skipped and named in the summary.

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
| `retimer.fit` | Offset fits: constant, rate, blocks, rate plus blocks, with pairs a reader asserted |
| `retimer.speech` | Text-free alignment of line starts and speech onsets |
| `retimer.score` | The verification score, per block |
| `retimer.pipeline` | The fixed pipeline: reference, matching, fit, plan, report. Also builds a plan under a reader's adjustments |
| `retimer.agent.workbench` | The agent's desk: the draft, the open questions, the dossier and the tools |
| `retimer.agent.prompt` | What the model is told, and the tool definitions |
| `retimer.agent.loop` | The tool loop, budgets, fallbacks, and what goes into the report |
| `retimer.cli` | Command line entry point |
| `retimer.bench.distort` | Distortion generator with ground truth and the ideal plan |
| `retimer.bench.cases` | Case format, seed recipes and case generation |
| `retimer.bench.metrics` | Line error, tolerance shares, file success, decisions, stability |
| `retimer.bench.runners` | The systems under test: ffsubsync, alass, the pipeline, the agent, input and oracle |
| `retimer.bench.harness` | Runs systems over cases and stores the results |
| `retimer.bench.report` | Summary tables |
| `bench/seeds` | Seed material: two open films with English and Japanese subtitles |
