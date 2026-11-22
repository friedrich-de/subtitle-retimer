# How the retimer works

What each part of the retimer does, from reading a file to the plan that changes it. The [README](../README.md) says how to run it.

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
- Turns request options (remove hearing-impaired text, karaoke, signs and
  credits, symbols, formatting) into content operations.
- Builds the match view: dialogue only, reduced to the spoken words. The
  layers of one line, and a line an effect draws frame by frame, are one line.
- Validates and applies an operation plan. The plan is the only thing that
  changes a file.

## What the fixed pipeline does

It runs the routine steps in one set order and always takes the best-scoring
answer. Its plan is the draft of every job. On its own it is delivered to
nobody: the benchmark runs it as the system `pipeline`, to show what the model
adds.

1. **Reference.** Audio is transcribed with word timestamps (Whisper large v3
   turbo, through OpenRouter, pinned to one upstream) and grouped into
   utterances. A reference subtitle is reduced to its dialogue. Of
   [several reference subtitles](#several-reference-subtitles) the one that
   fits the target is taken. With both, the subtitle is first checked against
   the audio, and the audio wins.
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
[bench/baselines/2026-11-16-fixed-pipeline.md](../bench/baselines/2026-11-16-fixed-pipeline.md).

On all 588 cases, with a made-up series and the problems of real files among
them, the agent solves 94%, the pipeline alone 87%, alass 62% and ffsubsync 52%
at best. With a reference subtitle and nothing to refuse the agent solves 373
of 380 and alass 323. The lead there comes from several reference tracks,
episode packs, karaoke scripts, edits in the middle of a scene and cold opens,
and alass solves 2 cases of drifting captions that the retimer does not. The
rest of the lead comes from audio and from knowing when not to sync. The
tables, and where the retimer loses, are in
[bench/baselines/2026-11-22-head-to-head.md](../bench/baselines/2026-11-22-head-to-head.md),
and the same systems on real files in
[bench/baselines/2026-11-18-real-files.md](../bench/baselines/2026-11-18-real-files.md).

## What the agent does

The agent starts where the fixed pipeline stops. The routine steps run first,
and their plan is the draft. A workbench then looks for what the draft rests
on too little:

- lines between two blocks, or at the start or the end of the file, that found
  no counterpart;
- a block with hardly anything certain behind it;
- a verdict on whether both sides are the same content that was reached by a
  small margin;
- no timing at all, because the parts that were placed cannot follow each
  other;
- a target that holds clearly more than the reference covers, such as several
  episodes in one file of which the reference is one;
- a stretch of a block whose placed lines land beside their places, early or
  late by a few tenths of a second, while the rest of the block lands on them.

When nothing is open, no model is called. On the benchmark that is two files in
three. Otherwise the model gets a dossier: the job, what the routine steps
found, and every open question with the lines concerned next to the stretch of
the reference they can belong to. It has ten tools and never writes a time.
It says which line begins at which place in the reference, what happens to a
line at the edge of the file, which part of the file the reference covers,
which stretch has a timing of its own, which fit to go with, and whether the
two sides are the same content. Each answer is an adjustment to the pipeline,
which then builds and validates the plan as it always does.

A file of several episodes is settled in one step. The routine steps find the
episode of the reference and where its lines would land. The dossier shows the
lines around both ends of that part, and the model names its first and its
last line. The lines outside are removed, and the part is matched and timed
anew, on its own. That matters because episodes of a series share lines, a
narration or a recap, and one chain of pairs through the whole file gives some
of the reference's places to the wrong episode. The report lists the removed
lines as `cut`, and the summary of a job counts them as `cut_lines`.

A stretch that is timed a little apart is settled in one step too. All lines of
a block move by one amount, and a fit tells two timings apart only when they
differ by more than its pairs scatter. Captions that run 0.4 s early for a
third of an episode are below that: the block gets the middle of all its pairs,
which suits neither part. The workbench measures it. It takes the lines that
found their counterpart, looks how far each lands beside it under the draft,
and cuts that row where its level changes by 0.15 s or more and clearly more
than the pairs scatter. The dossier lists the stretches with how far they land
early or late and shows pairs from those that lean. The model reads them and
names the stretches with `retime_stretches`. The block is cut there and every
piece is timed by its own pairs (`Adjustments.stretches`,
`retimer.fit.with_stretches`). Where a stretch begins is read off the lines
between its first pair and the last pair before it: each starts where a line
of the reference starts under one of the two timings. Where they do not tell,
it begins after the longest pause, because a timing changes between scenes.
Only a reference subtitle is timed exactly enough for this. The times of a
transcript wander by themselves, so against audio alone the question is not
asked. On the benchmark this solves 12 of the 15 cases of caption drift
against a reference subtitle, which nothing solved before:
[bench/baselines/2026-11-18-stretches.md](../bench/baselines/2026-11-18-stretches.md).

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
- A refusal is overturned only by a timing that holds: its confidence reaches
  the bar of a block, and the lines it places reach over at least a third of
  the file. The next episode of a series reads like the same one for as long as
  its recap lasts, so the question of same content shows lines from the rest of
  the file with the place that comes closest to each. An answer of "same
  content" that rests on the opening alone leaves the refusal standing.
- A file is cut down to a part only where the routine steps found the reference
  in the target, where the part holds the lines they placed, and where the
  lines placed in it reach over at least half of the reference. The next
  episode shares a recap with the reference, which is a piece of it, so it
  cannot be cut down to the lines the two have in common.
- A line named to the edge tool that is no edge line is left as it is and
  reported. The other lines named with it are still decided.
- A stretch gets a timing of its own only where it holds at least four lines
  that found their counterpart, and where more of those lines then land within
  0.15 s of their places than before. A stretch that was tried and gained
  nothing is not asked about again.
- A job has a budget of eight model calls and one dollar. When it runs out, or
  when the model stops without settling the job, the plan so far is returned
  as `partial`.
- When the model cannot be reached, the job fails with the code
  `model_unavailable` and can be sent again. The draft of the routine steps is
  never handed out in the place of a result the model was to settle.
- The text of subtitles and transcripts is data. The model is told not to
  follow instructions in it, and none of its tools could do harm if it did.

On the 194 seed cases:

| System | Files synced | At 500 ms | Decisions right | Cost per file |
| --- | --- | --- | --- | --- |
| Best existing tool | 43% | 45% | 16% | none |
| Fixed pipeline alone (a diagnostic, not delivered) | 87% | 89% | 66% | none |
| Agent on Claude Haiku 5.5 | 94% | 98% | 100% | 0.03 cents |
| Agent on Claude Opus 5.5 | 95% | 98% | 100% | 0.6 cents |

The model is asked about 31% of the files and takes two turns and six seconds
on those. Haiku 5.5 is the default. What is left are block cases against audio
alone, where the limit is the transcript and not the model. The figures and
what is behind them are in
[bench/baselines/2026-11-16-agent.md](../bench/baselines/2026-11-16-agent.md).
The model is called through OpenRouter, pinned to Anthropic's servers, and
[comes out the same that way](../bench/baselines/2026-11-17-agent-openrouter.md).

## The matched reference

A job with a reference subtitle can hand that subtitle back as a second file,
reduced to what the target says too. It holds the dialogue lines of the
reference that have a counterpart in the delivered target, with the same
content options applied. Signs, songs, sound descriptions and comments are
left out, and so is dialogue the target has nothing for. The two files lie on
one timeline, so together they are a dual subtitle, and line by line the one
is a translation of the other. The retimed target is delivered as always.

- **Which lines belong together** is read off the clock once the target is in
  place: a line of the reference and a dialogue line of the target that are on
  screen together for at least half of the shorter of the two. A long sentence
  in one language is often two lines in the other, and then all of them stay.
  The text is not compared a second time. A name or a short answer reads too
  differently across languages for that.
- **Times.** The lines keep the reference's own times, which are the ones in
  sync. Only where audio was sent as well and the reference disagreed with it
  are they moved onto the audio, as they were for the sync itself.
- **When there is none.** Lines of the target in a block that could not be
  placed with confidence pair with nothing. A job that ends as
  `rejected_no_match`, or whose target could only be placed by the timing of
  speech, has no matched reference. The report says why, and so does a
  warning.
- **The report** counts under `matched_reference` what was kept and what was
  left out, by reason.
- **The pairs.** The report also lists, under `matched_reference.pairs`, which
  lines of the two delivered files belong together, so that nobody has to work
  it out from the times again. A pair names its lines on both sides by their
  number in the delivered file, with their times and their plain text:

  ```json
  {"start_ms": 61000, "end_ms": 69000,
   "target": [{"index": 5, "start_ms": 61000, "end_ms": 63000, "text": "昨日の夜は"},
              {"index": 6, "start_ms": 66000, "end_ms": 69000, "text": "どこにいたんだ"}],
   "reference": [{"index": 5, "start_ms": 61000, "end_ms": 69000, "text": "Where were you last night?"}]}
  ```

  `index` counts from 1 over the lines a player shows as text, which in an SRT
  file is the cue number. Mostly a pair is one line on each side. Where the
  two languages cut a sentence differently, or a script draws one line in
  several layers, everything that hangs together is one pair. Every line of
  the matched reference is in exactly one pair, and the pairs come in the
  order they are shown.

Tried on four episodes of an anime series, with a fansub's English script as
the reference for the Japanese broadcast captions: 1,414 of the 1,415 English
dialogue lines came back, and none of the 445 signs and 672 song lines. The
one line is a chant that the captions describe as a sound. The other way
round, 1,680 of the 1,779 caption lines came back. What stays out are gasps,
laughs and a newscast in the background, which the English script does not
have. A mistimed copy of the captions, late and with a cut of 35 seconds in
it, gave the same file byte for byte.

## Several reference subtitles

A video file often holds more than one subtitle track: the full dialogue, a
track with only signs and songs, a second language, a commentary. Which of
them is in sync with the picture is not the question, they all are. The
question is which one says what the target says, and a label such as "English"
does not answer it. So a job may carry up to six reference subtitles, and the
service looks into each of them.

Every track is reduced to its dialogue and matched against the target, with
the same matching and the same test the sync itself uses. Then:

- **The track that fits is taken.** With several that fit, the one in which
  most lines of the target find their counterpart. Labels only decide between
  tracks that fit about equally well, within two lines or 5% of each other: a
  track that is not marked as forced before one that is, then the default
  track, then the one with more dialogue, then the one that was sent first.
- **A track of signs is not other content.** A track without dialogue, or
  with far too little of it to carry the target (fewer than 30 lines and less
  than a fifth of the target's), is set aside as `too_little_dialogue`. A track
  with dialogue of its own that does not match is `no_match`: another
  episode, another film.
- **The same track twice is compared once.** A track with the same dialogue
  at the same times as an earlier one is a `duplicate`.
- **A file that is no subtitle** does not stop the job when other tracks
  came with it. It is `unreadable`, with the reason.

The job says what it found, in its summary under `reference`:

```json
{
  "chosen": 1,
  "reason": "Of 2 reference subtitles only \"Full Subtitles\" fits the target: 215 of 280 lines of dialogue have a counterpart in it.",
  "candidates": [
    {"index": 0, "language": "eng", "title": "Signs & Songs", "default": false, "forced": true,
     "filename": "track3.ass", "lines": 460, "dialogue_lines": 0,
     "matched_lines": 0, "comparable_lines": 0, "share": 0.0,
     "verdict": "too_little_dialogue",
     "reason": "It has no dialogue: its 460 lines are signs, songs or sound descriptions.",
     "duplicate_of": null},
    {"index": 1, "language": "eng", "title": "Full Subtitles", "default": true, "forced": false,
     "filename": "track2.ass", "lines": 733, "dialogue_lines": 273,
     "matched_lines": 215, "comparable_lines": 280, "share": 0.768,
     "verdict": "chosen",
     "reason": "The target's dialogue finds its counterpart in it: 215 of 280 lines.",
     "duplicate_of": null}
  ]
}
```

`index` is the position of the track among the `reference_subtitle` parts of
the request, from 0. `chosen` is the index of the track the job was synced
against, or `null` when none of them fits. `matched_lines` of
`comparable_lines` is how much of the target's dialogue found a counterpart.
The report has the same under `reference_choice`, and there every track also
counts under `left_out` the lines that are no dialogue, by what they are.

| Verdict | Meaning |
| --- | --- |
| `chosen` | The job was synced against this track. |
| `fits` | It fits the target too, and another one fits more or was preferred. |
| `duplicate` | The same dialogue as the earlier track `duplicate_of`. |
| `too_little_dialogue` | Signs, songs, or too few spoken lines to judge the target by. |
| `no_match` | Dialogue of its own that is not the target's. |
| `unreadable` | Not a subtitle, or more lines than a subtitle may have. `reason` says which. |

When no track fits, what happens depends on what else the job has:

| The job has | Outcome |
| --- | --- |
| Audio as well | The target is synced against the audio alone, and a warning says why. |
| No audio, several tracks | `rejected_no_match`. Tracks of signs alone are refused without a call to the agent's model. Of tracks that are other content, the closest is still put before the agent, which may find that it is the same content after all. A client that has the video can send the job again with audio. |
| No audio, one track | As for any job with one reference subtitle: it is tried, and the outcome says how that went. |

The matched reference is made from the chosen track and keeps that track's
format.

What it costs: only the embeddings of the tracks' dialogue, which are cached
by content. A job with two tracks of an episode of 24 minutes cost 0.03 cents
and took about ten seconds the first time, and under a second after that.
Three tracks of an episode twice as long cost 0.09 cents and took 42 seconds.
A job with several tracks counts as one job for the rate limits.

Tried on four episodes of an anime series, with Japanese captions as the
target and the tracks a release holds: the full English script, the same
script reduced to its signs and songs, Japanese and Chinese scripts, and the
script of another episode. The full track was chosen in either order of
sending. The signs track came back as `too_little_dialogue`, and the other
episode as `no_match` with 4 of 280 lines. Two tracks of signs alone were
refused. On an open film, a signs track and the script of other content were
refused without audio, and with the audio the same job was synced against the
audio alone.

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
