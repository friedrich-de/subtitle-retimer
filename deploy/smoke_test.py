#!/usr/bin/env python3
"""Checks a running service the way a client meets it: closed without a key, working with one.

Run by the deploying account with the Python of the release, after a deploy or
whenever the service is in doubt:

    cd /opt/retimer/current
    .venv/bin/python deploy/smoke_test.py --url https://retimer.example

It checks the gate, then sends three real jobs with a subtitle of Tears of Steel.
One has a block moved by 41 seconds and goes against the audio of the film,
which takes a transcript. The second has an outro added that the film does not
have and goes against the Japanese subtitle, which takes the agent, and asks
for that subtitle back as the matched reference. The third comes with three
reference subtitles, as a video file holds them: a track of signs, the
subtitle of another film and the Japanese one, which the service has to find.
Together they cost about one cent at the providers. `--large` adds the uploads
around the size limit, which is where the proxy in front has a say of its own.

The key comes from a file and is never printed, and never sent over plain HTTP
to anything but this machine.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pysubs2

from retimer.bench.cases import load_seeds
from retimer.bench.seeds import encode_speech_audio

REPOSITORY = Path(__file__).resolve().parent.parent
SEEDS = REPOSITORY / "bench" / "seeds"
SEED = "tears-of-steel"
# Every line from here on is moved, as if a scene had been cut in before it.
BLOCK_FROM_MS = 240_000
BLOCK_SHIFT_MS = 41_000
# The other job: everything late by this much, and an outro nobody speaks in the film.
OFFSET_MS = 2_500
OUTRO = ("Thank you for watching.", "Subscribe for more films like this one.", "See you next time.")
TOLERANCE_MS = 500
# The third job: what a track of signs shows, and the film whose subtitle is other content.
SIGNS = ("Harbor Office", "Platform 3", "Closed on Sundays", "Chapter Two", "North Gate")
OTHER_SEED = "sintel"
MB = 1024 * 1024
LOCAL = ("127.0.0.1", "localhost", "::1")


class Checks:
    """Counts what held and what did not, and says each as it happens."""

    def __init__(self) -> None:
        self.failed = 0

    def check(self, holds: bool, what: str, detail: str = "") -> bool:
        if not holds:
            self.failed += 1
        mark = "ok  " if holds else "FAIL"
        print(f"{mark}  {what}{f' ({detail})' if detail else ''}", flush=True)
        return holds

    def note(self, text: str) -> None:
        print(f"      {text}", flush=True)


def error_code(response: httpx.Response) -> str | None:
    """The code of a refusal by the service, or None when the answer is not one of its own."""
    try:
        return response.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return None


def seed_audio(media: Path, say) -> Path:
    """The audio of the seed film as a client would send it. Downloaded once."""
    seed = next(seed for seed in load_seeds(SEEDS).seeds if seed.id == SEED)
    destination = media / seed.audio.file
    if not destination.exists():
        say(f"downloading {seed.audio.source_url}")
        with tempfile.TemporaryDirectory(dir=media) as temp:
            download = Path(temp) / "source"
            with (
                urllib.request.urlopen(seed.audio.source_url) as response,
                download.open("wb") as handle,
            ):
                shutil.copyfileobj(response, handle)
            encode_speech_audio(download, destination)
    return destination


def moved_block() -> tuple[bytes, list[int]]:
    """The English subtitle with its second part moved, and where each line belongs."""
    subtitle = pysubs2.load(str(SEEDS / SEED / "en.srt"))
    truth = [line.start for line in subtitle]
    for line in subtitle:
        if line.start >= BLOCK_FROM_MS:
            line.start += BLOCK_SHIFT_MS
            line.end += BLOCK_SHIFT_MS
    return subtitle.to_string("srt").encode("utf-8"), truth


def late_with_outro() -> tuple[bytes, list[int]]:
    """The English subtitle, late as a whole, with lines after the end that belong nowhere."""
    subtitle = pysubs2.load(str(SEEDS / SEED / "en.srt"))
    truth = [line.start for line in subtitle]
    subtitle.shift(ms=OFFSET_MS)
    cursor = max(line.end for line in subtitle) + 4_000
    for text in OUTRO:
        subtitle.append(pysubs2.SSAEvent(start=cursor, end=cursor + 2_500, text=text))
        cursor += 3_000
    return subtitle.to_string("srt").encode("utf-8"), truth


def signs_track() -> bytes:
    """A track of signs, as a video file has it next to the full one. No line is spoken."""
    subtitle = pysubs2.SSAFile()
    subtitle.styles["Signs"] = pysubs2.SSAStyle()
    for index, text in enumerate(SIGNS):
        start = 5_000 + index * 17_000
        subtitle.append(
            pysubs2.SSAEvent(
                start=start, end=start + 2_000, style="Signs", text="{\\pos(320,60)}" + text
            )
        )
    return subtitle.to_string("ass").encode("utf-8")


def check_gate(checks: Checks, url: str, key: str) -> None:
    host = urlsplit(url).hostname or ""
    # A proxy on this machine is believed about HTTPS, so a check against the
    # service itself has to say what the proxy would.
    forwarded = {"X-Forwarded-Proto": "https"} if host in LOCAL else {}
    with httpx.Client(base_url=url, timeout=30, headers=forwarded) as client:
        response = client.get("/healthz")
        checks.check(
            response.status_code == 200 and response.json() == {"status": "ok"},
            "the health check answers",
        )

        response = client.get("/v1/jobs")
        checks.check(
            response.status_code == 401 and error_code(response) == "unauthorized",
            "a request without a key is refused",
            f"{response.status_code} {error_code(response)}",
        )
        made_up = {"Authorization": f"Bearer rt_{secrets.token_urlsafe(32)}"}
        response = client.get("/v1/jobs", headers=made_up)
        checks.check(
            response.status_code == 401 and error_code(response) == "unauthorized",
            "a request with a wrong key is refused",
            f"{response.status_code} {error_code(response)}",
        )
        response = client.post("/v1/jobs", files={"target": ("a.srt", b"1\n")})
        checks.check(
            response.status_code == 401,
            "an upload without a key is refused",
            str(response.status_code),
        )
        for path in (
            "/docs",
            "/openapi.json",
            "/v1/features",
            "/v1/jobs/0123456789abcdef0123456789abcdef",
        ):
            response = client.get(path)
            checks.check(response.status_code == 401, f"{path} says nothing without a key", "401")

        response = client.get("/v1/jobs", headers={"Authorization": f"Bearer {key}"})
        checks.check(
            response.status_code == 200 and "jobs" in response.json(),
            "a request with the key is served",
            str(response.status_code),
        )
        response = client.get("/v1/features", headers={"Authorization": f"Bearer {key}"})
        features = response.json().get("features", []) if response.status_code == 200 else []
        checks.check(
            {"reference_tracks", "remove_signs_and_credits"} <= set(features),
            "the service says what it can do",
            f"{response.status_code} {features}",
        )

    if host not in LOCAL and url.startswith("https://"):
        # Without the key: nothing secret goes over plain HTTP.
        plain = "http://" + url[len("https://") :]
        try:
            response = httpx.get(plain + "/v1/jobs", timeout=30)
            closed = response.status_code in (301, 302, 307, 308, 403)
            checks.check(closed, "plain HTTP is turned away", str(response.status_code))
        except httpx.HTTPError as error:
            checks.check(True, "plain HTTP is turned away", type(error).__name__)


def run_job(
    checks: Checks,
    client: httpx.Client,
    target: tuple[bytes, list[int]],
    reference: tuple[str, str, Path],
    wait_s: float,
    needs_agent: bool = False,
    extra_lines: bool = False,
    matched_reference: bool = False,
) -> None:
    """Send a target with one reference, wait for the job and judge what comes back.

    `target` is the subtitle and where its lines belong. `reference` is the
    form field, the file name to send, and the file. `extra_lines` is for a
    target with lines after the end that nobody removes. `matched_reference`
    asks for the reference subtitle back, reduced to what the target says too.
    """
    subtitle, truth = target
    field, name, file = reference
    size = file.stat().st_size / MB
    what = f"{size:.0f} MB of audio" if field == "reference_audio" else "a reference subtitle"
    started = time.monotonic()
    with file.open("rb") as handle:
        response = client.post(
            "/v1/jobs",
            files={"target": ("tears-of-steel.en.srt", subtitle), field: (name, handle)},
            data={"options": json.dumps({"matched_reference": True})} if matched_reference else {},
            timeout=httpx.Timeout(30, write=900, read=300),
        )
    if not checks.check(
        response.status_code == 202,
        f"a job with {what} is accepted",
        f"{response.status_code} {error_code(response) or ''}".strip(),
    ):
        checks.note(response.text[:300])
        return
    checks.note(f"upload took {time.monotonic() - started:.1f} s")
    job = response.json()
    path = f"/v1/jobs/{job['id']}"
    try:
        deadline = time.monotonic() + wait_s
        while job["status"] != "finished" and time.monotonic() < deadline:
            time.sleep(2)
            job = client.get(path).json()
        if not checks.check(job["status"] == "finished", "the job finishes", job["status"]):
            return
        summary = job.get("summary") or {}
        checks.check(
            job["outcome"] == "synced",
            "the job ends as synced",
            f"{job['outcome']}, error {job.get('error')}" if job["outcome"] != "synced" else "",
        )
        agent = summary.get("agent") or {}
        checks.note(
            f"confidence {summary.get('confidence')}, fit {summary.get('fit')}, "
            f"{summary.get('seconds')} s, {summary.get('cost_usd')} USD, "
            f"agent {agent.get('model')} consulted {agent.get('consulted')}"
        )
        for warning in summary.get("warnings") or []:
            checks.note(f"warning: {warning[:200]}")
        if needs_agent:
            checks.check(
                agent.get("consulted") is True and agent.get("finished") == "submitted",
                "the agent settled what the routine steps left open",
                f"consulted {agent.get('consulted')}, finished {agent.get('finished')}",
            )

        response = client.get(path + "/result")
        if checks.check(response.status_code == 200, "the result can be fetched"):
            result = pysubs2.SSAFile.from_string(response.text)
            starts = [line.start for line in result]
            near = sum(abs(a - b) <= TOLERANCE_MS for a, b in zip(starts, truth, strict=False))
            complete = len(starts) >= len(truth) if extra_lines else len(starts) == len(truth)
            checks.check(
                complete and near >= 0.9 * len(truth),
                "the lines are back where they belong",
                f"{near} of {len(truth)} within {TOLERANCE_MS} ms",
            )
        if matched_reference:
            response = client.get(path + "/matched_reference")
            if checks.check(response.status_code == 200, "the matched reference can be fetched"):
                handed = [line.start for line in pysubs2.SSAFile.from_string(response.text)]
                own = [line.start for line in pysubs2.load(str(file))]
                checks.check(
                    set(handed) <= set(own) and len(handed) >= 0.9 * len(own),
                    "the matched reference holds the lines of the reference, at their own times",
                    f"{len(handed)} of {len(own)} lines",
                )
        for document in ("plan", "report"):
            response = client.get(f"{path}/{document}")
            checks.check(response.status_code == 200, f"the {document} can be fetched")
    finally:
        deleted = client.delete(path)
        gone = client.get(path)
        checks.check(
            deleted.status_code == 204 and gone.status_code == 404,
            "the job is deleted on request",
            f"{deleted.status_code}, then {gone.status_code}",
        )


def run_choice(checks: Checks, client: httpx.Client, wait_s: float) -> None:
    """Send a target with three reference subtitles and see that the right one is taken."""
    subtitle, truth = late_with_outro()
    tracks = [
        ("signs.ass", signs_track(), {"language": "eng", "title": "Signs", "forced": True}),
        (
            f"{OTHER_SEED}.en.srt",
            (SEEDS / OTHER_SEED / "en.srt").read_bytes(),
            {"language": "eng", "title": "English", "default": True},
        ),
        ("tears-of-steel.ja.srt", (SEEDS / SEED / "ja.srt").read_bytes(), {"language": "jpn"}),
    ]
    options = {"agent": "off", "reference_tracks": [label for _, _, label in tracks]}
    files = [("target", ("tears-of-steel.en.srt", subtitle))]
    files += [("reference_subtitle", (name, content)) for name, content, _ in tracks]
    response = client.post("/v1/jobs", files=files, data={"options": json.dumps(options)})
    if not checks.check(
        response.status_code == 202,
        "a job with three reference subtitles is accepted",
        f"{response.status_code} {error_code(response) or ''}".strip(),
    ):
        checks.note(response.text[:300])
        return
    job = response.json()
    path = f"/v1/jobs/{job['id']}"
    try:
        deadline = time.monotonic() + wait_s
        while job["status"] != "finished" and time.monotonic() < deadline:
            time.sleep(2)
            job = client.get(path).json()
        if not checks.check(job["status"] == "finished", "the job finishes", job["status"]):
            return
        summary = job.get("summary") or {}
        reference = summary.get("reference") or {}
        verdicts = [track.get("verdict") for track in reference.get("candidates") or []]
        checks.check(
            job["outcome"] == "synced" and reference.get("chosen") == 2,
            "the subtitle of the film is chosen among the three",
            f"{job['outcome']}, chosen {reference.get('chosen')}",
        )
        checks.check(
            verdicts == ["too_little_dialogue", "no_match", "chosen"],
            "the signs and the other film are told apart from it",
            ", ".join(str(verdict) for verdict in verdicts),
        )
        checks.note(f"{reference.get('reason')} {summary.get('cost_usd')} USD")
        response = client.get(path + "/result")
        if checks.check(response.status_code == 200, "the result can be fetched"):
            starts = [line.start for line in pysubs2.SSAFile.from_string(response.text)]
            near = sum(abs(a - b) <= TOLERANCE_MS for a, b in zip(starts, truth, strict=False))
            checks.check(
                near >= 0.9 * len(truth),
                "the lines are back where they belong",
                f"{near} of {len(truth)} within {TOLERANCE_MS} ms",
            )
    finally:
        deleted = client.delete(path)
        checks.check(deleted.status_code == 204, "the job is deleted on request")


def upload_with_curl(url: str, key: str, body: Path, work: Path) -> tuple[int, str]:
    """Send `body` as audio with curl, which takes an answer that comes before the upload ends.

    The key goes in through a file, so it is on no command line.
    """
    header = work / "header"
    header.touch(mode=0o600)
    header.write_text(f"Authorization: Bearer {key}\n")
    answer = work / "answer"
    target = work / "target.srt"
    target.write_bytes(moved_block()[0])
    command = [
        "curl", "--silent", "--show-error", "--max-time", "600",
        "--output", str(answer), "--write-out", "%{http_code}",
        "--header", f"@{header}", "--header", "Expect:",
        "--form", f"target=@{target}", "--form", f"reference_audio=@{body};filename=audio.wav",
    ]  # fmt: skip
    if (urlsplit(url).hostname or "") in LOCAL:
        command += ["--header", "X-Forwarded-Proto: https"]
    done = subprocess.run([*command, url + "/v1/jobs"], capture_output=True, text=True, check=False)
    text = answer.read_text(errors="replace") if answer.exists() else done.stderr
    return int(done.stdout or 0), text


def check_limits(
    checks: Checks,
    url: str,
    key: str,
    client: httpx.Client,
    audio: Path,
    media: Path,
    wait_s: float,
) -> None:
    """What happens around the size limit, where the proxy in front has a limit of its own."""
    with tempfile.TemporaryDirectory(dir=media) as temp:
        work = Path(temp)
        zeros = work / "zeros"
        for megabytes, by_service in ((96, True), (101, False)):
            with zeros.open("wb") as handle:
                handle.truncate(megabytes * MB)
            status, text = upload_with_curl(url, key, zeros, work)
            ours = '"body_too_large"' in text
            if by_service:
                checks.check(
                    status == 413 and ours,
                    f"a request of {megabytes} MB is refused by the service, in its own words",
                    f"{status}: {text[:160]}",
                )
            else:
                checks.check(
                    status == 413,
                    f"a request of {megabytes} MB is refused",
                    f"{status}, by {'the service' if ours else 'the proxy in front'}",
                )
        zeros.unlink()

        # The same film as plain PCM: about 70 MB, the size of a client that did not compress.
        wav = work / "tears-of-steel.wav"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(audio), "-ac", "1", "-ar", "48000",
             "-c:a", "pcm_s16le", str(wav)],
            check=True,
        )  # fmt: skip
        run_job(checks, client, moved_block(), ("reference_audio", wav.name, wav), wait_s)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="where the service is reached")
    parser.add_argument("--key-file", type=Path, default=Path("/etc/retimer/selfcheck.key"))
    parser.add_argument(
        "--media", type=Path, default=Path("/opt/retimer/smoke"), help="where the film is kept"
    )
    parser.add_argument("--no-job", action="store_true", help="check the gate only, at no cost")
    parser.add_argument("--large", action="store_true", help="also test around the size limit")
    parser.add_argument(
        "--no-agent", action="store_true", help="for a server that runs without the agent"
    )
    parser.add_argument("--wait", type=float, default=300.0, help="seconds a job may take")
    args = parser.parse_args()

    url = args.url.rstrip("/")
    host = urlsplit(url).hostname or ""
    if not url.startswith("https://") and host not in LOCAL:
        print("error: the key is only sent over HTTPS, or to this machine", file=sys.stderr)
        return 2
    key = os.environ.get("RETIMER_API_KEY") or args.key_file.read_text().strip()
    checks = Checks()
    print(f"checking {url}", flush=True)
    check_gate(checks, url, key)
    if not args.no_job:
        args.media.mkdir(parents=True, exist_ok=True)
        audio = seed_audio(args.media, checks.note)
        headers = {"Authorization": f"Bearer {key}"}
        if host in LOCAL:
            headers["X-Forwarded-Proto"] = "https"
        with httpx.Client(base_url=url, timeout=60, headers=headers) as client:
            run_job(
                checks, client, moved_block(), ("reference_audio", audio.name, audio), args.wait
            )
            japanese = SEEDS / SEED / "ja.srt"
            run_job(
                checks,
                client,
                late_with_outro(),
                ("reference_subtitle", "tears-of-steel.ja.srt", japanese),
                args.wait,
                needs_agent=not args.no_agent,
                # The lines after the end are for the agent to remove.
                extra_lines=args.no_agent,
                matched_reference=True,
            )
            run_choice(checks, client, args.wait)
            if args.large:
                check_limits(checks, url, key, client, audio, args.media, args.wait)
    with contextlib.suppress(BrokenPipeError):
        print("all checks held" if not checks.failed else f"{checks.failed} check(s) failed")
    return 1 if checks.failed else 0


if __name__ == "__main__":
    sys.exit(main())
