# Deployment on a Linux server

How to run the service on the internet: as a system service on a Linux server
with systemd, behind a proxy that does HTTPS. For a home network next to
Jellyfin, the Docker setup in the [README](../README.md) is simpler.

```
client ── HTTPS ──> proxy (Cloudflare Tunnel, Caddy, nginx) ── HTTP ──> 127.0.0.1:8712
                    on the server or in front of it                   retimer.service
```

The service listens on the machine itself and nowhere else. It has no port to
the outside and no certificate of its own. The proxy terminates TLS, and the
service believes the proxy, and nobody else, about whether a request came over
HTTPS and from which address. The examples below use
`https://retimer.example.org`; put your own address in its place.

## What is where

| Path | Owner | Content |
| --- | --- | --- |
| `/opt/retimer/releases/COMMIT` | `deploy` | One release: the code and its Python environment. |
| `/opt/retimer/current` | `deploy` | A link to the release that runs. |
| `/opt/retimer/repo.git`, `bin`, `python`, `cache` | `deploy` | A copy of the repository, `uv`, and what `uv` downloads. |
| `/opt/retimer/.ssh` | `deploy` | Only for a private repository: the deploy key that reads it. |
| `/opt/retimer/smoke` | `deploy` | The film the smoke test uploads (`smoke_test.py` fetches it). |
| `/var/lib/retimer` | `retimer` | Jobs, uploads while they arrive, the cache of transcripts. |
| `/etc/retimer/retimer.env` | `root` | Settings that are not secret. |
| `/etc/retimer/secrets.env` | `root`, mode 600 | The OpenRouter key and the hashes of the API keys. |
| `/etc/retimer/selfcheck.key` | `root`, group `deploy` | An API key of its own for the smoke test. |
| `/etc/systemd/system/retimer.service` | `root` | The unit. |
| `/usr/local/sbin/retimer-secrets` | `root` | The tool that takes the keys. |

Three accounts have a part. `deploy` installs releases and can do so without
root. `retimer` runs the service: it has no login, cannot write the code it
runs, and sees its data directory and the network and nothing else of the
machine. `root` holds the keys and hands them to the service when it starts.

## The service

`retimer.service` starts at boot, and comes back after ten seconds when the
process crashes or is killed. It does not come back from exit status 2, which
is a setup the service refuses to start with, such as a missing key. It is
held to 4 GB of memory and yields the processor to the other services of the
machine.

```bash
systemctl status retimer
sudo journalctl -u retimer -n 50        # the log blanks keys
sudo systemctl restart retimer
```

Settings are in `/etc/retimer/retimer.env`, one `NAME=value` a line, and take
effect at the next restart. The README of the repository lists them.

## Keys

No key is ever in the repository, in a chat or on a command line. A person
types them on the server:

```bash
sudo retimer-secrets                  # the OpenRouter key, and the first API key
sudo retimer-secrets status           # what is set, without showing any of it
sudo retimer-secrets new-key LABEL    # one more API key, for another client
sudo retimer-secrets revoke LABEL     # stop accepting a key
```

Input is hidden. A provider key is checked with its provider before it is
saved. An API key is shown once, to the person at the terminal, and the server
keeps its hash. The tool restarts the service when something changed.

One provider key is all the service needs. OpenRouter serves the transcripts
and the embeddings, and the agent calls its model through it as well, pinned
to Anthropic's own servers. To call the model at Anthropic directly instead,
put `RETIMER_AGENT_PROVIDER=anthropic` into `/etc/retimer/retimer.env`; the
tool then asks for an Anthropic key too.

## The proxy

Anything that terminates TLS and forwards to `127.0.0.1:8712` will do. Run it
on the same machine, or add its address to `RETIMER_TRUSTED_PROXIES` in
`/etc/retimer/retimer.env`.

- **Cloudflare Tunnel.** In the Zero Trust dashboard, under Networks, Tunnels,
  add a public hostname to the server's tunnel with the service `HTTP`,
  `127.0.0.1:8712`. Leave Cloudflare Access off for it: the service checks its
  own key on every call, and Access in front would turn clients away.
- **Caddy.** `retimer.example.org { reverse_proxy 127.0.0.1:8712 }`.

A proxy may have limits of its own, and what it turns away gets its answer and
not the service's. Cloudflare's free plan stops a request at 100 MB, so the
service takes 95 MB a request (`RETIMER_MAX_REQUEST_MB`) and 90 MB of audio,
and answers anything between with a 413 a client can read. Cloudflare also
gives an answer 100 seconds; no call of the service takes that long, because a
job is submitted and asked about later.

## A new release

```bash
/opt/retimer/current/deploy/release.sh        # as deploy: fetch main, build, test, switch
sudo systemctl restart retimer
cd /opt/retimer/current && .venv/bin/python deploy/smoke_test.py --url https://retimer.example.org
```

`release.sh` builds the release in a directory of its own from `uv.lock`,
runs the tests there, and only then points `current` at it. The service keeps
running the old release until the restart. When the unit or the keys tool
changed, `release.sh` says so, and `sudo bash
/opt/retimer/current/deploy/server-setup.sh` installs them. The last three
releases stay, and `release.sh COMMIT` goes back to one of them.

`smoke_test.py` checks that the service is closed without a key, and sends
three real jobs through the public address: one against audio, which takes a
transcript, one that takes the agent and asks for the matched reference back,
and one with three reference subtitles of which only one fits. They cost about
a cent. `--no-job` checks the gate only. `--large` also uploads around the
size limit.

## From nothing

On a fresh server with `ffmpeg`, `git` and `curl`, and an account `deploy`:

1. As root, from a copy of this directory: `bash server-setup.sh`. It makes
   the account `retimer`, the directories above, the unit and the keys tool.
   It removes nothing.
2. As `deploy`, put `uv` at `/opt/retimer/bin/uv`.
3. As `deploy`, fetch the repository once by hand and run `release.sh` from it:
   `git clone --bare https://github.com/friedrich-de/subtitle-retimer.git /opt/retimer/repo.git`,
   then `git --git-dir /opt/retimer/repo.git show main:deploy/release.sh | bash`.
   For a private fork, make a key with
   `ssh-keygen -t ed25519 -N '' -f /opt/retimer/.ssh/deploy_key`, put GitHub's
   host key into `/opt/retimer/.ssh/known_hosts`, add the public half to the
   fork as a deploy key without write access, and set `RETIMER_REPOSITORY` to
   the fork's SSH address. `release.sh` uses the key when it is there.
4. `sudo retimer-secrets`, then the proxy, then the smoke test.
