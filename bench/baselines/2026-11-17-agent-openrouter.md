# The agent through OpenRouter, 2026-11-17

Since this day the agent calls its model through OpenRouter unless it is told
otherwise (`RETIMER_AGENT_PROVIDER`), so that one key covers everything the
service buys. This run checks that the route makes no difference to the
result. It is `agent-haiku` at medium effort on the same 194 seed cases as
[2026-11-16-agent.md](2026-11-16-agent.md), run once, with no Anthropic key on
the machine. The transcripts were made anew for it.

| | Called at Anthropic, 2026-11-16 | Through OpenRouter, 2026-11-17 |
| --- | --- | --- |
| Files solved, of 194 | 183 | 183 |
| File success | 94% | 94% |
| At 500 ms | 98% | 98% |
| Decisions right | 100% | 100% |
| Files the model is asked about | 61 (31%) | 60 (31%) |
| Turns when asked | 2.1 | 2.1 |
| Seconds when asked | 6 | 6.0 |
| Cost when asked | 0.08 cents | 0.08 cents |
| Cost of the run | 5 cents | 5 cents |
| Not finished, declined, kept back | 0 | 0 |

The fixed pipeline solves 170 files in both runs, and no case is worse with
the agent than without it.

## What the route is

- The same client and the same requests. OpenRouter takes the Messages API of
  Anthropic at `https://openrouter.ai/api`, with the instructions, the strict
  tools, the effort setting and the prompt cache as they are. A job still
  reads nearly all its input tokens from the cache.
- The model is `anthropic/claude-haiku-5.5` there. A report keeps the name
  `claude-haiku-5-5` and says under `provider` how the model was reached.
- Every request is pinned to Anthropic's own servers (`only: anthropic`, no
  fallbacks). OpenRouter can also serve a Claude model from other clouds, and
  those were not measured.
- The prices are the list prices on both routes, and the cost in a report is
  computed from the token counts as before. What OpenRouter adds on top
  depends on the account and is not in a report.
- OpenRouter does not take the request for a rerun of a declined request on
  another model, which Opus 5.5 and Sonnet 5.5 use at Anthropic. Through
  OpenRouter a declined request leaves the plan of the routine steps, as it
  does for Haiku 5.5 on either route.

One figure of the pipeline moved: it gets 27 of the 44 decisions right,
against 29 the day before. The difference is in the cases against audio alone
(14 of 18, against 16), where a decision rests on the transcript, and the
transcripts of this run are new ones. The agent gets all 44 right in both
runs.
