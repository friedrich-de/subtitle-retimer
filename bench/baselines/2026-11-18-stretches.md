# Stretches that run apart: 588 cases, 2026-11-18

What the agent's question about stretches that lean, and its tool
`retime_stretches`, change on the benchmark. All 588 cases of
[bench/README.md](../README.md), the agent with Haiku 5.5, every case once.
The run before the change already holds the step that cuts a file of several
episodes down, so it solves more than
[the run over 535 cases](2026-11-18-real-file-problems.md) did.

A case is **solved** as in the other runs: 95% of the dialogue lines within
250 ms of their true start, or, where nothing can be timed, the file refused
or left as it was.

## In short

| | Before | With the stretches |
| --- | --- | --- |
| Solved of 588 | 538 (91%) | 550 (94%) |
| Against a reference subtitle, of 466 | 447 | 459 |
| Against audio alone, of 122 | 91 | 91 |
| Jobs where the model was asked | 211 | 222 |
| What the model cost, all jobs | 0.22 USD | 0.27 USD |

- **Twelve cases are gained and none is lost.** All twelve are in the family
  `caption_drift`, which no system solved before: one or two stretches of the
  file sit 0.35 s to 0.5 s early inside one block.
- **The question comes up in 13 cases**: 12 of the 15 drift cases against a
  reference subtitle, and one two-language file that was solved before and
  after. The model named the stretches in every one of them.
- **Every other family is solved as often as before**, the 21 files of several
  episodes among them.

The run over all 588 cases was made when a stretch still began after the
longest pause between its first pair and the last pair before it, and solved
548. The lines in between now say where it begins. The 42 cases of the two
families in which the question comes up were run again after that, and two
more drift cases are solved. Nothing else reads those bounds.

## The drift cases

| Case | Reference | Cases | Before | With the stretches |
| --- | --- | --- | --- | --- |
| `half-the-file-0.5s-early` | subtitle | 5 | 0 | 5 |
| `one-stretch-0.4s-early` | subtitle | 5 | 0 | 4 |
| `two-stretches-off` | subtitle | 5 | 0 | 3 |
| all three | audio | 6 | 0 | 0 |
| All | | 21 | 0 | 12 |

Where it gains, nine lines in ten were up to 0.25 s to 0.4 s off before and
are within 0.1 s after.

The nine that stay open:

- **Six are against audio alone.** The times of a transcript wander by a few
  tenths of a second themselves, so a stretch that leans cannot be told from
  the transcript's own scatter. The question is not asked there.
- **Three are stretches too short to be told from scatter**, in the two short
  films of 26 and 76 lines. A stretch is flagged when it holds at least four
  lines that found their counterpart and its level stands clearly apart from
  the lines around it. The two films are timed by hand in both languages, and
  their pairs scatter by up to 0.3 s.

## What was tried with it and left out

Three more changes were measured in the same runs and are not in the code,
because none of them solved a case:

| | Before | Asked on open questions | Asked on every job |
| --- | --- | --- | --- |
| Solved of 588, with all four changes | 538 | 545 | 548 |
| Against audio alone, of 122 | 91 | 88 | 91 |
| Jobs where the model was asked | 211 | 223 | 584 |
| What the model cost, all jobs | 0.22 USD | 0.44 USD | 0.67 USD |

- **A reading sample on every dossier**, a dozen lines from all through the
  file with the place each lands on. The model read more for it, which
  doubled what a job costs where it is asked (0.10 to 0.20 cents), and no
  case was solved through it.
- **A tool to open placed lines again** and pair them with other places.
  Against a reference subtitle no job changed its result. Against audio four
  files that were right fell under the bar and one was gained: a pair with a
  place of a transcript gives a line the rough time of a stretch of speech in
  place of the time the routine steps found.
- **Asking the model on every job.** Of the 365 jobs without an open question
  the same 352 are solved. The model changed the plan of 20 and the outcome
  of none. A job that is asked without need costs 0.07 cents and about four
  seconds. The three cases between 545 and 548 are audio jobs that were asked
  in both runs and paired differently.

## Limits

- One run per column. From run to run the model settles two or three audio
  cases differently.
- In the benchmark every stretch that is flagged is drift that was put there.
  A check without any model that retimes every flagged stretch solves the same
  ten cases. What the model's reading adds shows only on real files, where a
  stretch can lean for other reasons.
- The drift cases are three kinds on five seeds. Drift that grows slowly
  through a file is not among them.
