# Seed material

Two short open films with real speech, music and effects, and three episodes
of a series that was made up for the benchmark.

| Seed | Length | Lines | Sound | Where it is from |
| --- | --- | --- | --- | --- |
| Tears of Steel | 12 min | 76 | English | CC BY 3.0, (CC) Blender Foundation, mango.blender.org |
| Sintel | 15 min | 26 | English | CC BY 3.0, (CC) Blender Foundation, durian.blender.org |
| The Rooftop Tomato Club, episodes 1 to 3 | 21 to 22 min each | about 395 each | none | Written for this benchmark by a language model |

## The films

Both films have English audio and subtitles in English and Japanese that share
their timing. The subtitle files are unchanged copies from download.blender.org
and durian.blender.org. The audio is not stored in the repository. `retimer
bench fetch-seeds` downloads each film and keeps only a mono Opus track, which
is the form a client would upload.

## The series

`rooftop-tomato-club` holds a series that does not exist: three consecutive
episodes about a school garden club, in Japanese and English. It is there for
what the films lack, which is the length and density of an episode, a next
episode with the same people in it, and two languages that cut their sentences
differently.

The lines were written by a language model and are not taken from any show.
The times were made by rule. There is no sound. `series.json` is the script:
who is in it, the narration that opens every episode, and every scene as the
model wrote it. The subtitle files of the episodes are made from it by
`retimer bench time-series rooftop-tomato-club`, and `retimer bench
write-series` has the model write another series.
[The benchmark's README](../README.md#the-made-up-series) says how both work.

## Adding material

Add an entry to `seeds.json` with the subtitles by language and, when there is
audio, a file name under the media directory. Put audio you already have into
`data/bench/media/` under that name and leave `source_url` out. Episodes of one
series name it in `series`: a case that needs the wrong episode then takes the
next one of that series.

A naturally mistimed file does not need a seed. Write a case directory by hand:
the mistimed subtitle, and a `case.json` whose `truth` gives the true start of
each line.
