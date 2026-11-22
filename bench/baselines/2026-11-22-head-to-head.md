# The retimer, alass and ffsubsync on all 588 cases, 2026-11-22

One run of every system over every case of [bench/README.md](../README.md), on
the code that is released as 0.1.0. It replaces the two runs that the earlier
comparisons were split over ([535 cases](2026-11-18-real-file-problems.md) and
[the 53 with several reference tracks](2026-11-18-several-references.md)), and
it is the first with the changes for episode packs, refusals and stretches that
drift.

- `agent-haiku`: the retimer as it is delivered, with Claude Haiku 5.5 at
  medium effort, called through OpenRouter.
- `pipeline`: the same without the model. A diagnostic, nothing a client gets.
- `alass` 2.0.0 and `ffsubsync` 0.5.1 with their defaults, and
  `ffsubsync-split` with `--split-penalty`. They take one reference: where a
  case has a subtitle and the audio they are given the subtitle, which is the
  better input for both here.
- `input`, the file as it was sent, and `oracle`, which applies the plan a case
  was made with (581 of 581 it can run).

A case is **solved** when 95% of its dialogue lines are within 250 ms of their
true start, or, where nothing can be timed, when the file is refused or left as
it was, as the case asks.

## In short

| What the case asks | Cases | Retimer | alass | ffsubsync, split | ffsubsync | Untouched |
| --- | --- | --- | --- | --- | --- | --- |
| Timing against a reference subtitle | 380 | **373 (98%)** | 323 (85%) | 251 (66%) | 186 (49%) | 30 (8%) |
| Timing against the audio alone | 116 | **86 (74%)** | 0 (0%) | 24 (21%) | 13 (11%) | 8 (7%) |
| Timing against a subtitle and the audio | 46 | **46 (100%)** | 40 (87%) | 29 (63%) | 26 (57%) | 4 (9%) |
| Refuse the wrong file, or leave it alone | 46 | **46 (100%)** | 0 (0%) | 0 (0%) | 0 (0%) | 20 (43%) |
| All | 588 | **551 (94%)** | 363 (62%) | 304 (52%) | 225 (38%) | 62 (11%) |

- **Against a reference subtitle the retimer solves 373 of 380, alass 323.**
  alass solves two cases the retimer does not, both stretches of drifting
  captions. The retimer solves 52 that alass does not: episode packs, files
  with several reference tracks, karaoke scripts, edits in the middle of a
  scene, cold opens.
- **Against audio** alass solves nothing and ffsubsync a fifth with its split
  option. The retimer solves three quarters. What it misses there is mostly
  breaks that a transcript times too loosely for the 250 ms bar: at 500 ms it
  solves 92%. On these two films alass misses every case by more than 10
  seconds (by a median of 40 s on one film and 4 minutes on the other), also
  a file that is only 1.5 s early. On real episodes it does better against
  audio, as the README of the repository shows.
- **Where a case has a subtitle and the audio**, the retimer uses both and
  solves all 46. alass, given the subtitle, solves 40. The 6 it misses have
  several reference tracks, and the one a client would send alone is a track
  of signs or another episode.
- **No other tool refuses.** In all 46 cases that ask for it (another film,
  the next episode of a series, a reference of signs and songs only) alass
  and ffsubsync move the file anyway.
- **What it cost:** the model was asked about 218 files (37%), for 0.28 USD
  over the whole run, 0.13 cents a file where it was asked. A file took 3.4 s
  on average, 8.7 s where the model was asked.

Over all 588 cases alass solves 2 that the retimer does not, ffsubsync with its
split option 3, ffsubsync 0. The retimer solves 190 that alass does not.

## By family

| Family | Cases | `agent-haiku` | `pipeline` | `alass` | `ffsubsync-split` | `ffsubsync` | `input` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `none` | 16 | 16 | 16 | 12 | 14 | 12 | 16 |
| `constant` | 80 | 80 | 80 | 60 | 70 | 64 | 16 |
| `rate` | 48 | 48 | 48 | 36 | 40 | 38 | 0 |
| `blocks` | 80 | 74 | 64 | 56 | 9 | 0 | 0 |
| `mixed` | 32 | 26 | 23 | 23 | 2 | 0 | 0 |
| `noise` | 48 | 48 | 48 | 36 | 40 | 40 | 0 |
| `wrong_content` | 7 | 7 | 7 | 0 | 0 | 0 | 0 |
| `loose_reference` | 30 | 30 | 30 | 30 | 25 | 20 | 5 |
| `cold_open` | 33 | 28 | 27 | 22 | 18 | 14 | 0 |
| `caption_drift` | 21 | 12 | 0 | 7 | 5 | 2 | 0 |
| `pack` | 21 | 21 | 11 | 14 | 5 | 6 | 0 |
| `mid_scene` | 21 | 16 | 16 | 11 | 14 | 0 | 0 |
| `next_episode` | 14 | 14 | 14 | 0 | 0 | 0 | 0 |
| `signs_reference` | 15 | 15 | 15 | 0 | 0 | 0 | 15 |
| `several_references` | 53 | 53 | 52 | 15 | 17 | 12 | 10 |
| `karaoke` | 27 | 26 | 26 | 14 | 18 | 5 | 0 |
| `captions` | 14 | 12 | 12 | 9 | 10 | 6 | 0 |
| `bilingual` | 21 | 18 | 18 | 13 | 17 | 6 | 0 |
| `odd_times` | 7 | 7 | 6 | 5 | 0 | 0 | 0 |

The plain families (`none` to `noise`) hold audio cases as well, which is where
alass and ffsubsync lose most of theirs there. What each family is, and which
real files it was made after, is in [bench/README.md](../README.md).

Since the runs of 2026-11-18 the retimer gained the packs (13 of 21 before,
all 21 now, with the other episodes taken out), the next episode of a series
(8 of 14, now 14) and drifting captions (0 of 21, now 12). Everything else is
where it was within a case or two. alass and ffsubsync give the same results as
in those runs, case for case, except in the 46 cases with a subtitle and the
audio, where they now get the subtitle: the cases are generated the same, and
the tools do not change from run to run.

## Limits

- **Home ground.** The families after real files are the problems this project
  found and then worked on. alass and ffsubsync ran with their defaults, not
  tuned to these cases.
- **Every audio case is from two English films**, Tears of Steel and Sintel.
  The retimer was never measured here against Japanese audio.
- **One run.** From run to run the model settles two or three audio cases
  differently (550 of 588 on 2026-11-18, 551 now).
- ffsubsync ended in an error on 7 cases, the files with times that only
  players read. They count as not solved.
- The harness first gave alass and ffsubsync the audio where a case has both
  (alass solved 0 of those 46, ffsubsync 5, with its split option 13). It now
  gives them the subtitle, and the three were run again on those 46 cases the
  same evening. Those records replace the first ones.
- On real files, where the truth is not known, the retimer was measured
  separately: see the README of the repository.

## How to run it again

```
retimer bench fetch-seeds
retimer bench generate
retimer bench run --systems input,oracle,ffsubsync,ffsubsync-split,alass,pipeline,agent-haiku --workers 4
```

With `OPENROUTER_API_KEY` set, `alass-cli` and `ffsubsync` on the `PATH`. It
took 28 minutes on 4 cores, after one audio case of each film had been run on
its own so that the transcripts were in the cache before the workers started.
