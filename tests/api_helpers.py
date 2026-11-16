"""A service on a temporary directory, with stand-ins for everything outside it."""

from dataclasses import dataclass, field
from pathlib import Path

from starlette.testclient import TestClient

from retimer import dumps
from retimer.api import ApiSettings, create_app
from retimer.api.auth import new_key
from retimer.api.store import JobStore
from retimer.api.worker import Services, Worker
from retimer.audio import AudioInfo
from retimer.cache import Cache, content_hash
from retimer.providers import DEFAULT_SPEECH_MODEL, DEFAULT_SPEECH_PROVIDER
from retimer.transcribe import DEFAULT_MAX_CHUNK_MS, Transcript

from .sync_helpers import FakeClient, make_subtitle, spread

AUDIO = b"not really audio, but the same bytes every time"


def fake_probe(path: Path) -> AudioInfo:
    return AudioInfo(container="ogg", codec="opus", duration_ms=180_000)


def srt(starts: list[int], texts: list[str] | None = None) -> bytes:
    return dumps(make_subtitle(starts, texts)).encode("utf-8")


def shifted(count: int = 30, by_ms: int = 3_500) -> tuple[bytes, bytes]:
    """A target that is late by `by_ms`, and the reference subtitle it belongs to."""
    return srt([start + by_ms for start in spread(count)]), srt(spread(count))


def starts_of(text: str) -> list[int]:
    from retimer import loads

    return [line.start_ms for line in loads(text).lines]


@dataclass
class Service:
    """The application, a client that holds a key, and the parts a test looks into."""

    settings: ApiSettings
    key: str
    app: object
    client: TestClient
    store: JobStore
    worker: Worker
    cache: Cache
    provider: FakeClient
    keys: dict[str, str] = field(default_factory=dict)

    def auth(self, label: str | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.keys[label] if label else self.key}"}

    def anonymous(self, **kwargs) -> TestClient:
        """A client without a key."""
        return TestClient(self.app, base_url="https://testserver", **kwargs)

    def submit(self, files: dict, options: str | None = None, **kwargs):
        data = {"options": options} if options is not None else None
        parts = {name: (f"{name}.bin", content) for name, content in files.items()}
        return self.client.post("/v1/jobs", files=parts, data=data, **kwargs)

    def run(self) -> None:
        """Run every queued job, as the worker would."""
        while self.worker.run_once():
            pass

    def finished(self, files: dict, options: str | None = None) -> dict:
        """Submit a job, run it, and return how it ended."""
        response = self.submit(files, options)
        assert response.status_code == 202, response.text
        self.run()
        return self.client.get(response.json()["links"]["self"]).json()

    def remember_transcript(self, transcript: Transcript, audio: bytes = AUDIO) -> None:
        """Put a transcript where the worker looks for the one of `audio`."""
        key = content_hash(
            audio, DEFAULT_SPEECH_MODEL, DEFAULT_SPEECH_PROVIDER, "", str(DEFAULT_MAX_CHUNK_MS)
        )
        self.cache.put("transcript-v1", key, transcript.model_dump_json().encode("utf-8"))


def make_service(
    tmp_path: Path, *, labels=("al",), model_client=None, probe=fake_probe, **overrides
):
    """A service whose worker only runs when the test says so."""
    keys = {}
    entries = []
    for label in labels:
        keys[label], entry = new_key(label)
        entries.append(entry)
    settings = ApiSettings(
        **{"data_dir": tmp_path / "data", "api_keys": entries, "min_free_bytes": 0, **overrides}
    )
    cache = Cache(tmp_path / "cache")
    provider = FakeClient()
    services = Services(cache=cache, openrouter=provider, model_client=model_client)
    app = create_app(settings, services=services, run_worker=False, probe=probe)
    first = keys[labels[0]]
    client = TestClient(
        app, base_url="https://testserver", headers={"Authorization": f"Bearer {first}"}
    )
    return Service(
        settings=settings,
        key=first,
        app=app,
        client=client,
        store=app.state.store,
        worker=app.state.worker,
        cache=cache,
        provider=provider,
        keys=keys,
    )
