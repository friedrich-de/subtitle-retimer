# Seed material

Two short open films with real speech, music and effects. They exist to exercise
the benchmark harness until a real corpus is collected. Both have English audio
and subtitles in English and Japanese that share their timing.

| Seed | Length | Dialogue lines | License | Attribution |
| --- | --- | --- | --- | --- |
| Tears of Steel | 12 min | 76 | CC BY 3.0 | (CC) Blender Foundation, mango.blender.org |
| Sintel | 15 min | 26 | CC BY 3.0 | (CC) Blender Foundation, durian.blender.org |

The subtitle files are unchanged copies from download.blender.org and
durian.blender.org. The audio is not stored in the repository. `retimer bench
fetch-seeds` downloads each film and keeps only a mono Opus track, which is the
form a client would upload.

## Adding material

Add an entry to `seeds.json` with the subtitles by language and, when there is
audio, a file name under the media directory. Put audio you already have into
`data/bench/media/` under that name and leave `source_url` out.

A naturally mistimed file does not need a seed. Write a case directory by hand:
the mistimed subtitle, and a `case.json` whose `truth` gives the true start of
each line.
