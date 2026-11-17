# Deployment

How the service runs at `https://retimer.example.org`, on the
rented server behind a Cloudflare Tunnel, and how it got there.

```
client ── HTTPS ──> Cloudflare ── tunnel ──> cloudflared ── HTTP ──> 127.0.0.1:8712
                                             (on the server)         retimer.service
```

The service listens on the machine itself and nowhere else. It has no port to
the outside and no certificate of its own. Cloudflare terminates TLS, and
`cloudflared` on the server holds an outgoing connection to Cloudflare through
which requests arrive. The service believes `cloudflared`, and nobody else,
about whether a request came over HTTPS and from which address.

## What is where

| Path | Owner | Content |
| --- | --- | --- |
| `/opt/retimer/releases/COMMIT` | `deploy` | One release: the code and its Python environment. |
| `/opt/retimer/current` | `deploy` | A link to the release that runs. |
| `/opt/retimer/repo.git`, `.ssh`, `bin`, `python`, `cache` | `deploy` | A copy of the repository, the key that reads it, `uv`, and what `uv` downloads. |
| `/opt/retimer/smoke` | `deploy` | The film the smoke test uploads. |
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

## The tunnel

The server already ran a Cloudflare Tunnel, so the service needed no tunnel of
its own, only a hostname on that one. In the Cloudflare Zero Trust dashboard,
under Networks, Tunnels, the tunnel of the server, Public Hostnames:

| Field | Value |
| --- | --- |
| Subdomain | `subtitleapi` |
| Domain | `example.org` |
| Service | `HTTP`, `127.0.0.1:8712` |

Cloudflare Access stays off for this hostname. The service checks its own key
on every call, and Access in front would turn scripts away. `cloudflared`
picks the change up within seconds and needs no restart.

Cloudflare has limits of its own, and what it turns away gets its answer and
not the service's:

- **100 MB a request** on the free plan. The service therefore takes 95 MB a
  request (`RETIMER_MAX_REQUEST_MB`) and 90 MB of audio, and answers anything
  between with a 413 a client can read. Above 100 MB the 413 is Cloudflare's
  and is not JSON.
- **100 seconds for an answer.** No call of the service takes that long: a job
  is submitted, and asked about later.

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

`smoke_test.py` checks that the service is closed without a key, and sends two
real jobs through the public address: one against audio, which takes a
transcript, and one that takes the agent. They cost about a cent. `--no-job`
checks the gate only. `--large` also uploads around the size limit.

## From nothing

On a fresh server with `ffmpeg`, `git` and `curl`, and an account `deploy`:

1. As root, from a copy of this directory: `bash server-setup.sh`. It makes
   the account `retimer`, the directories above, the unit and the keys tool.
   It removes nothing.
2. As `deploy`, put `uv` at `/opt/retimer/bin/uv`, make a key with
   `ssh-keygen -t ed25519 -N '' -f /opt/retimer/.ssh/deploy_key`, and put the
   host key of GitHub into `/opt/retimer/.ssh/known_hosts`. Add the public
   half of the key to the repository as a deploy key without write access.
3. As `deploy`, fetch `release.sh` once by hand and run it:
   `git clone --bare git@github.com:friedrich-de/retimer-api.git /opt/retimer/repo.git`
   with the `GIT_SSH_COMMAND` from `release.sh`, then
   `git --git-dir /opt/retimer/repo.git show main:deploy/release.sh | bash`.
4. `sudo retimer-secrets`, then the hostname on the tunnel, then the smoke
   test.
