# Development

Setting up a checkout, the command line, the benchmark, and where the code is.

## Setup

```bash
uv sync --extra dev        # or: python -m venv .venv && .venv/bin/pip install -e ".[dev]"
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

Python 3.11 or newer, and `ffmpeg` for the tests that read audio. These three
checks are what a change has to pass; run them before you open a pull request.

## Command line

```bash
retimer serve --dev                              # the HTTP API, see http-api.md
retimer inspect episode.ass                      # the inspection report as JSON
retimer sample episode.ass --label sign          # look at lines by label, style or pattern
retimer clean episode.srt --remove-hearing-impaired -o cleaned.srt
retimer apply episode.ass plan.json -o synced.srt --format srt
retimer sync episode.ja.srt --audio episode.ogg -o synced.srt
retimer sync episode.ja.srt --reference episode.en.srt -o synced.srt --plan plan.json
retimer sync episode.ja.srt --reference episode.en.ass -o synced.srt --matched-reference pair.en.ass
retimer sync episode.ja.srt --reference track2.ass --reference track3.ass -o synced.srt
retimer sync episode.ja.srt --audio episode.ogg -o synced.srt --agent opus --report report.json
```

`sync` takes audio, a reference subtitle, or both. It prints the outcome, the
blocks it moved and any warnings, and can write the plan and the full report
next to the result. It exits with 3 and writes no file when the reference is
other content. `--reference` may be given up to six times, and the summary
then says [which one was taken](how-it-works.md#several-reference-subtitles) and why.
`--matched-reference` also writes
[the matched reference](how-it-works.md#the-matched-reference): the reference subtitle,
reduced to the dialogue lines the synced file has a counterpart for.
`--remove-signs-and-credits` drops signs and credits from the output, here
and in `clean`.

`sync` is the only command that calls a hosted service. It needs
`OPENROUTER_API_KEY` in the environment and `ffmpeg` for audio. Transcripts and
embeddings are cached by content under `data/cache` (or `RETIMER_CACHE_DIR`),
so a second subtitle for the same audio costs no second transcription. A
quarter hour of audio costs about one cent to transcribe and takes about 20
seconds; the embeddings cost a small fraction of a cent.

A model settles what the routine steps leave open, and `sync` does not run
without the key for it. `--agent` names the model: `haiku` (the default),
`sonnet`, `opus` or a full model id. The model is
called through OpenRouter with the same `OPENROUTER_API_KEY`, or at Anthropic
itself with `RETIMER_AGENT_PROVIDER=anthropic` and
`ANTHROPIC_RETIMER_API_KEY`. `--agent-effort` sets how much
the model may think (default `medium`), and `--agent-always` asks it about
every file. The summary then says whether the model was asked, about what, how
it finished and what it cost, and the report holds every call it made.

## Benchmark

The benchmark turns in-sync subtitles into mistimed ones in known ways, runs
every system on them, and measures how close each line lands to its true time.
It is how every later choice (speech model, embedding model, agent model) gets
decided. Details are in [bench/README.md](../bench/README.md).

```bash
retimer bench fetch-seeds     # download the audio of the two seed films (once)
retimer bench run             # generate the cases, run every system, print the summary
retimer bench run --systems pipeline --only tears-of-steel.ja --repeats 3
retimer bench run --family pack,next_episode,karaoke   # only some kinds of cases
retimer bench report data/bench/results/<run>
retimer bench systems         # which systems can run on this machine
retimer bench soundcheck episode.ogg synced.srt   # do the lines start where a voice sets in?
```

`ffsubsync` and `alass-cli` are the baselines. Install them to include them
(`pipx install ffsubsync`, `cargo install alass-cli`). `pipeline` is this
project's fixed pipeline alone, as a diagnostic, and needs
`OPENROUTER_API_KEY`. `agent-haiku`, `agent-sonnet` and `agent-opus` are the
retimer as it is delivered, on each model. They call the
model the way the service does (`RETIMER_AGENT_PROVIDER`) and run only when
named, because every run costs
money: `retimer bench run --systems pipeline,agent-haiku --repeats 3`. A system
that cannot run is skipped and named in the summary.

`soundcheck` is a yardstick for files that have no known-correct timing. It
compares the starts of the dialogue lines with the sound itself, without a
speech model, a transcript or a reference subtitle, and says for the whole
file and for every three minutes how far the lines are off and how far that
reading stands above chance. It tells whether lines are within about half a
second of the voices, which is enough to compare tools on real files, and not
more. [bench/README.md](../bench/README.md#the-sound-check) has what was measured.

### Real files

The benchmark's cases are made, also those that replicate what real files
do. How a system does on the files people really sync is measured on a set
built from your own videos and from Jimaku: every subtitle track and the audio of each video, and the
Japanese subtitle files shared for the same series. The set is kept outside
the repository, because it names releases.

```bash
retimer realset build SET --videos VIDEOS --jimaku-key-file KEY   # tracks, audio, targets, pairs
retimer realset embed SET     # every line's embedding, with a local model, into the set's cache
retimer realset tune SET      # choose settings for alass and ffsubsync on a tenth of the pairs
retimer realset transcribe SET --key-file KEY   # transcripts for a sample of the videos, within a spending limit
retimer realset run SET       # the retimer, alass and ffsubsync over every pair
retimer realset judge SET     # window check, sound check, and how far each system moved the lines
retimer realset report SET
```

No output of a real file has a known truth, so each is held against three
witnesses: lines that clearly mean the same in the output and in the video's
own subtitle track, the sound check, and the places where the systems disagree
with each other. A run reads its embeddings and transcripts from the set and
asks no provider. The agent is the exception: it is run when it is named,
`--systems retimer-agent`, and stops asking its model at a limit.
[bench/realset/README.md](../bench/realset/README.md) has the details.

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
| `retimer.providers` | The OpenRouter client for speech-to-text, embeddings and reading pictures |
| `retimer.pgs` | Blu-ray image subtitles: the pictures of a stream and when each is shown |
| `retimer.ocr` | Reading the pictures of an image subtitle into a subtitle with text |
| `retimer.unpack` | Getting an image subtitle out of the archive it came in |
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
| `retimer.choice` | Which of several reference subtitles a target is synced against, and why |
| `retimer.counterpart` | The matched reference: which lines of the reference the target has a counterpart for |
| `retimer.agent.workbench` | The agent's desk: the draft, the open questions, the dossier and the tools |
| `retimer.agent.prompt` | What the model is told, and the tool definitions |
| `retimer.agent.loop` | The tool loop, budgets, fallbacks, and what goes into the report |
| `retimer.api.app` | The HTTP API: the gate every request passes, the routes, the intake of uploads |
| `retimer.api.auth` | API keys: making, hashing and checking them |
| `retimer.api.limits` | Rate limits |
| `retimer.api.store` | Jobs in SQLite and their files, the queue, expiry |
| `retimer.api.worker` | Runs queued jobs and cleans up |
| `retimer.api.config` | Settings from the environment |
| `retimer.api.server` | Start-up checks, and logs that keep secrets out |
| `retimer.cli` | Command line entry point |
| `retimer.bench.distort` | Distortion generator with ground truth and the ideal plan |
| `retimer.bench.cases` | Case format, seed recipes and case generation |
| `retimer.bench.realfile` | The cases that replicate what real files did: packs, the next episode, karaoke scripts, captions and more |
| `retimer.bench.series` | The made-up series: a model writes the lines, a rule makes the times |
| `retimer.bench.metrics` | Line error, tolerance shares, file success, decisions, stability |
| `retimer.bench.runners` | The systems under test: ffsubsync, alass, the pipeline, the agent, input and oracle |
| `retimer.bench.harness` | Runs systems over cases and stores the results |
| `retimer.bench.report` | Summary tables |
| `retimer.bench.soundcheck` | The sound check: line starts against the voices in the audio, without a model |
| `retimer.realset.build` | Builds a real-file set: tracks and audio out of videos, targets from Jimaku |
| `retimer.realset.names` | What a file name says: the episode, and the kind of file |
| `retimer.realset.pairs` | The roles of tracks, which file is for which video, and the pairs |
| `retimer.realset.manifest` | The manifest of a set, its overrides, and its packed tracks |
| `retimer.realset.embedding` | A local stand-in for the embedding provider, and the set's cache |
| `retimer.realset.speech` | Has a sample of a set's audio transcribed, once and within a spending limit |
| `retimer.realset.systems` | The systems run over a set, and the settings tried for the tools |
| `retimer.realset.run` | Runs systems over the pairs, keeps the outputs, continues a run |
| `retimer.realset.tune` | Chooses the tools' settings on a tenth of the main pairs |
| `retimer.realset.judge` | Judges every output: window check, sound check, shifts |
| `retimer.realset.windows` | The arithmetic of judging, per window of three minutes |
| `retimer.realset.report` | The report of a run |
| `bench/seeds` | Seed material: two open films and a made-up series, each with English and Japanese subtitles |
| `deploy` | The system service, the keys tool, releases and the smoke test for the server |
