# Changelog

## 0.1.0 (not released yet)

The first public release.

- Syncs SRT and ASS subtitles to a reference subtitle, to audio, or to both:
  offsets, frame rates, cuts and ad breaks, stretches that drift, and files of
  several episodes. A file of other content is refused.
- A model (Claude Haiku 5.5 through OpenRouter by default) settles what the
  routine steps leave open; code does the timing math.
- Up to six reference subtitles per job, and the one that fits is chosen.
- The matched reference: the reference reduced to the lines the target has a
  counterpart for, for dual subtitles.
- Picture subtitles (PGS) read into text.
- Clean-up options for Japanese captions: the brackets around a thought or a
  voice off screen, speaker names with their readings or after such a bracket,
  and sound descriptions in 〔〕.
- An HTTP API with keys, rate limits, a daily budget and upload limits; HTTPS
  behind a proxy, or plain HTTP on a home network with `RETIMER_ALLOW_HTTP`.
- A Dockerfile and a compose file for running it next to Jellyfin.
- The benchmark and the real-file kit used to measure it.
- Licensed under the AGPL-3.0.
