# Several reference tracks: 53 cases, 2026-11-18

The first run over the family `several_references`
([bench/README.md](../README.md#several-reference-tracks)): a target against
the two to six subtitle tracks of one video, of which one is the dialogue, or
none. The same seven systems as in
[the run over 535 cases](2026-11-18-real-file-problems.md), every case once.

The retimer gets every track with its labels and chooses. `alass` and
`ffsubsync` take one reference, so they get the track a client would send
that sends a single one: the track marked default, and the first where none
is marked. With the audio beside the tracks they get the audio.

A case is **solved** as in the other run: 95% of the dialogue lines within
250 ms of their true start, or, where nothing can be timed, the file refused
or left as it was.

## In short

| System | Solved of 53 |
| --- | --- |
| `pipeline` | 52 (98%) |
| `agent-haiku` | 50 (94%) |
| `ffsubsync-split` | 18 (34%) |
| `alass` | 13 (25%) |
| `ffsubsync` | 10 (19%) |
| `input` | 10 (19%) |

- **The choice was right in all 53 cases**, for the pipeline and for the
  agent: a dialogue track of the film was taken wherever there is one (39
  cases), in any position, without labels and against labels that point to
  the signs. Where there is none (14 cases) no track passed.
- **One track is not enough where the file mislabels it.** In the six kinds
  of case in which the default or first track is signs or another episode,
  `alass` and `ffsubsync` solve none of 30. Given the dialogue track they do
  what they do elsewhere: `alass` solves 13 of 15, `ffsubsync-split` 15 of 15.
- **The agent takes the next episode for the same film in 3 of 5 cases.**
  This is the weakness the run over 535 cases found in `next_episode`, met
  here by another road. See below.
- **The pipeline misses one case by its timing, not by its choice**: a film of
  26 lines at the wrong frame rate, 77% of the lines within 250 ms and all
  within 500 ms.

## Case by case

| Case | Cases | `agent-haiku` | `pipeline` | `alass` | `ffsubsync-split` | `ffsubsync` | `input` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `full-and-signs` | 5 | 5 | 5 | 4 | 5 | 5 | 0 |
| `signs-marked-default` | 5 | 5 | 5 | 0 | 0 | 0 | 0 |
| `three-without-labels` | 5 | 5 | 5 | 0 | 0 | 0 | 0 |
| `two-languages-fit` | 5 | 5 | 5 | 5 | 5 | 0 | 0 |
| `same-track-twice` | 5 | 5 | 4 | 0 | 0 | 0 | 0 |
| `six-tracks` | 5 | 5 | 5 | 0 | 0 | 0 | 0 |
| `in-sync` | 5 | 5 | 5 | 4 | 5 | 5 | 5 |
| `only-signs` | 5 | 5 | 5 | 0 | 0 | 0 | 5 |
| `other-episode-and-signs` | 5 | 2 | 5 | 0 | 0 | 0 | 0 |
| `full-and-signs`, with audio | 2 | 2 | 2 | 0 | 1 | 0 | 0 |
| `three-without-labels`, with audio | 2 | 2 | 2 | 0 | 0 | 0 | 0 |
| `only-signs`, with audio | 2 | 2 | 2 | 0 | 1 | 0 | 0 |
| `other-episode-and-signs`, with audio | 2 | 2 | 2 | 0 | 1 | 0 | 0 |
| All | 53 | 50 | 52 | 13 | 18 | 10 | 10 |

Five seeds, the two films and the three episodes of the made-up series, and
the two films again for the cases with audio.

Which track the retimer chose, by what the track is:

| Cases | Tracks they hold | `pipeline` chose | `agent-haiku` chose |
| --- | --- | --- | --- |
| 39 with a dialogue track of the film | Signs in all 39, another episode in 12, a second dialogue track in 15 | The dialogue in 39 | The dialogue in 39 |
| 7 of `only-signs` | Signs only | None in 7 | None in 7 |
| 7 of `other-episode-and-signs` | Another episode and signs | None in 7 | None in 4, the other episode in 3 |

In `two-languages-fit` and `six-tracks` two dialogue tracks fit, one in the
language of the target and one in the language of the reference. Either is a
right choice, and the count of matched lines decides between them.

## Where the retimer loses

**The next episode, taken for the same one (agent, 3 cases).** In
`other-episode-and-signs` no track passes the routine check: the track of
signs has no dialogue, and the other episode shares 11 to 15 of some 320
lines with the target, its opening narration and a few phrases. The fixed
pipeline refuses all five. The closest track is then handed to the model to
read side by side, as a single reference would be, and for the three episodes
of the series the model calls it the same content. The file comes back as
`partial` with a confidence of 0.02 to 0.06. The two films share nothing, and
there the job is refused without the model being asked. With the audio beside
the tracks the same cases are synced on the audio and solved.

**A short film at the wrong frame rate (pipeline, 1 case).** `same-track-twice`
on the film with 26 lines: the dialogue track was chosen, and the fit of the
rate on so few loosely timed lines leaves 6 lines between 250 ms and 500 ms
off. The agent solves it.

## What this does and does not show

- The tracks are made from the seeds: a full script with made-up songs and
  signs, the same songs and signs alone, the other language, the next
  episode. They are the shapes real video files have, not real tracks.
- A check on real videos was done apart from this run, and its files cannot
  be published: 455 cases from 222 videos that hold two or more text tracks,
  the choice alone, without syncing. A dialogue track was chosen in 372 of
  372 cases with a target of the video itself, and none in 83 of 83 with a
  target of another video.
- `alass` and `ffsubsync` are measured on the one track a client would pick
  by the file's flags. A client that picks better gives them more to work
  with. The table says what the choice is worth to a client that cannot tell.

## How it was run

```
retimer bench fetch-seeds
retimer bench generate
retimer bench run --family several_references --systems input,oracle,ffsubsync,ffsubsync-split,alass,pipeline,agent-haiku --workers 4
```

Three and a half minutes on four cores, on the code of the change that added
the family. `oracle` solves all 53.

| System | Runs | Ended in an error | Mean seconds per case | Cost of the run (USD) |
| --- | --- | --- | --- | --- |
| `agent-haiku` | 53 | 0 | 2.4 | 0.015 |
| `pipeline` | 53 | 0 | 7.8 | 0.075 |
| `alass` | 53 | 0 | 0.7 | 0.00 |
| `ffsubsync-split` | 53 | 0 | 2.4 | 0.00 |
| `ffsubsync` | 53 | 0 | 1.4 | 0.00 |

The pipeline ran first and on an empty cache. Its 45 cases without audio took
5.8 s and 0.007 cents each, which is reading and comparing every track for
the first time. Seven of its 7.5 cents are transcripts of the two films,
fetched more than once because four workers asked for them at the same time.
The agent ran after it and read tracks and transcripts from the cache. It
asked the model about 13 of the 53 files, at 0.12 cents a file when asked.

## The run's report

Run started 2026-11-18T14:47:42+00:00, 53 cases, 1 run(s) per case. Systems: input, oracle, ffsubsync (ffsubsync 0.5.1), ffsubsync-split (ffsubsync 0.5.1), alass (alass-cli 2.0.0), pipeline, agent-haiku (claude-haiku-5-5, effort medium, consult auto).

File success is the share of files with at least 95% of dialogue lines within 250 ms of their true start, and the column after it is the same with 500 ms. Error columns are medians over files. The tolerance columns pool all dialogue lines. A tool that reports no outcome counts as having left a file alone when no line moved more than 100 ms.

### Overall

| System | Runs | File success | At 500 ms | Median error (ms) | 90th pct error (ms) | Within 100 ms | Within 250 ms | Within 500 ms | Decisions right | Failed | Mean time (s) | Cost (USD) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| agent-haiku | 53 | 100% | 100% | 13 | 26 | 96% | 100% | 100% | 80% | 0 | 2.4 | 0.0154 |
| alass | 53 | 30% | 30% | 78,570 | 217,772 | 37% | 41% | 41% | 20% | 0 | 0.7 | 0.0000 |
| ffsubsync | 53 | 23% | 30% | 10,026 | 34,900 | 37% | 38% | 39% | 33% | 0 | 1.4 | 0.0000 |
| ffsubsync-split | 53 | 42% | 42% | 18,260 | 23,898 | 41% | 44% | 44% | 33% | 0 | 2.4 | 0.0000 |
| input | 53 | 12% | 12% | 8,000 | 20,900 | 19% | 19% | 19% | 67% | 0 | 0.0 | 0.0000 |
| oracle | 53 | 100% | 100% | 0 | 0 | 100% | 100% | 100% | 100% | 0 | 0.0 | 0.0000 |
| pipeline | 53 | 98% | 100% | 13 | 26 | 96% | 100% | 100% | 100% | 0 | 7.8 | 0.0753 |

### By reference

| System | Reference | Runs | File success | At 500 ms | Median error (ms) | 90th pct error (ms) | Within 100 ms | Within 250 ms | Within 500 ms | Decisions right | Failed | Mean time (s) | Cost (USD) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| agent-haiku | subtitle | 45 | 100% | 100% | 8 | 15 | 98% | 100% | 100% | 80% | 0 | 2.7 | 0.0148 |
| agent-haiku | both | 8 | 100% | 100% | 110 | 123 | 46% | 100% | 100% | n/a | 0 | 0.7 | 0.0006 |
| alass | subtitle | 45 | 37% | 37% | 78,570 | 217,772 | 38% | 43% | 43% | 20% | 0 | 0.1 | 0.0000 |
| alass | both | 8 | 0% | 0% | 145,915 | 234,721 | 1% | 4% | 8% | n/a | 0 | 3.9 | 0.0000 |
| ffsubsync | subtitle | 45 | 29% | 29% | 8,322 | 34,900 | 37% | 37% | 38% | 33% | 0 | 0.8 | 0.0000 |
| ffsubsync | both | 8 | 0% | 38% | 43,836 | 52,899 | 28% | 59% | 64% | n/a | 0 | 4.6 | 0.0000 |
| ffsubsync-split | subtitle | 45 | 43% | 43% | 20,678 | 66,090 | 40% | 43% | 43% | 33% | 0 | 2.0 | 0.0000 |
| ffsubsync-split | both | 8 | 38% | 38% | 1,158 | 1,458 | 56% | 56% | 56% | n/a | 0 | 5.0 | 0.0000 |
| input | subtitle | 45 | 14% | 14% | 13,645 | 21,748 | 19% | 19% | 19% | 67% | 0 | 0.0 | 0.0000 |
| input | both | 8 | 0% | 0% | 8,000 | 8,000 | 6% | 6% | 6% | n/a | 0 | 0.0 | 0.0000 |
| oracle | subtitle | 45 | 100% | 100% | 0 | 0 | 100% | 100% | 100% | 100% | 0 | 0.0 | 0.0000 |
| oracle | both | 8 | 100% | 100% | 0 | 0 | 100% | 100% | 100% | n/a | 0 | 0.0 | 0.0000 |
| pipeline | subtitle | 45 | 97% | 100% | 8 | 15 | 98% | 100% | 100% | 100% | 0 | 5.8 | 0.0030 |
| pipeline | both | 8 | 100% | 100% | 110 | 123 | 46% | 100% | 100% | n/a | 0 | 19.3 | 0.0723 |

### What the model was asked

An agent system calls its model only for files whose draft plan leaves something open. Turns, time and cost are means over the runs in which it was called. A plan is kept back when the model's changes put fewer certain lines in place than the plan it started from.

| System | Model | Runs | Model asked | Turns when asked | Seconds when asked | Cost when asked (USD) | Cost per run (USD) | Plans changed | Kept back | Not finished |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| agent-haiku | claude-haiku-5-5 | 53 | 13 (25%) | 2.5 | 8.3 | 0.0012 | 0.00029 | 9 | 0 | 0 |
