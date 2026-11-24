# The HTTP API

The calls, the options, how the service is run and what keeps it closed. The [README](../README.md) has the quick start.

A job takes from under a second to a minute, so the API is job-based: submit,
ask how it is doing, fetch what it produced.

Run through a live service, the 194 seed cases come out exactly as they do
when the agent is called directly, and every delivered file is the file its
plan describes. That run is in
[bench/baselines/2026-11-16-api.md](../bench/baselines/2026-11-16-api.md).

| Call | Purpose |
| --- | --- |
| `POST /v1/jobs` | Submit target, reference and options. Answers with the job. |
| `GET /v1/jobs/{id}` | Status, the current stage, and the outcome with a summary once finished. |
| `GET /v1/jobs/{id}/result` | The retimed subtitle. |
| `GET /v1/jobs/{id}/matched_reference` | The reference subtitle, reduced to the lines the target has a counterpart for. Only for a job that asked for it. |
| `GET /v1/jobs/{id}/plan` | The operation plan as JSON. |
| `GET /v1/jobs/{id}/report` | Diagnosis, decisions, scores and warnings. |
| `GET /v1/jobs` | The jobs of the key, newest first. |
| `DELETE /v1/jobs/{id}` | Delete a job and its files before they expire. |
| `POST /v1/ocr` | Submit an image subtitle to be read into text. Answers with the job. |
| `GET /v1/features` | What this server can do beyond the first release, and its limits. |
| `GET /healthz` | Whether the service is up. The only call that needs no key. |

```bash
ffmpeg -i episode.mkv -vn -ac 1 -c:a libopus -b:a 24k -application voip episode.ogg

curl https://retimer.example/v1/jobs \
  -H "Authorization: Bearer $RETIMER_API_KEY" \
  -F target=@episode.ja.srt \
  -F reference_audio=@episode.ogg \
  -F 'options={"remove_hearing_impaired": true}'
```

A job is a multipart form with these parts:

| Part | Content |
| --- | --- |
| `target` | The subtitle to synchronize, SRT or ASS. Required. |
| `reference_audio` | Audio of the video, without the picture. Mono Opus at 24 kbps is enough and keeps an episode near 4 MB. |
| `reference_subtitle` | A subtitle that is in sync, in any language. SRT or ASS. The part may be sent up to six times, once for every text track of the video file. See [Several reference subtitles](how-it-works.md#several-reference-subtitles). |
| `options` | A JSON object, see below. |

One reference is required, and audio and subtitles may be sent together.
Audio is taken in Ogg, Matroska or WebM, MP3, MP4 or M4A, FLAC, WAV and AAC.

| Option | Default | Meaning |
| --- | --- | --- |
| `remove_hearing_impaired` | `false` | Drop sound descriptions and speaker labels from the output: `[door slams]`, `（笑い）`, `〔エラー音〕`, a name with its reading such as `（六導(りくどう)玲霞(れいか)）`, and in Japanese captions a name after the bracket of a thought or a voice (`《ハルリ：`). |
| `remove_karaoke` | `false` | Drop karaoke and song lines from the output. |
| `remove_signs_and_credits` | `false` | Drop what is shown on screen and not spoken: signs, and the credits and advertisements of whoever made or passed on the file. |
| `strip_formatting` | `false` | Remove styling tags, keep the text. |
| `remove_symbols` | `false` | Remove music notes and similar symbols, among them the arrows and device icons of Japanese broadcast captions (`➡`, `📱`, `☎`). In a Japanese subtitle also the brackets that captions put around a thought, a narration or a voice off screen (`《》`, `≪≫`, `＜＞`, `〈〉`, `｟｠`, `⸨⸩`, doubled parentheses); their words stay, and so do quotation marks such as `「」` and `『』`. |
| `drop_unmatched` | `true` | Trim leading or trailing lines when the reference confirms the content is absent. |
| `matched_reference` | `false` | Also hand back the reference subtitle, reduced to the dialogue lines the delivered target has a counterpart for. See [The matched reference](how-it-works.md#the-matched-reference). Needs a `reference_subtitle`. |
| `audio_language`, `reference_language` | detected | Hints such as `"ja"`. |
| `reference_tracks` | none | What the video file says about each `reference_subtitle`, in the order they were sent: a list of `{"language": "eng", "title": "Signs & Songs", "default": false, "forced": true}`, every field optional. With it there has to be one entry for every track. The labels are hints that decide between tracks which fit equally well, and they come back in the summary. |
| `output_format` | as the target | `"srt"` or `"ass"`. The matched reference keeps the format of the reference subtitle it was made from unless one is named here. |
| `agent` | the server's | `"haiku"`, `"sonnet"` or `"opus"`. A job cannot be run without a model. |

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
    "reference": null,
    "target_lines": 76,
    "delivered_lines": 76,
    "trimmed_lines": 0,
    "cut_lines": 0,
    "matched_reference_lines": null,
    "matched_reference_pairs": null,
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
`links` only names what exists. A job that asked for the matched reference
has it under `matched_reference`, with the number of its lines and of its
pairs in the summary. The pairs themselves are in the report.
The content options apply to it as they do to the target. `reference` is
`null` for a job without a reference subtitle, and otherwise says which track
the job was synced against and what was found out about each, as shown under
[Several reference subtitles](how-it-works.md#several-reference-subtitles).

| Outcome | Meaning | Delivered |
| --- | --- | --- |
| `synced` | The plan was applied and verified. | File, plan, report |
| `already_in_sync` | The timing was fine. Only the content options were applied. | File, plan, report |
| `partial` | Some blocks could not be placed with confidence. The summary names them. | File, plan, report |
| `rejected_no_match` | Reference and target are different content. | Plan and report, no file |
| `transcribed` | An image subtitle was read into text. | File and report |
| `failed` | A technical error. `error` holds a code and a sentence. The code `model_unavailable` means the model could not be asked: send the job again. | Nothing |

A refusal is JSON as well, `{"error": {"code": "…", "message": "…"}}`, with
the usual status: 401 without a valid key, 409 for a result that is not there
yet, 413 for an upload over the limit, 422 for a file or an option that cannot
be used, such as `matched_reference` with audio alone, and 429 with
`Retry-After` when a limit is reached. A file that is no
subtitle, or audio that cannot be read, is refused when it is submitted and
not when the job runs. With several reference subtitles, one that cannot be
read is set aside and the job goes on with the others. More than six are
refused (`too_many_reference_subtitles`), and so is a list of
`reference_tracks` that is not as long as the tracks sent
(`reference_tracks_mismatch`).

A client that also talks to servers of an earlier release asks
`GET /v1/features` first. It needs the key like every other call, and a
server that does not have the route answers 404:

```json
{
  "features": ["matched_reference", "ocr", "reference_tracks", "remove_signs_and_credits"],
  "limits": {"reference_subtitles": 6, "request_bytes": 99614720, "subtitle_bytes": 67108864,
             "subtitle_dialogue_lines": 4000, "audio_bytes": 94371840, "audio_minutes": 240,
             "image_subtitle_bytes": 94371840}
}
```

## Image subtitles

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

## Running the service

```bash
retimer api-key --label jellyfin    # a key for the client, and its entry for the server
export RETIMER_API_KEYS="jellyfin:9f2c…"

retimer serve --dev                 # on this machine: plain HTTP, and /docs with the schema
retimer serve                       # on 127.0.0.1:8000, behind a proxy that does HTTPS
retimer serve --host 0.0.0.0 --allow-http   # on a home network, plain HTTP
```

Three ways to run it:

- **Behind a proxy that does HTTPS** (Caddy, nginx, a Cloudflare Tunnel). The
  default. The service refuses a request that did not arrive over HTTPS, and
  believes only the proxies in `RETIMER_TRUSTED_PROXIES` about that.
- **On a home network**, next to Jellyfin: `--allow-http` or
  `RETIMER_ALLOW_HTTP=1`. Plain HTTP from any address. Keys, limits and the
  budget stay as they are and `/docs` stays closed, but the key and the
  uploads cross the network unencrypted. The Docker setup in the
  [README](../README.md#quick-start-with-docker) runs this way.
- **For development**, `--dev`: plain HTTP and `/docs`, on this machine only.

[deploy/README.md](../deploy/README.md) runs it as a system service on a Linux
server behind a proxy, with the keys typed on the server, releases that are
tested before they go live, and a smoke test against the public address.

The server needs `ffmpeg` and `OPENROUTER_API_KEY`. That one key pays for the
transcripts, the embeddings and the agent's model, which is called through
OpenRouter and served by Anthropic. With `RETIMER_AGENT_PROVIDER=anthropic`
the model is called at Anthropic itself and needs `ANTHROPIC_RETIMER_API_KEY`.
Without the key for its model the service does not start.
One process serves requests and runs the jobs, one at a time. Jobs are rows in
a SQLite file and their files a directory each under `RETIMER_DATA_DIR`. A job
that was running when the server stopped is started over once at the next
start. `retimer serve --no-worker` with a separate `retimer worker` splits the
two.

## What keeps the service closed

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
- **HTTPS only, unless you say otherwise.** The server listens on the machine
  itself and expects a proxy in front that terminates TLS. A request that did
  not arrive over HTTPS is refused, and only the proxy is believed about that
  (`RETIMER_TRUSTED_PROXIES`, default `127.0.0.1`). A server that is exposed
  directly by mistake therefore answers nothing. `RETIMER_ALLOW_HTTP=1` turns
  this off for a home network, and the log says so at every start.
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
| `RETIMER_ALLOW_HTTP` | `0` | `1` takes plain HTTP from any address, for a network you trust. Keys are still required. |
| `RETIMER_MAX_REQUEST_MB` | 95 | Largest request as a whole: the files and the form around them. Cloudflare turns a request over 100 MB away with an answer of its own. |
| `RETIMER_MAX_AUDIO_MB` | 90 | Largest audio upload. Not larger than a request. |
| `RETIMER_MAX_AUDIO_MINUTES` | 240 | Longest audio. |
| `RETIMER_MAX_SUBTITLE_MB` | 64 | Largest subtitle upload. A fansub script with typesetting reaches 50 MB. Not larger than a request. |
| `RETIMER_MAX_IMAGE_SUBTITLE_MB` | 90 | Largest image subtitle upload, bare or packed. |
| `RETIMER_MAX_IMAGE_STREAM_MB` | 1024 | Largest image subtitle once it is unpacked. |
| `RETIMER_MAX_SUBTITLE_LINES` | 400000 | Most lines in a subtitle, signs and effects included. A script with typesetting has up to 140,000. |
| `RETIMER_MAX_REFERENCE_SUBTITLES` | 6 | Most reference subtitles in one job. Each is matched against the target, one after the other. |
| `RETIMER_MAX_DIALOGUE_LINES` | 4000 | Most lines of dialogue in a subtitle. Matching needs about 320 MB of memory at 3000 lines and 1 GB at 6000. |
| `RETIMER_REQUESTS_PER_MINUTE` | 120 | Per key. |
| `RETIMER_JOBS_PER_HOUR` | 60 | Per key. |
| `RETIMER_MAX_ACTIVE_JOBS` | 10 | Queued or running, per key. |
| `RETIMER_DAILY_BUDGET_USD` | 5 | Provider cost of all jobs of one day (UTC). |
| `RETIMER_RETENTION_HOURS` | 24 | How long a finished job is kept. |
| `RETIMER_CACHE_DAYS` | 30 | How long transcripts and embeddings are kept. |
| `RETIMER_AGENT` | `haiku` | The model when a job names none: `haiku`, `sonnet` or `opus`. |
| `RETIMER_AGENT_MAX_COST_USD` | 0.25 | What the agent may spend on one job. |
| `RETIMER_AGENT_PROVIDER` | `openrouter` | Where the agent calls its model: `openrouter`, with the key the service has anyway, or `anthropic`, with `ANTHROPIC_RETIMER_API_KEY`. |
| `RETIMER_OCR_MODEL` | `haiku` | The model that reads image subtitles when a request names none. |
| `RETIMER_OCR_MAX_COST_USD` | 0.5 | What reading one image subtitle may cost. |
| `RETIMER_WORKERS` | 1 | Jobs that run side by side. |

Jobs that are queued or running when the budget runs out still finish, so a
day can end a little above it. A spending limit on the provider accounts
themselves is the last line.
