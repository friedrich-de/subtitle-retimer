# The fixed pipeline on the seed cases, 2026-11-16

The fixed pipeline (`pipeline`) next to ffsubsync 0.5.1 and alass 2.0.0 on the
194 cases generated from the two seed films. The pipeline has no agent: it
transcribes or reads the reference, matches lines by meaning, fits the offsets
and always takes the best-scoring fit. This is the bar the agent has to clear.

The seed set is small: two short films heavy on music and effects, with 76 and
26 dialogue lines. The pipeline's thresholds were set on these same cases, so
the figures are a first reading and not a held-out result.

## What stands out

- The pipeline syncs 86% of the files, the best existing tool 43%. At the wider
  500 ms tolerance it is 89% against 45%.
- Every offset, every frame rate change and every in-sync file is handled
  right, against audio as well as against a subtitle in another language. On
  the Sintel audio, where no existing tool solves a single case, the pipeline
  solves all of those.
- Breaks are where it still fails: 62% of the block cases and 60% of the mixed
  ones. Most of what it misses are blocks of two to four lines, which a 26-line
  film cut in five places is full of, and which leave too little text to
  place. The rest are audio cases at the edge of the strict tolerance, see
  below. Almost all of the lines still land right: 93% within 250 ms on the
  block cases.
- It rejects every wrong reference. No existing tool rejects any.
- Against audio it trims trailing lines that land in silence, and it keeps a
  line wherever the speech model heard anything at all, even something unclear.
  Against a subtitle alone it only trims what also reads like a credit or an
  advertisement, so a spoken outro stays. That is one line too many in each of
  the twelve subtitle-only noise cases and most of what is behind "Decisions
  right" being 66%. Judging such lines is left to the agent.

## How exact "in sync" can be against audio

The truth is the timing a person gave the subtitles. Measured against the sound
itself, the Tears of Steel subtitles appear about a quarter of a second before
the voice sets in and the Sintel subtitles about a quarter of a second after.
The pipeline places lines on the voice, so on audio cases its median error is
about 190 ms, all of it this difference in style, and a file can miss the
strict 250 ms tolerance without being out of sync. At 500 ms the audio cases
go from 78% to 86%. For the same reason a file that is within 350 ms of the
voice counts as in sync and is left alone.

## Time, cost and repeatability

- A first run on a film transcribes its audio: about 20 seconds and one cent
  for a quarter hour (0.0082 USD for Tears of Steel, 0.0099 USD for Sintel).
  The embeddings cost less than a hundredth of a cent per file. Everything
  after that comes from the cache, which is why the time and cost columns
  below show almost nothing for the pipeline: the two films were already
  transcribed when this run was made.
- The speech model does not return the same transcript twice. Run again on a
  fresh transcription of both films, the pipeline synced 85% of the files (89%
  at 500 ms), with all in-sync, wrong-content and audio trimming decisions
  right. The figures move by a case or two between transcriptions.

## What the choices behind it were

- Speech: `openai/whisper-large-v3-turbo` through OpenRouter, pinned to the
  Groq upstream. The same model at other upstreams returned far fewer words or,
  at one, loops of invented text. Opus in an OGG container is accepted and word
  timestamps come back.
- Embeddings: `baai/bge-m3` through OpenRouter, so one key covers both. Eight
  models were compared on the seeds by how many correct anchors they find.
  Four came out level (`bge-m3`, `multilingual-e5-large`, `pplx-embed-v1-0.6b`
  and `text-embedding-3-large`), and `bge-m3` is among the cheapest of them.
  The seed set is too small to call this final.

Reproduce with `retimer bench fetch-seeds` and `retimer bench run`.

File success is the share of files with at least 95% of dialogue lines within 250 ms of their true start, and the column after it is the same with 500 ms. Error columns are medians over files. The tolerance columns pool all dialogue lines. A tool that reports no outcome counts as having left a file alone when no line moved more than 100 ms.

## Overall

| System | Runs | File success | At 500 ms | Median error (ms) | 90th pct error (ms) | Within 100 ms | Within 250 ms | Within 500 ms | Decisions right | Failed | Mean time (s) | Cost (USD) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| alass | 194 | 39% | 39% | 39,715 | 164,460 | 40% | 42% | 44% | 9% | 0 | 2.8 | 0.0000 |
| ffsubsync | 194 | 33% | 43% | 7,187 | 59,980 | 52% | 60% | 61% | 9% | 0 | 3.8 | 0.0000 |
| ffsubsync-split | 194 | 43% | 45% | 110 | 40,244 | 62% | 65% | 65% | 16% | 0 | 3.8 | 0.0000 |
| input | 194 | 11% | 11% | 8,560 | 20,602 | 15% | 20% | 20% | 23% | 0 | 0.0 | 0.0000 |
| pipeline | 194 | 86% | 89% | 0 | 0 | 64% | 97% | 99% | 66% | 0 | 0.3 | 0.0000 |

## By distortion

| System | Distortion | Runs | File success | At 500 ms | Median error (ms) | 90th pct error (ms) | Within 100 ms | Within 250 ms | Within 500 ms | Decisions right | Failed | Mean time (s) | Cost (USD) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| alass | none | 10 | 40% | 40% | 39,716 | 167,727 | 41% | 42% | 45% | 40% | 0 | 2.6 | 0.0000 |
| alass | constant | 50 | 40% | 40% | 39,715 | 167,726 | 41% | 42% | 45% | n/a | 0 | 2.7 | 0.0000 |
| alass | rate | 30 | 40% | 40% | 38,086 | 155,839 | 41% | 45% | 45% | n/a | 0 | 2.9 | 0.0000 |
| alass | blocks | 50 | 38% | 38% | 39,716 | 167,727 | 39% | 41% | 43% | n/a | 0 | 2.7 | 0.0000 |
| alass | mixed | 20 | 35% | 35% | 48,442 | 161,018 | 35% | 36% | 38% | n/a | 0 | 3.0 | 0.0000 |
| alass | noise | 30 | 40% | 40% | 24,245 | 126,387 | 41% | 42% | 44% | 0% | 0 | 3.0 | 0.0000 |
| alass | wrong_content | 4 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0% | 0 | 2.2 | 0.0000 |
| ffsubsync | none | 10 | 40% | 70% | 106 | 244 | 61% | 82% | 85% | 40% | 0 | 3.7 | 0.0000 |
| ffsubsync | constant | 50 | 52% | 64% | 70 | 70 | 66% | 75% | 76% | n/a | 0 | 3.8 | 0.0000 |
| ffsubsync | rate | 30 | 50% | 70% | 88 | 157 | 69% | 83% | 85% | n/a | 0 | 3.8 | 0.0000 |
| ffsubsync | blocks | 50 | 0% | 0% | 64,464 | 129,988 | 22% | 25% | 26% | n/a | 0 | 3.7 | 0.0000 |
| ffsubsync | mixed | 20 | 0% | 0% | 58,848 | 127,792 | 5% | 6% | 7% | n/a | 0 | 3.7 | 0.0000 |
| ffsubsync | noise | 30 | 60% | 70% | 70 | 70 | 77% | 84% | 85% | 0% | 0 | 3.9 | 0.0000 |
| ffsubsync | wrong_content | 4 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0% | 0 | 3.5 | 0.0000 |
| ffsubsync-split | none | 10 | 70% | 70% | 70 | 70 | 85% | 85% | 85% | 70% | 0 | 3.5 | 0.0000 |
| ffsubsync-split | constant | 50 | 70% | 70% | 70 | 70 | 85% | 86% | 86% | n/a | 0 | 3.9 | 0.0000 |
| ffsubsync-split | rate | 30 | 60% | 60% | 70 | 70 | 70% | 70% | 71% | n/a | 0 | 3.7 | 0.0000 |
| ffsubsync-split | blocks | 50 | 6% | 6% | 53,958 | 137,996 | 31% | 34% | 34% | n/a | 0 | 3.8 | 0.0000 |
| ffsubsync-split | mixed | 20 | 0% | 5% | 56,942 | 135,869 | 15% | 19% | 23% | n/a | 0 | 3.9 | 0.0000 |
| ffsubsync-split | noise | 30 | 60% | 70% | 70 | 70 | 77% | 84% | 85% | 0% | 0 | 3.9 | 0.0000 |
| ffsubsync-split | wrong_content | 4 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0% | 0 | 3.5 | 0.0000 |
| input | none | 10 | 100% | 100% | 0 | 0 | 100% | 100% | 100% | 100% | 0 | 0.0 | 0.0000 |
| input | constant | 50 | 20% | 20% | 8,000 | 8,000 | 0% | 20% | 20% | n/a | 0 | 0.0 | 0.0000 |
| input | rate | 30 | 0% | 0% | 10,853 | 20,730 | 0% | 0% | 0% | n/a | 0 | 0.0 | 0.0000 |
| input | blocks | 50 | 0% | 0% | 56,048 | 133,648 | 37% | 38% | 38% | n/a | 0 | 0.0 | 0.0000 |
| input | mixed | 20 | 0% | 0% | 77,391 | 165,412 | 0% | 0% | 0% | n/a | 0 | 0.0 | 0.0000 |
| input | noise | 30 | 0% | 0% | 2,700 | 2,700 | 0% | 0% | 0% | 0% | 0 | 0.0 | 0.0000 |
| input | wrong_content | 4 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0% | 0 | 0.0 | 0.0000 |
| pipeline | none | 10 | 100% | 100% | 0 | 0 | 100% | 100% | 100% | 100% | 0 | 0.3 | 0.0000 |
| pipeline | constant | 50 | 100% | 100% | 0 | 0 | 60% | 100% | 100% | n/a | 0 | 0.3 | 0.0000 |
| pipeline | rate | 30 | 100% | 100% | 0 | 0 | 60% | 100% | 100% | n/a | 0 | 0.4 | 0.0000 |
| pipeline | blocks | 50 | 62% | 68% | 0 | 123 | 67% | 93% | 97% | n/a | 0 | 0.3 | 0.0000 |
| pipeline | mixed | 20 | 60% | 75% | 0 | 192 | 60% | 90% | 97% | n/a | 0 | 0.3 | 0.0000 |
| pipeline | noise | 30 | 100% | 100% | 0 | 0 | 60% | 100% | 100% | 50% | 0 | 0.3 | 0.0000 |
| pipeline | wrong_content | 4 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 100% | 0 | 0.1 | 0.0000 |

## By reference

| System | Reference | Runs | File success | At 500 ms | Median error (ms) | 90th pct error (ms) | Within 100 ms | Within 250 ms | Within 500 ms | Decisions right | Failed | Mean time (s) | Cost (USD) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| alass | audio | 78 | 0% | 0% | 56,955 | 188,444 | 1% | 5% | 7% | 0% | 0 | 4.7 | 0.0000 |
| alass | subtitle | 78 | 97% | 97% | 0 | 0 | 99% | 100% | 100% | 22% | 0 | 0.0 | 0.0000 |
| alass | both | 38 | 0% | 0% | 61,656 | 185,581 | 1% | 4% | 7% | 0% | 0 | 4.5 | 0.0000 |
| ffsubsync | audio | 78 | 13% | 29% | 59,980 | 81,852 | 35% | 49% | 50% | 0% | 0 | 5.9 | 0.0000 |
| ffsubsync | subtitle | 78 | 63% | 63% | 0 | 1 | 78% | 78% | 79% | 22% | 0 | 0.6 | 0.0000 |
| ffsubsync | both | 38 | 13% | 29% | 61,518 | 81,852 | 36% | 48% | 50% | 0% | 0 | 5.9 | 0.0000 |
| ffsubsync-split | audio | 78 | 26% | 29% | 1,110 | 56,530 | 48% | 52% | 53% | 11% | 0 | 5.7 | 0.0000 |
| ffsubsync-split | subtitle | 78 | 67% | 68% | 0 | 0 | 84% | 84% | 85% | 22% | 0 | 0.9 | 0.0000 |
| ffsubsync-split | both | 38 | 26% | 29% | 1,112 | 56,530 | 47% | 51% | 52% | 12% | 0 | 5.9 | 0.0000 |
| input | audio | 78 | 11% | 11% | 8,460 | 20,486 | 15% | 21% | 21% | 22% | 0 | 0.0 | 0.0000 |
| input | subtitle | 78 | 11% | 11% | 8,560 | 20,668 | 14% | 20% | 20% | 22% | 0 | 0.0 | 0.0000 |
| input | both | 38 | 11% | 11% | 10,853 | 20,730 | 14% | 20% | 20% | 25% | 0 | 0.0 | 0.0000 |
| pipeline | audio | 78 | 78% | 86% | 186 | 240 | 12% | 95% | 98% | 89% | 0 | 0.3 | 0.0000 |
| pipeline | subtitle | 78 | 91% | 91% | 0 | 0 | 99% | 99% | 99% | 33% | 0 | 0.3 | 0.0000 |
| pipeline | both | 38 | 92% | 92% | 0 | 0 | 99% | 99% | 99% | 88% | 0 | 0.4 | 0.0000 |

