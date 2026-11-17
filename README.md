# retimer-api

An API that synchronizes a subtitle file to a reference (audio, an in-sync
subtitle, or both). An AI agent diagnoses the file and chooses the operations;
deterministic tools do all the timing math.

The project is built in milestones. This repository holds the subtitle core,
the benchmark harness, the fixed pipeline that syncs a file without an agent,
the agent that settles what the pipeline leaves open, the HTTP API with its
deployment, and the reading of image subtitles.

## What the subtitle core does

- Reads and writes SRT and ASS, including legacy encodings such as Shift-JIS.
  Times are read as players read them, also the forms that tools which cut a
  film's script into parts write, such as `0:-39:-28.91`. A time before zero
  is shown from zero on.
- Labels every line: dialogue, sign, song, sound description, credit, comment.
  The labels hold on fansub scripts, where karaoke effects and typesetting
  that is redrawn for every frame can be twenty times the dialogue: the styles
  of a song are known by name, by family and by their timing tags, and a
  positioned line that is gone within a quarter second is a sign.
- Produces an inspection report that summarizes a file without quoting all of it.
- Turns request options (remove hearing-impaired text, karaoke, symbols,
  formatting) into content operations.
- Builds the match view: dialogue only, reduced to the spoken words. The
  layers of one line, and a line an effect draws frame by frame, are one line.
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
The model is called through OpenRouter, pinned to Anthropic's servers, and
[comes out the same that way](bench/baselines/2026-11-17-agent-openrouter.md).

## The HTTP API

A job takes from under a second to a minute, so the API is job-based: submit,
ask how it is doing, fetch what it produced.

Run through a live service, the 194 seed cases come out exactly as they do
when the agent is called directly, and every delivered file is the file its
plan describes. That run is in
[bench/baselines/2026-11-16-api.md](bench/baselines/2026-11-16-api.md).

| Call | Purpose |
| --- | --- |
| `POST /v1/jobs` | Submit target, reference and options. Answers with the job. |
| `GET /v1/jobs/{id}` | Status, the current stage, and the outcome with a summary once finished. |
| `GET /v1/jobs/{id}/result` | The retimed subtitle. |
| `GET /v1/jobs/{id}/plan` | The operation plan as JSON. |
| `GET /v1/jobs/{id}/report` | Diagnosis, decisions, scores and warnings. |
| `GET /v1/jobs` | The jobs of the key, newest first. |
| `DELETE /v1/jobs/{id}` | Delete a job and its files before they expire. |
| `POST /v1/ocr` | Submit an image subtitle to be read into text. Answers with the job. |
| `GET /healthz` | Whether the service is up. The only call that needs no key. |

```bash
ffmpeg -i episode.mkv -vn -ac 1 -c:a libopus -b:a 24k -application voip episode.ogg

curl https://retimer.example/v1/jobs \
  -H "Authorization: Bearer $RETIMER_API_KEY" \
  -F target=@episode.ja.srt \
  -F reference_audio=@episode.ogg \
  -F 'options={"remove_hearing_impaired": true}'
```

A job is a multipart form with up to four parts:

| Part | Content |
| --- | --- |
| `target` | The subtitle to synchronize, SRT or ASS. Required. |
| `reference_audio` | Audio of the video, without the picture. Mono Opus at 24 kbps is enough and keeps an episode near 4 MB. |
| `reference_subtitle` | A subtitle that is in sync, in any language. SRT or ASS. |
| `options` | A JSON object, see below. |

One reference is required, and both may be sent. Audio is taken in Ogg,
Matroska or WebM, MP3, MP4 or M4A, FLAC, WAV and AAC.

| Option | Default | Meaning |
| --- | --- | --- |
| `remove_hearing_impaired` | `false` | Drop sound descriptions and speaker labels from the output. |
| `remove_karaoke` | `false` | Drop karaoke and song lines from the output. |
| `strip_formatting` | `false` | Remove styling tags, keep the text. |
| `remove_symbols` | `false` | Remove music notes and similar symbols, among them the arrows and device icons of Japanese broadcast captions (`➡`, `📱`, `☎`). |
| `drop_unmatched` | `true` | Trim leading or trailing lines when the reference confirms the content is absent. |
| `audio_language`, `reference_language` | detected | Hints such as `"ja"`. |
| `output_format` | as the target | `"srt"` or `"ass"`. |
| `agent` | the server's | `"haiku"`, `"sonnet"`, `"opus"`, or `"off"` for the fixed pipeline. |

An option the service does not know is refused, not ignored.

The answer to every job call is the job:

```json
{
  "id": "b0dd7dc2896b4956aeab67099d6aeed2",
  "status": "finished",
  "stage": null,
  "outcome": "synced",
  "expires_at": "2026-11-17T08:48:06Z",
  "summary": {
    "confidence": 0.93,
    "fit": "blocks",
    "blocks": [
      {"from_ms": null, "to_ms": 239724, "shift_ms": 0, "lines": 17, "confidence": 0.79},
      {"from_ms": 239724, "to_ms": null, "shift_ms": -40901, "lines": 59, "confidence": 0.95}
    ],
    "target_lines": 76,
    "delivered_lines": 76,
    "trimmed_lines": 0,
    "warnings": [],
    "agent": {"model": "claude-haiku-5-5", "consulted": true, "turns": 2, "cost_usd": 0.0008},
    "cost_usd": 0.0116,
    "seconds": 21.4
  },
  "error": null,
  "links": {"self": "/v1/jobs/b0dd…", "result": "…/result", "plan": "…/plan", "report": "…/report"}
}
```

`status` goes from `queued` to `running` to `finished`. While a job runs,
`stage` says where it is: `transcribing`, `matching`, `deciding`, `writing`.
`links` only names what exists.

| Outcome | Meaning | Delivered |
| --- | --- | --- |
| `synced` | The plan was applied and verified. | File, plan, report |
| `already_in_sync` | The timing was fine. Only the content options were applied. | File, plan, report |
| `partial` | Some blocks could not be placed with confidence. The summary names them. | File, plan, report |
| `rejected_no_match` | Reference and target are different content. | Plan and report, no file |
| `transcribed` | An image subtitle was read into text. | File and report |
| `failed` | A technical error. `error` holds a code and a sentence. | Nothing |

A refusal is JSON as well, `{"error": {"code": "…", "message": "…"}}`, with
the usual status: 401 without a valid key, 409 for a result that is not there
yet, 413 for an upload over the limit, 422 for a file or an option that cannot
be used, and 429 with `Retry-After` when a limit is reached. A file that is no
subtitle, or audio that cannot be read, is refused when it is submitted and
not when the job runs.

### Image subtitles

Blu-ray subtitles are pictures (PGS, a `.sup` file), and the Japanese
subtitles of a disc are often shared in that form. `POST /v1/ocr` reads one
into a subtitle with text. The times are taken from the stream and are exact.
The text is read by a model that can see, through the same OpenRouter key as
everything else.

```bash
curl https://retimer.example/v1/ocr \
  -H "Authorization: Bearer $RETIMER_API_KEY" \
  -F subtitle=@"Show 01 BDSUP.7z" \
  -F 'options={"language": "ja"}'
```

| Part | Content |
| --- | --- |
| `subtitle` | The image subtitle: a `.sup` file, or a gzip, zip or 7z archive that holds one. Required. |
| `options` | A JSON object, see below. |

A `.sup` file is large, around 100 MB for an episode, and packs to a few
megabytes. Sites serve them packed, and the service takes them as they come.
A request stays under the limit of every request, so a bare stream of a whole
episode has to be packed first.

| Option | Default | Meaning |
| --- | --- | --- |
| `language` | seen by the model | The language of the subtitles, such as `"ja"`. |
| `model` | the server's | `"haiku"`, `"sonnet"`, `"gemini-flash"`, `"gemini-flash-lite"` or `"qwen-flash"`. |
| `output_format` | `"srt"` | `"srt"` or `"ass"`. |

The answer is a job like any other, asked about under `/v1/jobs/{id}` and
fetched from `/v1/jobs/{id}/result`. Its stages are `decoding`, `reading` and
`writing`, and its outcome is `transcribed`. The summary says how many lines
were read, from how many pictures, by which model and at what cost, and the
report holds the same in full. A picture that was read once is remembered by
its content for as long as transcripts are, so the same file costs nothing a
second time.

Pictures are shown to the model sixteen at a time, each after its number, and
it answers with the text of each. Furigana are left out. A subtitle that
fades in is read from the frame in which it is most visible. What reading
one file may cost is limited (`RETIMER_OCR_MAX_COST_USD`), and it counts
against the daily budget. The cost is what OpenRouter reports, including what
an upstream charged on the account's own key for it. Where it reports nothing,
the model's list price is used. DVD subtitles (VobSub) are not read.

The result is an ordinary subtitle, and can be the target or the reference of
a job.

### Running the service

```bash
retimer api-key --label al     # a key for the client, and its entry for the server
export RETIMER_API_KEYS="al:9f2c…"

retimer serve --dev            # on this machine: plain HTTP, and /docs with the schema
retimer serve                  # production: on 127.0.0.1:8000, behind a proxy that does HTTPS
```

[deploy/README.md](deploy/README.md) is how the service runs on its server:
as a system service behind a Cloudflare Tunnel, with the keys typed on the
server, releases that are tested before they go live, and a smoke test
against the public address.

The server needs `ffmpeg` and `OPENROUTER_API_KEY`. That one key pays for the
transcripts, the embeddings and the agent's model, which is called through
OpenRouter and served by Anthropic. With `RETIMER_AGENT_PROVIDER=anthropic`
the model is called at Anthropic itself and needs `ANTHROPIC_RETIMER_API_KEY`;
without that key jobs then run the fixed pipeline.
One process serves requests and runs the jobs, one at a time. Jobs are rows in
a SQLite file and their files a directory each under `RETIMER_DATA_DIR`. A job
that was running when the server stopped is started over once at the next
start. `retimer serve --no-worker` with a separate `retimer worker` splits the
two.

### What keeps the service closed

The provider keys behind the service cost money, so nothing is served to a
stranger.

- **A key on every call.** Every request passes one gate before anything else
  looks at it, so a route cannot be left open by forgetting to protect it.
  Without a valid key the answer is 401, for a path that exists and for one
  that does not. The health check is the exception and says only that the
  service is up. The service does not start without a key.
- **Keys are not stored.** A key is 256 random bits. The server holds its
  SHA-256 hash and a label for the logs, compares in constant time, and takes
  the key from a header only, never from the address of a request.
- **HTTPS only.** The server listens on the machine itself and expects a proxy
  in front that terminates TLS. A request that did not arrive over HTTPS is
  refused, and only the proxy is believed about that
  (`RETIMER_TRUSTED_PROXIES`, default `127.0.0.1`). A server that is exposed
  directly by mistake therefore answers nothing.
- **No documentation in production.** `/docs` and the schema exist on a
  development server only, and a development server only listens on its own
  machine.
- **Limits before the body is read.** A request without a key is refused
  before its upload is taken. The size of an upload is checked from its header
  and counted while it arrives. A request as a whole has a limit, and it is
  below the one of the proxy in front, so that a client is told by the service
  and in JSON. Each file has a size limit of its own, audio a length limit and
  subtitles a line limit. An upload that has not arrived after 15 minutes is
  dropped. Connections that send no request at all are for the proxy to
  close.
- **Rate limits.** Requests per key and minute, jobs per key and hour, jobs
  under way per key, and wrong keys per address. A limit answers 429 and says
  how long to wait.
- **A daily budget.** What the jobs of a day cost at the providers is added
  up. When the budget is spent, new jobs are refused until the next day. The
  agent has a cost limit per job on top.
- **Uploads are data.** Nothing a client sends names a file on the server.
  Audio is only opened in a fixed list of containers and only from the local
  disk, so a playlist or script that points at other files or addresses is
  refused. Text is cleaned by patterns that read a line once, however it is
  built.
- **Nothing is kept.** The audio of a job is deleted when the job is done, the
  job with its files 24 hours later, transcripts and embeddings after 30 days.
  `DELETE` removes a job at once.
- **Errors say little.** A failure on the server's side reaches the client as
  a code and a sentence. The cause goes to the log, and the log blanks the
  provider keys and anything shaped like an API key.

| Setting | Default | Meaning |
| --- | --- | --- |
| `RETIMER_API_KEYS` | none, required | Entries `label:sha256`, separated by commas. |
| `RETIMER_DATA_DIR` | `data` | Jobs, their files, the cache. |
| `RETIMER_TRUSTED_PROXIES` | `127.0.0.1` | Whose word on HTTPS and the client's address is taken. |
| `RETIMER_MAX_REQUEST_MB` | 95 | Largest request as a whole: the files and the form around them. Cloudflare turns a request over 100 MB away with an answer of its own. |
| `RETIMER_MAX_AUDIO_MB` | 90 | Largest audio upload. Not larger than a request. |
| `RETIMER_MAX_AUDIO_MINUTES` | 240 | Longest audio. |
| `RETIMER_MAX_SUBTITLE_MB` | 64 | Largest subtitle upload. A fansub script with typesetting reaches 50 MB. Not larger than a request. |
| `RETIMER_MAX_IMAGE_SUBTITLE_MB` | 90 | Largest image subtitle upload, bare or packed. |
| `RETIMER_MAX_IMAGE_STREAM_MB` | 1024 | Largest image subtitle once it is unpacked. |
| `RETIMER_MAX_SUBTITLE_LINES` | 400000 | Most lines in a subtitle, signs and effects included. A script with typesetting has up to 140,000. |
| `RETIMER_MAX_DIALOGUE_LINES` | 4000 | Most lines of dialogue in a subtitle. Matching needs about 320 MB of memory at 3000 lines and 1 GB at 6000. |
| `RETIMER_REQUESTS_PER_MINUTE` | 120 | Per key. |
| `RETIMER_JOBS_PER_HOUR` | 60 | Per key. |
| `RETIMER_MAX_ACTIVE_JOBS` | 10 | Queued or running, per key. |
| `RETIMER_DAILY_BUDGET_USD` | 5 | Provider cost of all jobs of one day (UTC). |
| `RETIMER_RETENTION_HOURS` | 24 | How long a finished job is kept. |
| `RETIMER_CACHE_DAYS` | 30 | How long transcripts and embeddings are kept. |
| `RETIMER_AGENT` | `haiku` | The model when a job names none, or `off`. |
| `RETIMER_AGENT_MAX_COST_USD` | 0.25 | What the agent may spend on one job. |
| `RETIMER_AGENT_PROVIDER` | `openrouter` | Where the agent calls its model: `openrouter`, with the key the service has anyway, or `anthropic`, with `ANTHROPIC_RETIMER_API_KEY`. |
| `RETIMER_OCR_MODEL` | `haiku` | The model that reads image subtitles when a request names none. |
| `RETIMER_OCR_MAX_COST_USD` | 0.5 | What reading one image subtitle may cost. |
| `RETIMER_WORKERS` | 1 | Jobs that run side by side. |

Jobs that are queued or running when the budget runs out still finish, so a
day can end a little above it. A spending limit on the provider accounts
themselves is the last line.

## Setup

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

Python 3.11 or newer.

## Command line

```bash
retimer serve --dev                              # the HTTP API, see above
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
`haiku` (the default), `sonnet`, `opus` or a full model id. The model is
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
`agent-sonnet` and `agent-opus` are the agent on each model. They call the
model the way the service does (`RETIMER_AGENT_PROVIDER`) and run only when
named, because every run costs
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
- Ranges may not overlap, no line may be over before zero, and no line may move in
  front of a line that precedes it. A plan that breaks a rule is refused with a
  list of issues naming the lines involved. A line that would begin before
  zero and still be on screen after it is shown from zero on, and a comment
  event stops at zero.

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
| `retimer.bench.metrics` | Line error, tolerance shares, file success, decisions, stability |
| `retimer.bench.runners` | The systems under test: ffsubsync, alass, the pipeline, the agent, input and oracle |
| `retimer.bench.harness` | Runs systems over cases and stores the results |
| `retimer.bench.report` | Summary tables |
| `bench/seeds` | Seed material: two open films with English and Japanese subtitles |
| `deploy` | The system service, the keys tool, releases and the smoke test for the server |
