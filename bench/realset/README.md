# The real-file set

The benchmark knows the true start of every line, because it makes its cases,
also those that replicate what real files do. What it cannot show is how a
system does on the files people really sync: fansub scripts with karaoke,
captions taken off a broadcast, files that hold four episodes, a subtitle for
another cut of the same episode. The real-file set is for that. It is built
from your own videos and from Jimaku, it is kept, and every system is run over
it and judged by the same measures.

A set names the releases it was built from, so it lives outside the
repository. Only the commands are here.

## What a set holds

```
manifest.json     videos, tracks, targets and pairs
overrides.json    what was found out about the set by hand
tracks/           every text subtitle track of every video, packed with xz
audio/            one mono Opus file per video, 24 kbit/s, as a client uploads it
targets/          the subtitle files from Jimaku, named by their content
jimaku/           what Jimaku listed, so that a rebuild sees the same files
cache/            the embeddings of every line (see below)
tuning.json       the settings chosen for the tools
runs/NAME/        results.jsonl, outputs/, judgments.jsonl, tracks.jsonl and report.md of a run
.plain/           unpacked copies, made when needed, safe to delete
```

Image subtitles are left out: PGS tracks of the videos and the archives on
Jimaku, which hold image subtitles.

## Tools

```
bench/realset/setup-tools.sh TOOLS_DIR
```

puts a virtual environment with this repository, ffsubsync and
sentence-transformers, and the alass binary, into one directory and nothing
anywhere else. Put `TOOLS_DIR/venv/bin`, `TOOLS_DIR/bin` and an `ffmpeg` with
libopus on the `PATH` for the commands below.

## Build

```
retimer realset build SET --videos VIDEOS --jimaku-key-file KEY_FILE
```

The videos are only read. For each one the command copies out every text
subtitle track and encodes one audio track, the Japanese one where the tags
say which that is. It then asks Jimaku for the files of each series and
downloads the text subtitles. A build that was cut off is continued by running
it again, and a set is brought up to date the same way. Without a key it
rebuilds the manifest from what is already there.

A folder of videos is a series. Its name begins with the AniList id
(`12345 - Some Show`), or `overrides.json` says which id it is.

**Roles of tracks.** A track's role comes from its lines, not from its title,
because releases mislabel their tracks: `main` is the English track with the
most lines that read like speech, `full` another such track, `foreign` a full
track in another language, `signs` a track of signs and songs. Captions for
the hard of hearing, known by their lines that only describe a sound, become
the main track only when there is no other: in a release with two audio
languages they follow the English voices.

**Which file is for which video.** The episode is read off the name. Files
that are named alike were numbered alike: some count from the first episode of
the series, some from the first of the season, so for each group of names both
readings are tried and the one that finds more videos wins. A lone special
among the regular episodes of a folder is episode 0. A folder with one video
is a film, and every file of its entry is for it. A file that covers several
episodes is paired with the first and the last of them.

**Overrides.** What the rules get wrong is corrected in `overrides.json`, and
stays with the set:

```json
{
  "folders": {"Some Show [BD]": {"anilist_id": 12345}},
  "targets": [
    {"entry": 77, "name_contains": ".WEBRip.", "episode_offset": -1,
     "reason": "this source counts the prologue as episode 1"},
    {"entry": 77, "name_contains": "Complete", "skip": "all episodes in one file"}
  ],
  "tracks": [
    {"video_contains": "Some Show - 04", "stream": 5, "role": "wrong_episode",
     "reason": "holds the subtitles of episode 3"}
  ],
  "notes": ["anything else worth knowing"]
}
```

## Pairs

A pair is a target and a track of a video.

| Kind | What | Expected |
| --- | --- | --- |
| `main` | Every target against the main track of its video | `placed`: it ends up in sync |
| `other_track` | Every other track of a video, each with one of the video's targets | `placed` for `full` and `foreign`, `refused` for `signs` and `wrong_episode` |
| `wrong` | One target in ten against the main track of another video: by turns the next episode and an episode of another series | `refused`: the system says so, or leaves the file alone |

One main pair in ten is a tuning pair. Which ones follows from the ids alone,
so it is the same tenth after every rebuild.

## Embeddings

The pipeline compares lines by meaning with `baai/bge-m3`, which the service
gets from a provider. Over a set that would be a million texts, so

```
retimer realset embed SET
```

runs the same model on this machine, on a graphics card when there is one, and
stores every vector in `SET/cache`, at half precision to keep the file small.
A run then reads every embedding from that cache. It asks no provider, costs
nothing, and gives the same result every time. After a change to how lines
are cleaned for matching, run `embed` again: it only adds what is new.

## Systems

| System | What it is |
| --- | --- |
| `retimer` | This project's fixed pipeline against the subtitle track |
| `alass`, `ffsubsync` | The tools with their defaults against the subtitle track |
| `alass-audio`, `ffsubsync-audio` | The tools with their defaults against the audio |
| `...-tuned` | Each of the four with the setting chosen on the tuning pairs |

The agent and the retimer's jobs against audio are not run: they need a model
at a provider, and a run over the set calls none. Against the audio only the
main and the wrong pairs differ from each other, so that is where it tells
something.

```
retimer realset tune SET
```

runs every setting in `retimer.realset.systems.CANDIDATES` on the tuning pairs,
judges the outputs, and writes the best setting per tool and reference to
`tuning.json`. Best is the largest share of verified files, and a tie goes to
the default. The report counts the systems on the other nine tenths.

A tuned run also does what someone who knows the tool would do with a file the
tool cannot read: it saves the target and the reference subtitle again,
without the empty rows inside lines and without the fonts in a track's header
that strict readers stumble over, and tries once more. The run with the
defaults gets every file as it is, and a file it cannot read counts as
failed.

## Run, judge, report

```
retimer realset run SET [--run NAME] [--systems a,b] [--kinds main,wrong] [--again]
retimer realset judge SET [--run NAME]
retimer realset report SET [--run NAME]
retimer realset tidy SET
```

`tidy` packs the outputs of every run with xz and deletes the unpacked
copies. A tidied run can still be judged and continued.

`run` writes each result as it comes. Started again, it only runs what is not
on record, so a system can be added to a run later. With `--again` the named
systems are run once more and their new results take the place of the old
ones, which is how one system of a run is brought up to date with the code. To measure a change to the
pipeline, give the run a new name: `--run my-fix --systems retimer`, then judge
and report it, and hold the report next to the one of the full run.

`judge` holds every output against three witnesses.

- **The window check.** Lines of the target that clearly mean the same as a
  line of the reference track are found once, from the text alone. Every
  system's output is held against them in windows of three minutes. A window
  with at least three such pairs is off when their median error is above
  0.35 s. A file is `verified` when no judged window is off, `off` when one
  is, and `unjudged` without a window to judge. The pairs are found with the
  retimer's own matching, so this check alone cannot clear the retimer.
- **The sound check.** `retimer bench soundcheck`, asked of the video's audio
  about every output: per window the offset between the line starts and where
  voices rise, and how far that reading stands above chance. It knows nothing
  of any subtitle track. It tells whether lines are within about half a second
  of the sound, and does not rank two results that are both that close.
- **Disagreement.** How far each system moved the lines of each window. Where
  two systems differ by more than 0.3 s, at most one of them is right.

For pairs that should be refused the judgment says what the system did:
refused, left the file alone (no line moved more than 0.1 s), or moved it.

The window check takes the main track of a video for the truth. Whether it is
one is asked of the sound as well: `judge` writes the sound check's reading of
every full track of every video to `tracks.jsonl`, and the report lists the
main tracks the sound finds off.

`report` writes `report.md` into the run: the share of verified main pairs by
kind of target file and system, the head to head with the retimer, where the
systems disagree, what each did with other tracks and wrong pairs, and the
clearest disagreements between the retimer and alass as candidates for a look
by a person.
