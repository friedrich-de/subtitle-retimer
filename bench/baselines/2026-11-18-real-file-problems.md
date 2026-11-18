# The retimer, alass and ffsubsync on 535 cases, 2026-11-18

The first run over the cases that replicate real files and over the made-up
series ([bench/README.md](../README.md#cases-that-replicate-real-files)).
Seven systems, every case once:

- `input`, the target as it was sent.
- `ffsubsync` 0.5.1 with its defaults, and `ffsubsync-split`, the same with
  `--split-penalty`, which lets it look for breaks.
- `alass` 2.0.0 with its defaults.
- `pipeline`, this project's fixed pipeline, and `agent-haiku`, the same with
  the agent on Haiku 5.5 at medium effort, called through OpenRouter.
- `oracle`, which applies the plan a case was made with. It solves all 528
  cases it is given and sits out the seven whose odd times no plan undoes.

| Cases | From the two films | From the series | Together |
| --- | --- | --- | --- |
| Plain: offset, rate, breaks, noise, other content | 194 | 117 | 311 |
| After real files, eleven families | 116 | 108 | 224 |
| Together | 310 | 225 | 535 |

A case that asks for timing is **solved** when 95% of its dialogue lines are
within 250 ms of their true start. A case that asks for a refusal, or for the
file to be left alone, is solved when the system does that. The run's own
report at the end of this file counts the two apart: file success over the 499
cases that ask for timing, and decisions.

## In short

| System | Timing, against a reference subtitle (345) | Timing, against audio alone (116) | Timing, against both (38) | Refuse, or leave alone (36) | All (535) |
| --- | --- | --- | --- | --- | --- |
| `agent-haiku` | 318 (92%) | 86 (74%) | 38 (100%) | 28 (78%) | 470 (88%) |
| `pipeline` | 306 (89%) | 84 (72%) | 35 (92%) | 36 (100%) | 461 (86%) |
| `alass` | 310 (90%) | 0 (0%) | 0 (0%) | 0 (0%) | 310 (58%) |
| `ffsubsync-split` | 236 (68%) | 24 (21%) | 10 (26%) | 0 (0%) | 270 (50%) |
| `ffsubsync` | 176 (51%) | 13 (11%) | 5 (13%) | 0 (0%) | 194 (36%) |
| `input` | 25 (7%) | 8 (7%) | 4 (11%) | 15 (42%) | 52 (10%) |

- **Over everything the retimer solves 86% to 88% of the cases, alass 58%,
  ffsubsync 36% to 50%.** Most of that lead comes from two things the other
  tools do not do at all: syncing against audio, and saying no.
- **Timing against a reference subtitle is close.** The agent solves 318 of
  345, alass 310, the fixed pipeline 306. alass is a strong tool where it has
  a subtitle to hold on to, and it solves 15 cases the agent does not: 8
  episode packs and 7 files with drifting captions.
- **Against audio alass solves nothing** (0 of 154 with audio alone or with
  both), as on the seed cases before. ffsubsync solves 34 of 154 with its
  split option. The agent solves 124, the pipeline 119.
- **No tool refuses.** In all 36 cases that ask for it, alass and ffsubsync
  move the file anyway: onto another episode, or onto the times of signs.
- **The agent loses refusals that the pipeline gets right.** See below.

## The cases after real files

| Family | Cases | `agent-haiku` | `pipeline` | `alass` | `ffsubsync-split` | `ffsubsync` | `input` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `loose_reference` | 30 | 30 of 30 | 30 of 30 | 30 of 30 | 25 of 30 | 20 of 30 | 5 of 30 |
| `cold_open` | 33 | 28 of 33 | 27 of 33 | 22 of 33 | 18 of 33 | 14 of 33 | 0 of 33 |
| `caption_drift` | 21 | 0 of 21 | 0 of 21 | 7 of 21 | 5 of 21 | 2 of 21 | 0 of 21 |
| `pack` | 21 | 13 of 21 | 11 of 21 | 14 of 21 | 5 of 21 | 6 of 21 | 0 of 21 |
| `mid_scene` | 21 | 16 of 21 | 16 of 21 | 11 of 21 | 14 of 21 | 0 of 21 | 0 of 21 |
| `next_episode` | 14 | 8 of 14 | 14 of 14 | 0 of 14 | 0 of 14 | 0 of 14 | 0 of 14 |
| `signs_reference` | 15 | 15 of 15 | 15 of 15 | 0 of 15 | 0 of 15 | 0 of 15 | 15 of 15 |
| `karaoke` | 27 | 26 of 27 | 26 of 27 | 14 of 27 | 18 of 27 | 5 of 27 | 0 of 27 |
| `captions` | 14 | 12 of 14 | 12 of 14 | 9 of 14 | 10 of 14 | 6 of 14 | 0 of 14 |
| `bilingual` | 21 | 18 of 21 | 18 of 21 | 13 of 21 | 17 of 21 | 6 of 21 | 0 of 21 |
| `odd_times` | 7 | 7 of 7 | 6 of 7 | 5 of 7 | 0 of 7 | 0 of 7 | 0 of 7 |

What each family is, and what real files did, is in
[bench/README.md](../README.md#cases-that-replicate-real-files).

- **Karaoke and signs.** With thousands of karaoke and sign lines around the
  dialogue, alass solves 14 of 27 and ffsubsync 5 (18 with the split option).
  The retimer solves 26. Both tools read every line of a script as speech, so
  a song drawn syllable by syllable outweighs the dialogue.
- **A reference of signs only.** The right answer is to leave the file alone.
  alass and ffsubsync move the dialogue in 15 of 15.
- **Odd times.** ffsubsync ends in an error on all 7. alass reads 5.
- **Loosely timed references** cost alass nothing (30 of 30) and ffsubsync a
  third.

Recipe by recipe:

| Recipe | Cases | `agent-haiku` | `pipeline` | `alass` | `ffsubsync-split` | `ffsubsync` | `input` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `loose-reference-blocks-2` | 5 | 5 of 5 | 5 of 5 | 5 of 5 | 5 of 5 | 0 of 5 | 0 of 5 |
| `loose-reference-blocks-3` | 5 | 5 of 5 | 5 of 5 | 5 of 5 | 0 of 5 | 0 of 5 | 0 of 5 |
| `loose-reference-early-1.5s` | 5 | 5 of 5 | 5 of 5 | 5 of 5 | 5 of 5 | 5 of 5 | 0 of 5 |
| `loose-reference-in-sync` | 5 | 5 of 5 | 5 of 5 | 5 of 5 | 5 of 5 | 5 of 5 | 5 of 5 |
| `loose-reference-late-8s` | 5 | 5 of 5 | 5 of 5 | 5 of 5 | 5 of 5 | 5 of 5 | 0 of 5 |
| `loose-reference-rate-25-to-23.976` | 5 | 5 of 5 | 5 of 5 | 5 of 5 | 5 of 5 | 5 of 5 | 0 of 5 |
| `cold-open-broadcast-with-breaks` | 7 | 6 of 7 | 6 of 7 | 4 of 7 | 0 of 7 | 0 of 7 | 0 of 7 |
| `cold-open-scene-4s-early` | 7 | 5 of 7 | 5 of 7 | 5 of 7 | 4 of 7 | 3 of 7 | 0 of 7 |
| `cold-open-scene-4s-early-rough-reference` | 5 | 3 of 5 | 3 of 5 | 3 of 5 | 3 of 5 | 3 of 5 | 0 of 5 |
| `cold-open-sponsor-card-10s` | 7 | 7 of 7 | 7 of 7 | 5 of 7 | 5 of 7 | 3 of 7 | 0 of 7 |
| `cold-open-two-lines-then-card` | 7 | 7 of 7 | 6 of 7 | 5 of 7 | 6 of 7 | 5 of 7 | 0 of 7 |
| `caption-drift-half-the-file-0.5s-early` | 7 | 0 of 7 | 0 of 7 | 1 of 7 | 2 of 7 | 0 of 7 | 0 of 7 |
| `caption-drift-one-stretch-0.4s-early` | 7 | 0 of 7 | 0 of 7 | 3 of 7 | 3 of 7 | 2 of 7 | 0 of 7 |
| `caption-drift-two-stretches-off` | 7 | 0 of 7 | 0 of 7 | 3 of 7 | 0 of 7 | 0 of 7 | 0 of 7 |
| `pack-first-of-two` | 7 | 7 of 7 | 7 of 7 | 5 of 7 | 5 of 7 | 6 of 7 | 0 of 7 |
| `pack-middle-of-three` | 7 | 2 of 7 | 2 of 7 | 5 of 7 | 0 of 7 | 0 of 7 | 0 of 7 |
| `pack-second-of-two` | 7 | 4 of 7 | 2 of 7 | 4 of 7 | 0 of 7 | 0 of 7 | 0 of 7 |
| `mid-scene-cut-3s` | 7 | 6 of 7 | 5 of 7 | 4 of 7 | 4 of 7 | 0 of 7 | 0 of 7 |
| `mid-scene-insert-5s` | 7 | 6 of 7 | 7 of 7 | 5 of 7 | 5 of 7 | 0 of 7 | 0 of 7 |
| `mid-scene-two-edits` | 7 | 4 of 7 | 4 of 7 | 2 of 7 | 5 of 7 | 0 of 7 | 0 of 7 |
| `next-episode-shared-opening` | 7 | 4 of 7 | 7 of 7 | 0 of 7 | 0 of 7 | 0 of 7 | 0 of 7 |
| `next-episode-shared-phrases` | 7 | 4 of 7 | 7 of 7 | 0 of 7 | 0 of 7 | 0 of 7 | 0 of 7 |
| `signs-reference-plain-signs-late-8s` | 5 | 5 of 5 | 5 of 5 | 0 of 5 | 0 of 5 | 0 of 5 | 5 of 5 |
| `signs-reference-signs-and-songs-in-sync` | 5 | 5 of 5 | 5 of 5 | 0 of 5 | 0 of 5 | 0 of 5 | 5 of 5 |
| `signs-reference-signs-and-songs-late-8s` | 5 | 5 of 5 | 5 of 5 | 0 of 5 | 0 of 5 | 0 of 5 | 5 of 5 |
| `karaoke-heavy-both-blocks-2` | 5 | 5 of 5 | 5 of 5 | 0 of 5 | 3 of 5 | 0 of 5 | 0 of 5 |
| `karaoke-heavy-reference` | 5 | 5 of 5 | 5 of 5 | 5 of 5 | 5 of 5 | 5 of 5 | 0 of 5 |
| `karaoke-heavy-reference-blocks-2` | 5 | 5 of 5 | 5 of 5 | 3 of 5 | 5 of 5 | 0 of 5 | 0 of 5 |
| `karaoke-heavy-target` | 7 | 6 of 7 | 6 of 7 | 4 of 7 | 0 of 7 | 0 of 7 | 0 of 7 |
| `karaoke-huge-reference` | 5 | 5 of 5 | 5 of 5 | 2 of 5 | 5 of 5 | 0 of 5 | 0 of 5 |
| `captions-cleaned` | 7 | 6 of 7 | 6 of 7 | 5 of 7 | 5 of 7 | 3 of 7 | 0 of 7 |
| `captions-kept` | 7 | 6 of 7 | 6 of 7 | 4 of 7 | 5 of 7 | 3 of 7 | 0 of 7 |
| `bilingual-furigana` | 7 | 6 of 7 | 6 of 7 | 5 of 7 | 6 of 7 | 0 of 7 | 0 of 7 |
| `bilingual-stacked` | 7 | 7 of 7 | 7 of 7 | 5 of 7 | 6 of 7 | 6 of 7 | 0 of 7 |
| `bilingual-twin-tracks` | 7 | 5 of 7 | 5 of 7 | 3 of 7 | 5 of 7 | 0 of 7 | 0 of 7 |
| `odd-times-script-cut-into-parts` | 7 | 7 of 7 | 6 of 7 | 5 of 7 | 0 of 7 | 0 of 7 | 0 of 7 |

## What only the retimer is asked to do

| What the case asks | Cases | `agent-haiku` | `pipeline` | `alass` | `ffsubsync-split` | `ffsubsync` | `input` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Refuse a subtitle of other content | 7 | 5 of 7 | 7 of 7 | 0 of 7 | 0 of 7 | 0 of 7 | 0 of 7 |
| Refuse the next episode of the series | 14 | 8 of 14 | 14 of 14 | 0 of 14 | 0 of 14 | 0 of 14 | 0 of 14 |
| Leave the file alone when the reference holds only signs and songs | 15 | 15 of 15 | 15 of 15 | 0 of 15 | 0 of 15 | 0 of 15 | 15 of 15 |
| Place the one episode of a pack that the reference covers | 21 | 13 of 21 | 11 of 21 | 14 of 21 | 5 of 21 | 6 of 21 | 0 of 21 |
| Place it, and take the other episodes out of the file | 21 | 6 of 21 | 3 of 21 | 0 of 21 | 0 of 21 | 0 of 21 | 0 of 21 |
| Sync captions, and remove the sound descriptions and songs as asked | 7 | 6 of 7 | 6 of 7 | 0 of 7 | 0 of 7 | 0 of 7 | 0 of 7 |
| Sync, and take out credits and adverts the reference has no place for | 48 | 48 of 48 | 18 of 48 | 0 of 48 | 0 of 48 | 0 of 48 | 0 of 48 |

alass places a pack's own episode more often than the retimer does. The other
episodes stay in its output: stacked at time zero where they came before the
episode, and past its end where they came after.

## Where the retimer loses

- **Captions that drift (0 of 21).** One or two stretches sit 0.35 s to 0.5 s
  early. The retimer places the file as a whole and leaves those stretches
  where they are: 20 of the 21 files are within 500 ms and none within 250 ms.
  alass solves 7 and ffsubsync with its split option 5. Nothing solves this
  family well.
- **Episode packs against a reference subtitle (agent 7 of 15, pipeline 5,
  alass 14).** When the episode is not the first in the file, the pipeline
  finds the timing and then stops: the earlier episodes would land before
  zero, it calls that no valid timing, and it hands the file back unchanged.
  Against audio it solves all 6.
- **The next episode, with the agent (8 of 14, pipeline 14 of 14).** All
  eight refusals the agent loses are on the series: 6 of 6 next episodes and
  2 of 3 plain other episodes. There the next episode shares a ninth of its
  lines with the reference, as an opening like a recap or as short phrases
  all over the file, and every episode begins with the same narration. The
  pipeline refuses, is in doubt because some pairs do agree, and asks the
  agent. The agent reads how both files begin, finds that they agree line by
  line, and answers "same content", eight times out of eight. On the films
  three lines are shared. The pipeline asks twice there, and the agent
  answers "other content" both times.
- **Two edits in the middle of scenes (4 of 7)**, and edits in the middle of a
  scene against audio (agent 2 of 6, pipeline 4 of 6).

Against the pipeline the agent solves 19 cases more (13 with breaks, two
packs, two edits in the middle of a scene, two others) and 10 fewer: the
eight refusals and two of the edits against audio.

## Films and series

| System | Plain, films (194) | Plain, series (117) | After real files, films (116) | After real files, series (108) |
| --- | --- | --- | --- | --- |
| `agent-haiku` | 182 (94%) | 115 (98%) | 87 (75%) | 86 (80%) |
| `pipeline` | 169 (87%) | 117 (100%) | 84 (72%) | 91 (84%) |
| `alass` | 74 (38%) | 111 (95%) | 45 (39%) | 80 (74%) |
| `ffsubsync-split` | 81 (42%) | 77 (66%) | 42 (36%) | 70 (65%) |
| `ffsubsync` | 63 (32%) | 72 (62%) | 18 (16%) | 41 (38%) |
| `input` | 20 (10%) | 12 (10%) | 8 (7%) | 12 (11%) |

The plain cases, on the series:

| Family | Cases | `agent-haiku` | `pipeline` | `alass` | `ffsubsync-split` | `ffsubsync` | `input` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `none` | 6 | 6 of 6 | 6 of 6 | 6 of 6 | 6 of 6 | 6 of 6 | 6 of 6 |
| `constant` | 30 | 30 of 30 | 30 of 30 | 30 of 30 | 30 of 30 | 30 of 30 | 6 of 30 |
| `rate` | 18 | 18 of 18 | 18 of 18 | 18 of 18 | 18 of 18 | 18 of 18 | 0 of 18 |
| `blocks` | 30 | 30 of 30 | 30 of 30 | 27 of 30 | 4 of 30 | 0 of 30 | 0 of 30 |
| `mixed` | 12 | 12 of 12 | 12 of 12 | 12 of 12 | 1 of 12 | 0 of 12 | 0 of 12 |
| `noise` | 18 | 18 of 18 | 18 of 18 | 18 of 18 | 18 of 18 | 18 of 18 | 0 of 18 |
| `wrong_content` | 3 | 1 of 3 | 3 of 3 | 0 of 3 | 0 of 3 | 0 of 3 | 0 of 3 |

And on the films:

| Family | Cases | `agent-haiku` | `pipeline` | `alass` | `ffsubsync-split` | `ffsubsync` | `input` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `none` | 10 | 10 of 10 | 10 of 10 | 4 of 10 | 7 of 10 | 4 of 10 | 10 of 10 |
| `constant` | 50 | 50 of 50 | 50 of 50 | 20 of 50 | 35 of 50 | 26 of 50 | 10 of 50 |
| `rate` | 30 | 30 of 30 | 30 of 30 | 12 of 30 | 18 of 30 | 15 of 30 | 0 of 30 |
| `blocks` | 50 | 44 of 50 | 34 of 50 | 19 of 50 | 3 of 50 | 0 of 50 | 0 of 50 |
| `mixed` | 20 | 14 of 20 | 11 of 20 | 7 of 20 | 0 of 20 | 0 of 20 | 0 of 20 |
| `noise` | 30 | 30 of 30 | 30 of 30 | 12 of 30 | 18 of 30 | 18 of 30 | 0 of 30 |
| `wrong_content` | 4 | 4 of 4 | 4 of 4 | 0 of 4 | 0 of 4 | 0 of 4 | 0 of 4 |

On the series alass and ffsubsync do far better than on the films. An episode
has about 395 lines of talk and every case has a reference subtitle. The films
have 76 and 26 lines under music and effects, and 116 of their 194 plain cases
have audio as the reference or as part of it. On the films the agent and the
pipeline are where earlier runs had them (182 and 169 of 194, against 183 and
170).

## What this does and does not show

- **Home ground.** The families come from the problems this project found in
  real files and then worked on. alass and ffsubsync ran as they come, with
  one variant of ffsubsync, and nobody tuned them. A run on kept real files,
  with both tools tuned on a tenth of the pairs, is the counterpart to this
  one and is not in this file.
- **The series is easier than a real episode for a tool that matches timing.**
  Its lines are written by a model and timed by rule. Both languages are timed
  from the same beats, so their lines start together far more often than two
  real subtitles of one episode do. The plain cases use those two files as
  they are. alass's 111 of 117 there is an upper bound, not an expectation.
  The cases after real files do not have this advantage: each gets a reference
  that was timed and cut anew.
- **Audio means two films.** The series has no sound, so every case against
  audio is from Tears of Steel or Sintel: English speech, much music, few
  lines. Nothing here says how any system does on Japanese audio.
- **One run.** The agent's answers and a transcript both vary a little from
  run to run. Earlier runs put that at two or three files.
- **Truth is exact here.** Every true start is known because the cases are
  made, not found. That is what real files cannot give, and why both kinds of
  run are needed.

## How it was run

```
retimer bench fetch-seeds
retimer bench run --systems input,oracle,ffsubsync,ffsubsync-split,alass,pipeline,agent-haiku --workers 4
```

25 minutes on four cores, at commit `59160d2`. The agent cost 29 cents: it was
asked about 195 of the 535 files, at 0.15 cents a file when asked. The
pipeline's 7 cents are the transcripts of the two films and the embeddings of
a first run, and a second run reads both from the cache.

| System | Runs | Ended in an error | Mean seconds per case | Cost of the run (USD) |
| --- | --- | --- | --- | --- |
| `agent-haiku` | 535 | 0 | 3.8 | 0.29 |
| `pipeline` | 535 | 0 | 0.9 | 0.07 |
| `alass` | 535 | 0 | 1.2 | 0.00 |
| `ffsubsync-split` | 535 | 7 | 2.9 | 0.00 |
| `ffsubsync` | 535 | 7 | 2.0 | 0.00 |

After this run the branch took in the change that lets a job carry several
reference subtitles (#16). The fixed pipeline was run again on the merged
code and solves the same 461 cases. Nineteen cases against audio differ in
the size of their error, and not because of the code: the first run began on
an empty cache, its workers fetched a transcript of each film more than once,
and the cache kept the last one.

## The run's report

Run started 2026-11-18T13:58:14+00:00, 535 cases, 1 run(s) per case. Systems: input, oracle, ffsubsync (ffsubsync 0.5.1), ffsubsync-split (ffsubsync 0.5.1), alass (alass-cli 2.0.0), pipeline, agent-haiku (claude-haiku-5-5, effort medium, consult auto).

File success is the share of files with at least 95% of dialogue lines within 250 ms of their true start, and the column after it is the same with 500 ms. Error columns are medians over files. The tolerance columns pool all dialogue lines. A tool that reports no outcome counts as having left a file alone when no line moved more than 100 ms.

## Overall

| System | Runs | File success | At 500 ms | Median error (ms) | 90th pct error (ms) | Within 100 ms | Within 250 ms | Within 500 ms | Decisions right | Failed | Mean time (s) | Cost (USD) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| agent-haiku | 535 | 89% | 96% | 3 | 7 | 89% | 95% | 97% | 82% | 0 | 3.8 | 0.2863 |
| alass | 535 | 62% | 65% | 50 | 71 | 83% | 90% | 92% | 12% | 0 | 1.2 | 0.0000 |
| ffsubsync | 535 | 39% | 45% | 98 | 3,441 | 65% | 70% | 72% | 11% | 7 | 2.0 | 0.0000 |
| ffsubsync-split | 535 | 54% | 58% | 70 | 90 | 76% | 79% | 80% | 13% | 7 | 2.9 | 0.0000 |
| input | 535 | 7% | 10% | 10,000 | 20,000 | 12% | 15% | 16% | 29% | 0 | 0.0 | 0.0000 |
| oracle | 528 | 100% | 100% | 0 | 0 | 100% | 100% | 100% | 100% | 0 | 0.0 | 0.0000 |
| pipeline | 535 | 85% | 92% | 3 | 8 | 89% | 95% | 97% | 62% | 0 | 0.9 | 0.0740 |

## By distortion

| System | Distortion | Runs | File success | At 500 ms | Median error (ms) | 90th pct error (ms) | Within 100 ms | Within 250 ms | Within 500 ms | Decisions right | Failed | Mean time (s) | Cost (USD) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| agent-haiku | none | 16 | 100% | 100% | 0 | 0 | 100% | 100% | 100% | 100% | 0 | 0.1 | 0.0000 |
| agent-haiku | constant | 80 | 100% | 100% | 2 | 2 | 93% | 100% | 100% | n/a | 0 | 0.1 | 0.0000 |
| agent-haiku | rate | 48 | 100% | 100% | 2 | 2 | 93% | 100% | 100% | n/a | 0 | 0.1 | 0.0000 |
| agent-haiku | blocks | 80 | 92% | 98% | 0 | 3 | 99% | 100% | 100% | n/a | 0 | 2.7 | 0.0280 |
| agent-haiku | mixed | 32 | 81% | 94% | 3 | 8 | 94% | 99% | 100% | n/a | 0 | 3.6 | 0.0149 |
| agent-haiku | noise | 48 | 100% | 100% | 2 | 2 | 93% | 100% | 100% | 100% | 0 | 4.4 | 0.0239 |
| agent-haiku | wrong_content | 7 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 71% | 0 | 5.7 | 0.0060 |
| agent-haiku | loose_reference | 30 | 100% | 100% | 10 | 13 | 100% | 100% | 100% | 100% | 0 | 1.3 | 0.0043 |
| agent-haiku | cold_open | 33 | 85% | 91% | 21 | 38 | 95% | 99% | 100% | n/a | 0 | 4.6 | 0.0175 |
| agent-haiku | caption_drift | 21 | 0% | 95% | 181 | 319 | 17% | 65% | 100% | n/a | 0 | 1.7 | 0.0040 |
| agent-haiku | pack | 21 | 62% | 62% | 240 | 240 | 33% | 41% | 41% | 29% | 0 | 31.0 | 0.1039 |
| agent-haiku | mid_scene | 21 | 76% | 86% | 15 | 40 | 92% | 95% | 100% | n/a | 0 | 3.6 | 0.0092 |
| agent-haiku | next_episode | 14 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 57% | 0 | 12.1 | 0.0274 |
| agent-haiku | signs_reference | 15 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 100% | 0 | 3.4 | 0.0056 |
| agent-haiku | karaoke | 27 | 96% | 100% | 24 | 40 | 98% | 100% | 100% | n/a | 0 | 2.2 | 0.0058 |
| agent-haiku | captions | 14 | 86% | 100% | 20 | 26 | 92% | 100% | 100% | n/a | 0 | 2.1 | 0.0031 |
| agent-haiku | bilingual | 21 | 86% | 100% | 24 | 40 | 95% | 99% | 100% | n/a | 0 | 3.8 | 0.0104 |
| agent-haiku | odd_times | 7 | 100% | 100% | 30 | 30 | 93% | 100% | 100% | n/a | 0 | 18.5 | 0.0223 |
| alass | none | 16 | 62% | 62% | 34 | 34 | 89% | 90% | 90% | 62% | 0 | 1.4 | 0.0000 |
| alass | constant | 80 | 62% | 62% | 32 | 32 | 89% | 90% | 90% | n/a | 0 | 1.5 | 0.0000 |
| alass | rate | 48 | 62% | 62% | 34 | 35 | 89% | 90% | 90% | n/a | 0 | 1.6 | 0.0000 |
| alass | blocks | 80 | 57% | 60% | 39 | 62 | 86% | 88% | 89% | n/a | 0 | 1.5 | 0.0000 |
| alass | mixed | 32 | 59% | 59% | 45 | 68 | 89% | 90% | 90% | n/a | 0 | 1.5 | 0.0000 |
| alass | noise | 48 | 62% | 62% | 30 | 33 | 89% | 90% | 90% | 0% | 0 | 1.4 | 0.0000 |
| alass | wrong_content | 7 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0% | 0 | 1.2 | 0.0000 |
| alass | loose_reference | 30 | 100% | 100% | 52 | 66 | 88% | 100% | 100% | 100% | 0 | 0.1 | 0.0000 |
| alass | cold_open | 33 | 67% | 67% | 64 | 101 | 70% | 94% | 94% | n/a | 0 | 1.0 | 0.0000 |
| alass | caption_drift | 21 | 33% | 71% | 139 | 271 | 49% | 79% | 93% | n/a | 0 | 1.3 | 0.0000 |
| alass | pack | 21 | 67% | 67% | 60 | 60 | 93% | 93% | 93% | 0% | 0 | 1.0 | 0.0000 |
| alass | mid_scene | 21 | 52% | 62% | 94 | 170 | 68% | 87% | 92% | n/a | 0 | 1.1 | 0.0000 |
| alass | next_episode | 14 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0% | 0 | 1.1 | 0.0000 |
| alass | signs_reference | 15 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0% | 0 | 0.1 | 0.0000 |
| alass | karaoke | 27 | 52% | 56% | 90 | 200 | 71% | 87% | 89% | n/a | 0 | 0.5 | 0.0000 |
| alass | captions | 14 | 64% | 71% | 76 | 100 | 90% | 92% | 92% | n/a | 0 | 1.2 | 0.0000 |
| alass | bilingual | 21 | 62% | 67% | 70 | 174 | 71% | 91% | 93% | n/a | 0 | 1.1 | 0.0000 |
| alass | odd_times | 7 | 71% | 71% | 59 | 95 | 92% | 93% | 93% | n/a | 0 | 1.4 | 0.0000 |
| ffsubsync | none | 16 | 62% | 81% | 35 | 35 | 93% | 97% | 97% | 62% | 0 | 2.4 | 0.0000 |
| ffsubsync | constant | 80 | 70% | 78% | 31 | 31 | 93% | 95% | 96% | n/a | 0 | 2.3 | 0.0000 |
| ffsubsync | rate | 48 | 69% | 81% | 31 | 32 | 94% | 97% | 97% | n/a | 0 | 2.1 | 0.0000 |
| ffsubsync | blocks | 80 | 0% | 0% | 43,090 | 124,762 | 34% | 35% | 35% | n/a | 0 | 2.2 | 0.0000 |
| ffsubsync | mixed | 32 | 0% | 0% | 52,659 | 112,933 | 22% | 23% | 23% | n/a | 0 | 2.3 | 0.0000 |
| ffsubsync | noise | 48 | 75% | 81% | 31 | 35 | 96% | 97% | 97% | 0% | 0 | 2.2 | 0.0000 |
| ffsubsync | wrong_content | 7 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0% | 0 | 2.0 | 0.0000 |
| ffsubsync | loose_reference | 30 | 67% | 67% | 74 | 80 | 80% | 81% | 81% | 80% | 0 | 0.8 | 0.0000 |
| ffsubsync | cold_open | 33 | 42% | 42% | 70 | 80 | 77% | 84% | 85% | n/a | 0 | 1.8 | 0.0000 |
| ffsubsync | caption_drift | 21 | 10% | 81% | 160 | 330 | 25% | 74% | 96% | n/a | 0 | 2.1 | 0.0000 |
| ffsubsync | pack | 21 | 29% | 29% | 464,321 | 476,830 | 33% | 33% | 33% | 0% | 0 | 2.0 | 0.0000 |
| ffsubsync | mid_scene | 21 | 0% | 0% | 379 | 3,533 | 33% | 43% | 55% | n/a | 0 | 1.8 | 0.0000 |
| ffsubsync | next_episode | 14 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0% | 0 | 1.9 | 0.0000 |
| ffsubsync | signs_reference | 15 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0% | 0 | 0.7 | 0.0000 |
| ffsubsync | karaoke | 27 | 19% | 19% | 130 | 24,770 | 56% | 60% | 60% | n/a | 0 | 1.3 | 0.0000 |
| ffsubsync | captions | 14 | 43% | 43% | 70 | 75 | 96% | 96% | 96% | n/a | 0 | 1.9 | 0.0000 |
| ffsubsync | bilingual | 21 | 29% | 29% | 965 | 29,830 | 52% | 57% | 57% | n/a | 0 | 1.9 | 0.0000 |
| ffsubsync | odd_times | 7 | 0% | 0% | n/a | n/a | 0% | 0% | 0% | n/a | 7 | 1.6 | 0.0000 |
| ffsubsync-split | none | 16 | 81% | 81% | 35 | 35 | 97% | 97% | 97% | 81% | 0 | 2.9 | 0.0000 |
| ffsubsync-split | constant | 80 | 81% | 81% | 31 | 31 | 97% | 98% | 98% | n/a | 0 | 2.8 | 0.0000 |
| ffsubsync-split | rate | 48 | 75% | 75% | 35 | 36 | 95% | 95% | 95% | n/a | 0 | 2.8 | 0.0000 |
| ffsubsync-split | blocks | 80 | 9% | 9% | 42,922 | 133,165 | 44% | 44% | 45% | n/a | 0 | 2.9 | 0.0000 |
| ffsubsync-split | mixed | 32 | 3% | 6% | 40,780 | 135,869 | 26% | 27% | 28% | n/a | 0 | 2.6 | 0.0000 |
| ffsubsync-split | noise | 48 | 75% | 81% | 40 | 40 | 96% | 97% | 97% | 0% | 0 | 2.8 | 0.0000 |
| ffsubsync-split | wrong_content | 7 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0% | 0 | 2.8 | 0.0000 |
| ffsubsync-split | loose_reference | 30 | 83% | 83% | 64 | 74 | 94% | 97% | 97% | 80% | 0 | 1.6 | 0.0000 |
| ffsubsync-split | cold_open | 33 | 55% | 55% | 80 | 80 | 84% | 85% | 86% | n/a | 0 | 2.3 | 0.0000 |
| ffsubsync-split | caption_drift | 21 | 24% | 86% | 106 | 292 | 56% | 87% | 98% | n/a | 0 | 2.7 | 0.0000 |
| ffsubsync-split | pack | 21 | 24% | 29% | 322,450 | 339,654 | 32% | 33% | 33% | 0% | 0 | 3.5 | 0.0000 |
| ffsubsync-split | mid_scene | 21 | 67% | 71% | 70 | 80 | 93% | 95% | 95% | n/a | 0 | 2.5 | 0.0000 |
| ffsubsync-split | next_episode | 14 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0% | 0 | 2.4 | 0.0000 |
| ffsubsync-split | signs_reference | 15 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0% | 0 | 1.7 | 0.0000 |
| ffsubsync-split | karaoke | 27 | 67% | 67% | 100 | 100 | 76% | 79% | 80% | n/a | 0 | 6.8 | 0.0000 |
| ffsubsync-split | captions | 14 | 71% | 71% | 70 | 70 | 97% | 97% | 97% | n/a | 0 | 2.7 | 0.0000 |
| ffsubsync-split | bilingual | 21 | 81% | 81% | 80 | 93 | 85% | 95% | 95% | n/a | 0 | 3.2 | 0.0000 |
| ffsubsync-split | odd_times | 7 | 0% | 0% | n/a | n/a | 0% | 0% | 0% | n/a | 7 | 1.2 | 0.0000 |
| input | none | 16 | 100% | 100% | 0 | 0 | 100% | 100% | 100% | 100% | 0 | 0.0 | 0.0000 |
| input | constant | 80 | 20% | 20% | 8,000 | 8,000 | 0% | 20% | 20% | n/a | 0 | 0.0 | 0.0000 |
| input | rate | 48 | 0% | 0% | 13,086 | 21,482 | 0% | 0% | 1% | n/a | 0 | 0.0 | 0.0000 |
| input | blocks | 80 | 0% | 0% | 59,352 | 126,436 | 33% | 33% | 33% | n/a | 0 | 0.0 | 0.0000 |
| input | mixed | 32 | 0% | 0% | 80,172 | 153,701 | 0% | 0% | 0% | n/a | 0 | 0.0 | 0.0000 |
| input | noise | 48 | 0% | 0% | 2,700 | 2,700 | 0% | 0% | 0% | 0% | 0 | 0.0 | 0.0000 |
| input | wrong_content | 7 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0% | 0 | 0.0 | 0.0000 |
| input | loose_reference | 30 | 17% | 17% | 8,463 | 14,450 | 22% | 22% | 22% | 100% | 0 | 0.0 | 0.0000 |
| input | cold_open | 33 | 0% | 0% | 10,000 | 10,000 | 0% | 0% | 1% | n/a | 0 | 0.0 | 0.0000 |
| input | caption_drift | 21 | 0% | 67% | 0 | 500 | 40% | 40% | 67% | n/a | 0 | 0.0 | 0.0000 |
| input | pack | 21 | 0% | 0% | 380,000 | 380,000 | 0% | 0% | 0% | 0% | 0 | 0.0 | 0.0000 |
| input | mid_scene | 21 | 0% | 0% | 1,700 | 3,000 | 22% | 22% | 22% | n/a | 0 | 0.0 | 0.0000 |
| input | next_episode | 14 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0% | 0 | 0.0 | 0.0000 |
| input | signs_reference | 15 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 100% | 0 | 0.0 | 0.0000 |
| input | karaoke | 27 | 0% | 0% | 14,100 | 26,100 | 0% | 0% | 0% | n/a | 0 | 0.0 | 0.0000 |
| input | captions | 14 | 0% | 0% | 11,800 | 11,800 | 0% | 0% | 0% | n/a | 0 | 0.0 | 0.0000 |
| input | bilingual | 21 | 0% | 0% | 22,400 | 31,400 | 0% | 0% | 0% | n/a | 0 | 0.0 | 0.0000 |
| input | odd_times | 7 | 0% | 0% | 1,500 | 1,500 | 0% | 0% | 0% | n/a | 0 | 0.0 | 0.0000 |
| oracle | none | 16 | 100% | 100% | 0 | 0 | 100% | 100% | 100% | 100% | 0 | 0.0 | 0.0000 |
| oracle | constant | 80 | 100% | 100% | 0 | 0 | 100% | 100% | 100% | n/a | 0 | 0.0 | 0.0000 |
| oracle | rate | 48 | 100% | 100% | 0 | 0 | 100% | 100% | 100% | n/a | 0 | 0.0 | 0.0000 |
| oracle | blocks | 80 | 100% | 100% | 0 | 0 | 100% | 100% | 100% | n/a | 0 | 0.0 | 0.0000 |
| oracle | mixed | 32 | 100% | 100% | 0 | 0 | 100% | 100% | 100% | n/a | 0 | 0.0 | 0.0000 |
| oracle | noise | 48 | 100% | 100% | 0 | 0 | 100% | 100% | 100% | 100% | 0 | 0.0 | 0.0000 |
| oracle | wrong_content | 7 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 100% | 0 | 0.0 | 0.0000 |
| oracle | loose_reference | 30 | 100% | 100% | 0 | 0 | 100% | 100% | 100% | 100% | 0 | 0.0 | 0.0000 |
| oracle | cold_open | 33 | 100% | 100% | 0 | 0 | 100% | 100% | 100% | n/a | 0 | 0.0 | 0.0000 |
| oracle | caption_drift | 21 | 100% | 100% | 0 | 0 | 100% | 100% | 100% | n/a | 0 | 0.0 | 0.0000 |
| oracle | pack | 21 | 100% | 100% | 0 | 0 | 100% | 100% | 100% | 100% | 0 | 0.0 | 0.0000 |
| oracle | mid_scene | 21 | 100% | 100% | 0 | 0 | 100% | 100% | 100% | n/a | 0 | 0.0 | 0.0000 |
| oracle | next_episode | 14 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 100% | 0 | 0.0 | 0.0000 |
| oracle | signs_reference | 15 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 100% | 0 | 0.0 | 0.0000 |
| oracle | karaoke | 27 | 100% | 100% | 0 | 0 | 100% | 100% | 100% | n/a | 0 | 0.1 | 0.0000 |
| oracle | captions | 14 | 100% | 100% | 0 | 0 | 100% | 100% | 100% | n/a | 0 | 0.0 | 0.0000 |
| oracle | bilingual | 21 | 100% | 100% | 0 | 0 | 100% | 100% | 100% | n/a | 0 | 0.0 | 0.0000 |
| pipeline | none | 16 | 100% | 100% | 0 | 0 | 100% | 100% | 100% | 100% | 0 | 0.1 | 0.0000 |
| pipeline | constant | 80 | 100% | 100% | 2 | 2 | 93% | 100% | 100% | n/a | 0 | 0.2 | 0.0000 |
| pipeline | rate | 48 | 100% | 100% | 2 | 2 | 93% | 100% | 100% | n/a | 0 | 0.4 | 0.0000 |
| pipeline | blocks | 80 | 80% | 82% | 0 | 6 | 99% | 99% | 100% | n/a | 0 | 1.8 | 0.0640 |
| pipeline | mixed | 32 | 72% | 81% | 3 | 12 | 94% | 98% | 99% | n/a | 0 | 1.1 | 0.0000 |
| pipeline | noise | 48 | 100% | 100% | 2 | 2 | 93% | 100% | 100% | 38% | 0 | 0.2 | 0.0000 |
| pipeline | wrong_content | 7 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 100% | 0 | 2.6 | 0.0082 |
| pipeline | loose_reference | 30 | 100% | 100% | 10 | 13 | 100% | 100% | 100% | 100% | 0 | 0.6 | 0.0000 |
| pipeline | cold_open | 33 | 82% | 88% | 21 | 38 | 95% | 99% | 100% | n/a | 0 | 0.9 | 0.0001 |
| pipeline | caption_drift | 21 | 0% | 95% | 181 | 319 | 17% | 65% | 100% | n/a | 0 | 1.0 | 0.0001 |
| pipeline | pack | 21 | 52% | 52% | 240 | 240 | 31% | 38% | 38% | 14% | 0 | 0.9 | 0.0000 |
| pipeline | mid_scene | 21 | 76% | 90% | 15 | 40 | 92% | 95% | 100% | n/a | 0 | 1.0 | 0.0000 |
| pipeline | next_episode | 14 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 100% | 0 | 1.5 | 0.0001 |
| pipeline | signs_reference | 15 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 100% | 0 | 0.1 | 0.0000 |
| pipeline | karaoke | 27 | 96% | 100% | 24 | 40 | 98% | 100% | 100% | n/a | 0 | 1.5 | 0.0001 |
| pipeline | captions | 14 | 86% | 100% | 20 | 26 | 92% | 100% | 100% | n/a | 0 | 2.3 | 0.0002 |
| pipeline | bilingual | 21 | 86% | 100% | 23 | 40 | 95% | 99% | 100% | n/a | 0 | 2.2 | 0.0011 |
| pipeline | odd_times | 7 | 86% | 86% | 30 | 30 | 91% | 98% | 98% | n/a | 0 | 1.0 | 0.0000 |

## By reference

| System | Reference | Runs | File success | At 500 ms | Median error (ms) | 90th pct error (ms) | Within 100 ms | Within 250 ms | Within 500 ms | Decisions right | Failed | Mean time (s) | Cost (USD) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| agent-haiku | audio | 122 | 74% | 92% | 123 | 202 | 27% | 94% | 99% | 89% | 0 | 3.7 | 0.0597 |
| agent-haiku | subtitle | 375 | 92% | 97% | 2 | 3 | 93% | 95% | 97% | 78% | 0 | 4.2 | 0.2215 |
| agent-haiku | both | 38 | 100% | 100% | 0 | 0 | 100% | 100% | 100% | 100% | 0 | 1.3 | 0.0051 |
| alass | audio | 122 | 0% | 0% | 55,171 | 187,458 | 1% | 4% | 7% | 0% | 0 | 3.8 | 0.0000 |
| alass | subtitle | 375 | 90% | 94% | 29 | 36 | 90% | 98% | 99% | 17% | 0 | 0.1 | 0.0000 |
| alass | both | 38 | 0% | 0% | 61,656 | 185,581 | 1% | 4% | 7% | 0% | 0 | 3.7 | 0.0000 |
| ffsubsync | audio | 122 | 11% | 24% | 59,980 | 81,488 | 34% | 46% | 48% | 0% | 2 | 4.8 | 0.0000 |
| ffsubsync | subtitle | 375 | 51% | 54% | 55 | 90 | 68% | 72% | 74% | 16% | 5 | 0.8 | 0.0000 |
| ffsubsync | both | 38 | 13% | 29% | 61,518 | 81,852 | 36% | 48% | 50% | 0% | 0 | 4.8 | 0.0000 |
| ffsubsync-split | audio | 122 | 21% | 25% | 1,116 | 56,530 | 41% | 49% | 50% | 7% | 2 | 5.0 | 0.0000 |
| ffsubsync-split | subtitle | 375 | 68% | 72% | 40 | 60 | 79% | 82% | 83% | 16% | 5 | 2.0 | 0.0000 |
| ffsubsync-split | both | 38 | 26% | 29% | 1,112 | 56,530 | 47% | 51% | 52% | 12% | 0 | 4.8 | 0.0000 |
| input | audio | 122 | 7% | 10% | 8,743 | 19,550 | 13% | 17% | 19% | 14% | 0 | 0.0 | 0.0000 |
| input | subtitle | 375 | 7% | 10% | 10,000 | 20,000 | 12% | 15% | 16% | 33% | 0 | 0.0 | 0.0000 |
| input | both | 38 | 11% | 11% | 10,853 | 20,730 | 14% | 20% | 20% | 25% | 0 | 0.0 | 0.0000 |
| oracle | audio | 120 | 100% | 100% | 0 | 0 | 100% | 100% | 100% | 100% | 0 | 0.0 | 0.0000 |
| oracle | subtitle | 370 | 100% | 100% | 0 | 0 | 100% | 100% | 100% | 100% | 0 | 0.0 | 0.0000 |
| oracle | both | 38 | 100% | 100% | 0 | 0 | 100% | 100% | 100% | 100% | 0 | 0.0 | 0.0000 |
| pipeline | audio | 122 | 72% | 88% | 123 | 220 | 27% | 93% | 99% | 89% | 0 | 1.2 | 0.0722 |
| pipeline | subtitle | 375 | 89% | 94% | 2 | 4 | 93% | 95% | 97% | 50% | 0 | 0.9 | 0.0018 |
| pipeline | both | 38 | 92% | 92% | 0 | 0 | 99% | 99% | 99% | 100% | 0 | 0.4 | 0.0000 |

## What the model was asked

An agent system calls its model only for files whose draft plan leaves something open. Turns, time and cost are means over the runs in which it was called. A plan is kept back when the model's changes put fewer certain lines in place than the plan it started from.

| System | Model | Runs | Model asked | Turns when asked | Seconds when asked | Cost when asked (USD) | Cost per run (USD) | Plans changed | Kept back | Not finished |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| agent-haiku | claude-haiku-5-5 | 535 | 195 (36%) | 2.4 | 10.3 | 0.0015 | 0.00054 | 150 | 0 | 0 |
