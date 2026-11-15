# Baselines on the seed cases, 2026-11-16

ffsubsync 0.5.1 and alass 2.0.0, both with their defaults, on the 194 cases
generated from the two seed films. `input` is the untouched target. This is the
bar the service has to clear. The seed set is small (two short films heavy on
music and effects, 76 and 26 dialogue lines), so the figures say more about the
harness working than about the tools in general.

What stands out:

- With a reference subtitle, alass solves almost everything, including breaks.
  The seed translations share their timing exactly, which is the easiest case
  for a tool that matches timing spans.
- With audio, alass fails on every case, including the one that is already in
  sync. It splits the file into many blocks that it places on music and effects.
  The same happens with WAV input and with the original video file.
- ffsubsync handles offsets and rate changes against the Tears of Steel audio,
  and cannot fix breaks without `--split-penalty`. With it, it fixes few.
- Against the Sintel audio no tool solves a single case. The film has 26 lines
  of dialogue in 15 minutes of music and effects, too little speech for tools
  that only compare when someone speaks.
- No tool rejects a wrong reference or trims trailing lines.

Reproduce with `retimer bench fetch-seeds` and
`retimer bench run --systems input,ffsubsync,ffsubsync-split,alass`.

Run started 2026-11-15T23:41:03+00:00, 194 cases, 1 run(s) per case. Systems: input, ffsubsync (ffsubsync 0.5.1), ffsubsync-split (ffsubsync 0.5.1), alass (alass-cli 2.0.0).

File success is the share of files with at least 95% of dialogue lines within 250 ms of their true start. Error columns are medians over files. The tolerance columns pool all dialogue lines. A tool that reports no outcome counts as having left a file alone when no line moved more than 100 ms.

## Overall

| System | Runs | File success | Median error (ms) | 90th pct error (ms) | Within 100 ms | Within 250 ms | Within 500 ms | Decisions right | Failed | Mean time (s) | Cost (USD) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| alass | 194 | 39% | 39,715 | 164,460 | 40% | 42% | 44% | 9% | 0 | 2.8 | 0.0000 |
| ffsubsync | 194 | 33% | 7,187 | 59,980 | 52% | 60% | 61% | 9% | 0 | 3.8 | 0.0000 |
| ffsubsync-split | 194 | 43% | 110 | 40,244 | 62% | 65% | 65% | 16% | 0 | 3.8 | 0.0000 |
| input | 194 | 11% | 8,560 | 20,602 | 15% | 20% | 20% | 23% | 0 | 0.0 | 0.0000 |

## By distortion

| System | Distortion | Runs | File success | Median error (ms) | 90th pct error (ms) | Within 100 ms | Within 250 ms | Within 500 ms | Decisions right | Failed | Mean time (s) | Cost (USD) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| alass | none | 10 | 40% | 39,716 | 167,727 | 41% | 42% | 45% | 40% | 0 | 2.6 | 0.0000 |
| alass | constant | 50 | 40% | 39,715 | 167,726 | 41% | 42% | 45% | n/a | 0 | 2.7 | 0.0000 |
| alass | rate | 30 | 40% | 38,086 | 155,839 | 41% | 45% | 45% | n/a | 0 | 2.9 | 0.0000 |
| alass | blocks | 50 | 38% | 39,716 | 167,727 | 39% | 41% | 43% | n/a | 0 | 2.7 | 0.0000 |
| alass | mixed | 20 | 35% | 48,442 | 161,018 | 35% | 36% | 38% | n/a | 0 | 3.0 | 0.0000 |
| alass | noise | 30 | 40% | 24,245 | 126,387 | 41% | 42% | 44% | 0% | 0 | 3.0 | 0.0000 |
| alass | wrong_content | 4 | n/a | n/a | n/a | n/a | n/a | n/a | 0% | 0 | 2.2 | 0.0000 |
| ffsubsync | none | 10 | 40% | 106 | 244 | 61% | 82% | 85% | 40% | 0 | 3.7 | 0.0000 |
| ffsubsync | constant | 50 | 52% | 70 | 70 | 66% | 75% | 76% | n/a | 0 | 3.8 | 0.0000 |
| ffsubsync | rate | 30 | 50% | 88 | 157 | 69% | 83% | 85% | n/a | 0 | 3.8 | 0.0000 |
| ffsubsync | blocks | 50 | 0% | 64,464 | 129,988 | 22% | 25% | 26% | n/a | 0 | 3.7 | 0.0000 |
| ffsubsync | mixed | 20 | 0% | 58,848 | 127,792 | 5% | 6% | 7% | n/a | 0 | 3.7 | 0.0000 |
| ffsubsync | noise | 30 | 60% | 70 | 70 | 77% | 84% | 85% | 0% | 0 | 3.9 | 0.0000 |
| ffsubsync | wrong_content | 4 | n/a | n/a | n/a | n/a | n/a | n/a | 0% | 0 | 3.5 | 0.0000 |
| ffsubsync-split | none | 10 | 70% | 70 | 70 | 85% | 85% | 85% | 70% | 0 | 3.5 | 0.0000 |
| ffsubsync-split | constant | 50 | 70% | 70 | 70 | 85% | 86% | 86% | n/a | 0 | 3.9 | 0.0000 |
| ffsubsync-split | rate | 30 | 60% | 70 | 70 | 70% | 70% | 71% | n/a | 0 | 3.7 | 0.0000 |
| ffsubsync-split | blocks | 50 | 6% | 53,958 | 137,996 | 31% | 34% | 34% | n/a | 0 | 3.8 | 0.0000 |
| ffsubsync-split | mixed | 20 | 0% | 56,942 | 135,869 | 15% | 19% | 23% | n/a | 0 | 3.9 | 0.0000 |
| ffsubsync-split | noise | 30 | 60% | 70 | 70 | 77% | 84% | 85% | 0% | 0 | 3.9 | 0.0000 |
| ffsubsync-split | wrong_content | 4 | n/a | n/a | n/a | n/a | n/a | n/a | 0% | 0 | 3.5 | 0.0000 |
| input | none | 10 | 100% | 0 | 0 | 100% | 100% | 100% | 100% | 0 | 0.0 | 0.0000 |
| input | constant | 50 | 20% | 8,000 | 8,000 | 0% | 20% | 20% | n/a | 0 | 0.0 | 0.0000 |
| input | rate | 30 | 0% | 10,853 | 20,730 | 0% | 0% | 0% | n/a | 0 | 0.0 | 0.0000 |
| input | blocks | 50 | 0% | 56,048 | 133,648 | 37% | 38% | 38% | n/a | 0 | 0.0 | 0.0000 |
| input | mixed | 20 | 0% | 77,391 | 165,412 | 0% | 0% | 0% | n/a | 0 | 0.0 | 0.0000 |
| input | noise | 30 | 0% | 2,700 | 2,700 | 0% | 0% | 0% | 0% | 0 | 0.0 | 0.0000 |
| input | wrong_content | 4 | n/a | n/a | n/a | n/a | n/a | n/a | 0% | 0 | 0.0 | 0.0000 |

## By reference

| System | Reference | Runs | File success | Median error (ms) | 90th pct error (ms) | Within 100 ms | Within 250 ms | Within 500 ms | Decisions right | Failed | Mean time (s) | Cost (USD) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| alass | audio | 78 | 0% | 56,955 | 188,444 | 1% | 5% | 7% | 0% | 0 | 4.7 | 0.0000 |
| alass | subtitle | 78 | 97% | 0 | 0 | 99% | 100% | 100% | 22% | 0 | 0.0 | 0.0000 |
| alass | both | 38 | 0% | 61,656 | 185,581 | 1% | 4% | 7% | 0% | 0 | 4.5 | 0.0000 |
| ffsubsync | audio | 78 | 13% | 59,980 | 81,852 | 35% | 49% | 50% | 0% | 0 | 5.9 | 0.0000 |
| ffsubsync | subtitle | 78 | 63% | 0 | 1 | 78% | 78% | 79% | 22% | 0 | 0.6 | 0.0000 |
| ffsubsync | both | 38 | 13% | 61,518 | 81,852 | 36% | 48% | 50% | 0% | 0 | 5.9 | 0.0000 |
| ffsubsync-split | audio | 78 | 26% | 1,110 | 56,530 | 48% | 52% | 53% | 11% | 0 | 5.7 | 0.0000 |
| ffsubsync-split | subtitle | 78 | 67% | 0 | 0 | 84% | 84% | 85% | 22% | 0 | 0.9 | 0.0000 |
| ffsubsync-split | both | 38 | 26% | 1,112 | 56,530 | 47% | 51% | 52% | 12% | 0 | 5.9 | 0.0000 |
| input | audio | 78 | 11% | 8,460 | 20,486 | 15% | 21% | 21% | 22% | 0 | 0.0 | 0.0000 |
| input | subtitle | 78 | 11% | 8,560 | 20,668 | 14% | 20% | 20% | 22% | 0 | 0.0 | 0.0000 |
| input | both | 38 | 11% | 10,853 | 20,730 | 14% | 20% | 20% | 25% | 0 | 0.0 | 0.0000 |
