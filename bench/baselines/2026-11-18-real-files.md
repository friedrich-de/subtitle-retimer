# The retimer, alass and ffsubsync on real files, 2026-11-18

The benchmark's cases are made, so their truth is known. This is the other
half: the same systems on files people really sync, built with the kit of
[realset/README.md](../realset/README.md). The set is one collection of 389
videos of 30 anime series and films with their own subtitle tracks, and 1,131
Japanese subtitle files from Jimaku for them. It names the releases it was
built from, so it is not in the repository.

- **The retimer** as it is delivered, with Claude Haiku 5.5, after the fix for
  episode packs.
- **alass** 2.0.0 and **ffsubsync** 0.5.1, each with the settings that did
  best on the tenth of the pairs kept for tuning, and with their defaults.

Nobody timed these files by hand, so two automatic judges stand in for the
truth ([how they work](../realset/README.md#run-judge-report)):

- **The window check:** the lines that clearly mean the same are within
  0.35 s of the video's own track in every three-minute window. It finds those
  lines with the retimer's own matching, so it favours the retimer.
- **The sound check:** the lines start within about half a second of the
  voices. It knows nothing of any subtitle track, and it cannot rank two
  results that are both that close.

## Against the video's subtitle track

The 1,027 pairs of a Jimaku file and the main track of its video that no
setting was tuned on.

| System | Window check | Sound check |
| --- | --- | --- |
| **Retimer** | **95%** (972) | **97%** (1,000) |
| alass, tuned | 80% | 93% |
| alass, defaults | 74% | 88% |
| ffsubsync, tuned | 68% | 83% |
| ffsubsync, defaults | 66% | 79% |
| The file as it came | 38% | 42% |

alass cannot read 24 of the files and ffsubsync 19. They count as not
verified. The retimer is far ahead on captions taken off TV broadcasts and on
files of several episodes, and level with alass on captions of streaming
sites.

## Against the audio

The retimer and the tools against the video's audio, on a sample of 90 pairs
from 29 videos of 28 series (transcripts cost money, so not every video was
transcribed).

| System | Window check | Sound check |
| --- | --- | --- |
| **Retimer** | **86%** | **97%** |
| alass, tuned | 59% | 74% |
| ffsubsync, tuned | 67% | 76% |

The window check reads lower for the retimer here partly because it trusts the
video's track: where the track itself is off the voices, the job against the
audio follows the voices.

## Files that must not be moved

- **The wrong file.** Of 125 pairs of a file and another episode or another
  series, the retimer refused 123. The other 2 were files of several episodes
  that did hold the episode, and it cut them to it. alass and ffsubsync moved
  every one they could read.
- **A track of signs and songs only as the reference.** Of 211 such pairs the
  retimer refused 188 and left 20 as they were. alass moved 209.

## Limits

- No truth timed by hand. The window check favours the retimer, the sound
  check is coarse: by the sound check alone the lead over tuned alass is 4
  points.
- One collection, mostly Japanese subtitles for anime. 110 of the 389 videos
  are one long series.
- The embeddings ran locally on bge-m3, not at the provider the service uses.
- The retimer's figures are of the code of the pack fix (PR #21). The
  stretches of PR #22, measured on the same set afterwards, moved 5 pairs up
  and 1 down. The figures on signs tracks are of that later run.
