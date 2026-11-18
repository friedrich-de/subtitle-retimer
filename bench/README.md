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

## Cases that replicate real files

A run over 1,600 real pairs of subtitles showed where syncing goes wrong or
gets hard. Those files are other people's work and cannot be published. So
every problem found there is rebuilt from the seed material, with the same
shape and with known-correct timing. Nothing in these cases is taken from a
real episode: song words, signs and speaker names are made up.

| Family | What real files did | The case | Expected outcome |
| --- | --- | --- | --- |
| `loose_reference` | No two subtitles of a film share their timing | Offset, rate and breaks as in the plain families, against a reference that was timed anew | `synced`, and one `already_in_sync` |
| `cold_open` | A short scene, then a sponsor card or a longer opening that the other version does not have | Two to six lines, a break of 4 s or 10 s, then the rest. One case is a recording from television with two commercial breaks as well | `synced` |
| `caption_drift` | Broadcast captions sit a fraction of a second early for minutes | One or two stretches of the file are 0.35 s to 0.5 s early | `synced` |
| `pack` | Several episodes in one file | The episode with another one before it, after it, or half of it on either side | `synced`, with the other episode removed |
| `mid_scene` | Two versions differ by a few seconds in the middle of a scene | A cut of 3 s or an insert of 5 s where the lines are dense, not in a pause | `synced` |
| `next_episode` | The next episode was sent by mistake, and episodes of a series share lines | Another episode as the target. It shares its opening lines with the reference, or short phrases all over the file | `rejected_no_match` |
| `signs_reference` | The reference track holds only signs and songs | A reference without one line of dialogue: a typeset script of signs and songs, or a plain file of sign texts | `left_alone` |
| `several_references` | A video holds several subtitle tracks, and what the file says of them is missing or wrong | Two to six tracks to choose from: the full script and its signs and songs alone, the dialogue in two languages, the same track twice, another episode, tracks without any label, a track of signs marked as the default one. See below | `synced`, and one `already_in_sync`. `left_alone` where every track is signs, `rejected_no_match` where one is another episode and none is the dialogue |
| `karaoke` | A typeset script is mostly karaoke and signs | The target, the reference or both carry an opening, an ending and moving signs the way a karaoke template writes them: thousands of lines around the dialogue, up to 14,000 | `synced` |
| `captions` | Captions name the speaker and describe sounds | Speaker names, marks, sound descriptions and music notes in the target, with and without the request to clean them | `synced`, cleaned as the options say |
| `bilingual` | Two languages in one file, or reading aids above the lines | Both languages in every line, both as lines of their own, or small kana above some of the lines | `synced` |
| `odd_times` | A tool cut a script into parts and wrote times only players read | Times such as `0:2:-96.50`, and the lines of the part before it at times before zero, where no player shows them | `synced` |

The target is the last language of a seed and the reference its first, so
Japanese captions are synced to an English subtitle, as in the real files.
Where the problem lies in the target and not in the reference, the case is
built against the audio as well. Where a case needs another episode it takes
the next seed of the same series. The two films stand in for each other.

One thing differs from the plain families throughout. There, the two languages
of a seed are used as they are, and for the films that means timing shared to
the millisecond, which no two subtitles of a real film have. Here every case
gets a reference of its own, timed anew the way a second translator would
have: each line start moved by about 150 ms (500 ms at most), some neighbours
joined into one line, some long lines cut in two, a few short ones left out.
Taken over all its lines the reference stays where it was, so its timing is
still the true one. It is stored in the case directory.

`left_alone` is an outcome of its own. A track of signs says nothing about
where a spoken line belongs. Refusing the job is right, and so is handing the
file back unmoved. Moving the lines is wrong.

### Several reference tracks

A case of `several_references` comes with the tracks in the order a video file
would hold them, each with what the file says of it: language, title, and
whether it is marked default or forced. As in real files those labels are
sometimes right, sometimes missing and sometimes wrong.

| Case | Tracks, in their order | Target |
| --- | --- | --- |
| `full-and-signs` | The full script (default, "Full Subtitles") and its signs and songs alone (forced, "Signs & Songs"). Both are typeset scripts with the same songs and signs | 8 s late |
| `signs-marked-default` | The same two the other way round, without titles, and the track of signs is the one marked default and forced | 1.5 s early |
| `three-without-labels` | A plain file of sign texts, the dialogue of another episode, the dialogue. The file says nothing about any of them | Two breaks |
| `two-languages-fit` | The dialogue in the reference language (default), the dialogue in the language of the target, signs. Either dialogue track is right | 2.7 s early, one break |
| `same-track-twice` | Signs, and one dialogue track that is there twice under two titles | 25 to 23.976 fps |
| `six-tracks` | As many as a job takes: signs (default and forced), the dialogue in the language of the target, another episode, the dialogue, the dialogue again, plain sign texts | 0.9 s late, three breaks |
| `in-sync` | Signs and songs (forced, "Signs & Songs") and the full script (default, "Full Subtitles") | In sync |
| `only-signs` | Two tracks of signs and songs | 8 s late |
| `other-episode-and-signs` | The dialogue of another episode (default, "Dialogue") and signs | 8 s late |

`full-and-signs`, `three-without-labels`, `only-signs` and
`other-episode-and-signs` are also built with the audio beside the tracks, for
the seeds that have audio. Then the last two are expected to be `synced` as
well: no track says where a line belongs, and the audio does.

A system that takes one reference gets the track a client would send that
sends a single one: the track marked default, and the first where none is
marked. So `alass` and `ffsubsync` are given the dialogue in `full-and-signs`,
`two-languages-fit` and `in-sync`, another episode in
`other-episode-and-signs`, and a track of signs in the five other cases. With
the audio beside the tracks they get the audio, as in every case that has
both. `pipeline`, the agent systems
and `api` get every track with its labels and choose. Which track was chosen
is in the record of a run, under `details.report.reference_choice` (for `api`
under `details.job.summary.reference`), next to what was found out about each
track.

## The made-up series

The two films are short and sparse: 76 and 26 lines in 12 and 15 minutes, and
nothing follows them. An episode of a series has some 390 lines in 22
minutes, opens and closes with the same words every week, and is followed by
another one with the same people in it. Real episodes cannot be published, so
the benchmark has a series that was written for it: *The Rooftop Tomato Club*,
three episodes about a school garden club, in Japanese and English.

The lines were written by the model the agent runs on, Claude Haiku 5.5, scene
by scene from an outline it made first. It writes a scene as beats. A beat is
one thing one character says, as the Japanese captions show it and as an
English subtitle shows it, and either side may show it in one line or in two.
One beat in six is cut differently in the two languages, so the files do not
pair line for line. More than a fifth of the beats are reactions of a few
characters.
Every episode opens with the same three lines of narration, and its preview
of the next episode closes with the same line.

The times are made by rule from a seeded generator. How long a line takes to
say follows from its sounds, at about eight a second. Pauses are short within
an exchange and long between scenes. An episode is laid out like a broadcast:
the narration, a scene, 90 seconds for the opening song, two halves with a
card between them, 90 seconds for the ending song, and the preview. Both
languages are timed against the same speech. The captions start with it. The
subtitles start within 80 ms of it and stay up longer.

There is no sound, so the series only gives cases against a reference
subtitle. And it is a model's idea of dialogue with a rule's idea of timing:
it has the density, the line lengths and the repetition of an episode, and
not the habits of a real captioner. For those, real files stay the measure.

```bash
retimer bench write-series                              # a new series from the model
retimer bench time-series rooftop-tomato-club --seed 1  # draw the times of a script again
```

`write-series` needs the key the agent needs, and took six minutes and 10
cents for three episodes. It adds the episodes to `seeds.json` under the name
of their series and stores the script as `series.json` beside the subtitles,
so the times can be drawn again without the model.

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
with no `true_start_ms` has no place in the reference (a trailing ad, say, or
a line of another episode) and is expected to be trimmed. Paths are relative
to the case directory. A case that replicates a real file has its reference
subtitle beside the target, as `reference.srt` or `reference.ass`.

A case with several reference subtitles lists them under `reference` as well,
and has them beside the target as `reference.0.ass`, `reference.1.srt` and so
on:

```json
"reference": {
  "kind": "subtitle",
  "subtitle": "reference.0.ass",
  "subtitle_language": "en",
  "subtitles": [
    {"file": "reference.0.ass", "role": "signs", "language": "en", "title": null, "default": true, "forced": true},
    {"file": "reference.1.ass", "role": "full", "language": "en", "title": null, "default": false, "forced": false}
  ]
}
```

`language`, `title`, `default` and `forced` are what the video file says, and
they are passed on to a system that takes several tracks. `role` is what the
track is, `full`, `signs` or `other_episode`, and no system gets to see it.
`subtitle` names the one track that a system gets which takes a single
reference.

`expected_outcome` is `synced`, `already_in_sync`, `rejected_no_match` or
`left_alone`.

A naturally mistimed file becomes a case the same way: write the directory by
hand, with `truth` taken from a hand-checked copy. Generated cases live under
`data/bench/cases`, which is not in the repository; `retimer bench generate`
rebuilds them and always produces the same set.

## Metrics

| Metric | Definition |
| --- | --- |
| Line error | Distance between the start of a dialogue line after syncing and its true start. Median and 90th percentile per file. |
| Within tolerance | Share of dialogue lines within 100 ms, 250 ms and 500 ms. A dialogue line the system removed counts as outside. |
| File success | At least 95% of a file's dialogue lines within 250 ms. For a case that is to be rejected: the system rejected it. For a case that is to be left alone: it rejected it, or no line moved. |
| At 500 ms | The same with 500 ms. See the note on audio below. |
| Decisions right | Correct outcome on the cases that are in sync, to be rejected or to be left alone, and lines without a place in the reference trimmed without losing dialogue. |
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
| `agent-haiku`, `agent-sonnet`, `agent-opus` | The pipeline with a model settling what its draft leaves open, on Claude Haiku 5.5, Sonnet 5.5 or Opus 5.5. The model is called through OpenRouter with the same key, or at Anthropic with `RETIMER_AGENT_PROVIDER=anthropic` and `ANTHROPIC_RETIMER_API_KEY`. |
| `api` | A running service, reached over HTTP as a client reaches it. Needs `RETIMER_API_URL` and `RETIMER_API_KEY`. |

The external tools take one reference. When a case has both audio and a
reference subtitle they get the audio. When it has several reference
subtitles they get the one a client would send that sends a single track
([above](#several-reference-tracks)). A tool is found on the `PATH`, or
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

`api` submits each case as a job, waits for it, fetches the result and the
plan, and deletes the job again. It measures what a client gets, and it checks
one promise on the way: the plan of a job, run on the target here, has to give
exactly the file the service delivered. The service decides which model its
agent runs on. Its limits apply to the benchmark as to any client: a run of all
cases needs `RETIMER_JOBS_PER_HOUR` raised on the server, or it waits.

A new system is a class with `name`, `unavailable()`, `supports(case)` and
`run(case, work_dir)` that returns a `RunOutput`, added in
`retimer.bench.cli.all_runners`.

## The sound check

The cases above know the true start of every line. A real file that nobody
timed by hand does not, and what this project reports about its own result is
no independent witness. `retimer bench soundcheck` is one: it asks the sound
itself whether the lines start where somebody starts to speak.

```
retimer bench soundcheck episode.ogg synced.srt
whole file      76 lines   offset    +60 ms   strength  3.41   clear
0:00-3:00       14 lines   offset    +50 ms   strength  1.66   clear
3:00-6:00       32 lines   offset    -10 ms   strength  2.36   clear
6:00-9:00       27 lines   offset   +300 ms   strength  2.12   clear
9:00-12:00       3 lines   too few to tell
```

It takes an audio or video file and a subtitle, in any state: the output of
this retimer, of another tool, or the file as it was. `--json` prints the same
as data. No speech model, no transcript and no reference subtitle are used,
only `ffmpeg` and arithmetic, so the check costs nothing and takes a few
seconds per episode.

- **How.** For every hundredth of a second it works out how much a voice
  stands out of the sound around it: loud in the band a telephone carries,
  louder than the seconds before and after, and with a pitch. Then it moves
  the dialogue lines back and forth and looks for the offset at which the most
  lines begin just where a voice rises.
- **Offset.** How far the lines would have to move to sit on the sound,
  positive when they come too early. It is searched within 5 s either way
  (`--reach`). Lines further off than that come out as not clear.
- **Strength.** The best offset near zero, measured against the best one that
  turns up a minute and more away, where the lines lie on other people's
  sentences and only chance can fit them. Above 1 the lines beat every chance
  offset. From 1.4 on the check calls the offset `clear`.
- **Stretches.** The file is read as a whole and in stretches of three minutes
  (`--window`), because a file can be right before a break and wrong after it.
  A stretch with fewer than 8 dialogue lines says nothing.

What it can and cannot tell, measured on the two seed films:

- A known shift is found again exactly, on the whole file and on every
  stretch. With the lines after minute three moved by 2 s, the stretches read
  +50, -2020 and -1700 ms where they read +50, -10 and +300 ms before.
- The lines of the other film come out at a strength of 0.8, and of 48
  stretches whose lines were moved far away the highest is 1.18. Every stretch
  that is in sync has 1.45 or more. On generated sound, about one stretch of
  unrelated lines in 70 gets over 1.4: one clear stretch alone is a strong
  hint and no proof.
- The offset of a file that is in sync is not zero. A person who times
  subtitles lets a line appear a little before or after the voice, and that
  habit differs by file: +60 ms on Tears of Steel, -200 ms on Sintel. Between
  the stretches of one file the reading wanders by up to a quarter of a second.
  So the check tells whether lines are within about half a second of the
  sound. It does not rank two results that are both that close.
- Both films are in English and have few lines. Japanese speech and the
  denser dialogue of a series episode are not measured here.

## Real files

The cases above are made, also those that replicate what real files do. The
files themselves are measured on a kept set of real videos' subtitle tracks,
audio and shared subtitle files. It has its own commands, `retimer realset`,
and its own page: [realset/README.md](realset/README.md). It runs the same
runners as this benchmark and uses the sound check as one of its three
witnesses.

## Results

Each run writes a directory under `data/bench/results`:

- `results.jsonl`: one record per case, system and repeat, with all metrics.
- `summary.md` and `summary.json`: the tables.
- `run.json`: when it ran and the tool versions.
- `outputs/<system>/<case>/`: the file each system produced.

Runs worth keeping are written up in [baselines/](baselines/). The one over
535 cases, with alass and ffsubsync next to the pipeline and the agent, is
[baselines/2026-11-18-real-file-problems.md](baselines/2026-11-18-real-file-problems.md).
It was made before the family `several_references` was added. The run over
that family's 53 cases is
[baselines/2026-11-18-several-references.md](baselines/2026-11-18-several-references.md).
With the audio of the two films fetched, the set now has 588 cases.
