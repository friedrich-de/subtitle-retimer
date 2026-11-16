import os
import threading
import time

import pytest

from retimer.api.store import (
    FINISHED,
    PLAN,
    QUEUED,
    REFERENCE_AUDIO,
    RESULT,
    RUNNING,
    TARGET,
    JobStore,
    is_job_id,
)
from retimer.cache import Cache

REQUEST = {"target": {"filename": "a.srt"}, "options": {}, "agent": "off", "output_format": "srt"}
DAY = 86_400.0


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return JobStore(tmp_path / "data", retention_s=DAY, clock=clock)


def queue(store: JobStore, owner: str = "al", files: tuple[str, ...] = (TARGET,)) -> str:
    job_id, directory = store.new_directory()
    for name in files:
        (directory / name).write_text("content")
    store.add(job_id, owner, REQUEST)
    return job_id


def test_a_job_is_queued_with_its_request_and_an_expiry(store, clock):
    job_id = queue(store)
    job = store.get(job_id)

    assert is_job_id(job_id)
    assert (job.status, job.outcome, job.attempts) == (QUEUED, None, 0)
    assert job.request == REQUEST
    assert job.expires_at == clock.now + DAY
    assert store.file(job_id, TARGET).read_text() == "content"
    assert store.file(job_id, RESULT) is None


def test_a_job_is_only_found_by_its_owner(store):
    job_id = queue(store, owner="al")

    assert store.get(job_id, "al") is not None
    assert store.get(job_id, "someone") is None
    assert store.list("someone") == []
    assert [job.id for job in store.list("al")] == [job_id]


def test_what_is_not_a_job_id_never_reaches_the_disk(store):
    for value in ("", "..", "../jobs", "a" * 31, "A" * 32, "g" * 32, "a" * 32 + "/x"):
        assert store.get(value) is None
        assert store.delete(value, "al") is None
        with pytest.raises(ValueError):
            store.directory(value)


def test_jobs_are_claimed_oldest_first_and_once(store, clock):
    first = queue(store)
    clock.now += 1
    second = queue(store)

    one = store.claim()
    two = store.claim()

    assert (one.id, two.id) == (first, second)
    assert (one.status, one.attempts, one.started_at) == (RUNNING, 1, clock.now)
    assert store.claim() is None


def test_two_workers_never_get_the_same_job(store):
    queued = {queue(store) for _ in range(40)}
    claimed: list[str] = []
    lock = threading.Lock()

    def work() -> None:
        while (job := store.claim()) is not None:
            with lock:
                claimed.append(job.id)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(claimed) == sorted(queued)


def test_finishing_records_the_outcome_and_deletes_the_audio(store, clock):
    job_id = queue(store, files=(TARGET, REFERENCE_AUDIO))
    store.claim()
    store.set_stage(job_id, "matching")
    assert store.get(job_id).stage == "matching"
    clock.now += 30

    store.finish(job_id, "synced", summary={"confidence": 0.9}, cost_usd=0.02)

    job = store.get(job_id)
    assert (job.status, job.stage, job.outcome) == (FINISHED, None, "synced")
    assert job.summary == {"confidence": 0.9}
    assert job.finished_at == clock.now
    assert job.expires_at == clock.now + DAY
    assert store.file(job_id, REFERENCE_AUDIO) is None
    assert store.file(job_id, TARGET) is not None
    assert store.active("al") == 0


def test_what_jobs_cost_adds_up_per_day(store, clock):
    for cost in (0.02, 0.03):
        job_id = queue(store)
        store.claim()
        store.finish(job_id, "synced", cost_usd=cost)
    assert store.spent_today() == pytest.approx(0.05)

    # A job is counted once, however often it is closed.
    store.finish(job_id, "synced", cost_usd=5.0)
    assert store.spent_today() == pytest.approx(0.05)

    clock.now += DAY
    assert store.spent_today() == 0.0


def test_a_job_can_be_deleted_unless_it_is_running(store):
    both = {queue(store), queue(store)}
    running = store.claim().id
    (waiting,) = both - {running}

    assert store.delete(running, "al") == RUNNING
    assert store.get(running) is not None
    assert store.delete(waiting, "someone") is None
    assert store.delete(waiting, "al") == "deleted"
    assert store.get(waiting) is None
    assert not store.directory(waiting).exists()

    store.finish(running, "synced")
    assert store.delete(running, "al") == "deleted"
    assert not store.directory(running).exists()


def test_an_interrupted_job_is_started_over_once_and_then_closed(store):
    job_id = queue(store)
    store.claim()

    assert store.recover() == (1, 0)
    assert store.get(job_id).status == QUEUED

    assert store.claim().attempts == 2
    assert store.recover() == (0, 1)
    job = store.get(job_id)
    assert (job.status, job.outcome, job.error["code"]) == (FINISHED, "failed", "interrupted")
    assert store.claim() is None


def test_expired_jobs_are_deleted_with_their_files(store, clock):
    old = queue(store)
    store.claim()
    (store.directory(old) / RESULT).write_text("result")
    (store.directory(old) / PLAN).write_text("{}")
    store.finish(old, "synced")
    clock.now += DAY / 2
    young = queue(store)
    clock.now += DAY / 2 + 1

    assert store.sweep() == 1

    assert store.get(old) is None
    assert not store.directory(old).exists()
    assert store.get(young) is not None
    assert store.directory(young).exists()


def test_a_running_job_is_not_swept_from_under_its_worker(store, clock):
    job_id = queue(store)
    store.claim()
    clock.now += 3 * DAY

    assert store.sweep() == 0
    assert store.directory(job_id).exists()

    store.finish(job_id, "synced")
    assert store.get(job_id).expires_at == clock.now + DAY


def test_uploads_that_never_became_a_job_are_cleared_away(store):
    fresh, fresh_dir = store.new_directory()
    stale, stale_dir = store.new_directory()
    (stale_dir / TARGET).write_text("cut off")
    two_hours_ago = time.time() - 7_200
    os.utime(stale_dir, (two_hours_ago, two_hours_ago))
    stranger = store.jobs_dir / "not-a-job"
    stranger.mkdir()

    store.sweep()

    assert not stale_dir.exists()
    assert fresh_dir.exists()
    assert stranger.exists()


def test_the_files_of_the_service_are_for_its_user_alone(store):
    job_id = queue(store)

    assert (store.path.stat().st_mode & 0o777) == 0o600
    assert (store.jobs_dir.stat().st_mode & 0o777) == 0o700
    assert (store.directory(job_id).stat().st_mode & 0o777) == 0o700


def test_old_cache_entries_are_forgotten(tmp_path, monkeypatch):
    cache = Cache(tmp_path / "cache")
    moment = [1_800_000_000.0]
    monkeypatch.setattr("retimer.cache.time.time", lambda: moment[0])
    cache.put("transcript-v1", "old", b"old words")
    moment[0] += 20 * DAY
    cache.put("transcript-v1", "new", b"new words")
    moment[0] += 15 * DAY

    assert cache.prune(30 * DAY) == 1

    assert cache.get("transcript-v1", "old") is None
    assert cache.get("transcript-v1", "new") == b"new words"


def test_a_cache_from_before_entries_had_an_age_still_opens(tmp_path, monkeypatch):
    import sqlite3

    directory = tmp_path / "cache"
    directory.mkdir()
    with sqlite3.connect(directory / "cache.sqlite3") as connection:
        connection.execute(
            "CREATE TABLE entries (namespace TEXT NOT NULL, key TEXT NOT NULL, "
            "value BLOB NOT NULL, PRIMARY KEY (namespace, key))"
        )
        connection.execute("INSERT INTO entries VALUES ('embedding-v1', 'k', x'01')")

    cache = Cache(directory)

    assert cache.get("embedding-v1", "k") == b"\x01"
    # Its entries count from the first cleanup on, so nothing is lost at once.
    assert cache.prune(30 * DAY) == 0
    assert cache.get("embedding-v1", "k") == b"\x01"
    moment = time.time() + 31 * DAY
    monkeypatch.setattr("retimer.cache.time.time", lambda: moment)
    assert cache.prune(30 * DAY) == 1
