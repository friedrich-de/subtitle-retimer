"""Jobs and their files: one SQLite file for the jobs, one directory per job.

The table is also the queue. The server writes a job as `queued`, a worker
claims the oldest one, and both see the same row. Nothing a client sends ever
names a file here: a job's directory is named by the job's id, which the server
makes, and the files in it have fixed names.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# The files of a job, by what they are.
TARGET = "target"
REFERENCE_SUBTITLE = "reference_subtitle"
REFERENCE_AUDIO = "reference_audio"
# The PGS stream of a job that reads an image subtitle.
IMAGE_SUBTITLE = "image_subtitle"
RESULT = "result"
# The reference subtitle, reduced to the lines the target has a counterpart for.
MATCHED_REFERENCE = "matched_reference"
PLAN = "plan.json"
REPORT = "report.json"


def reference_file(index: int) -> str:
    """The file of the reference subtitle at this position among those of a job."""
    return f"{REFERENCE_SUBTITLE}.{index}"


QUEUED = "queued"
RUNNING = "running"
FINISHED = "finished"

# A job that was running when the service stopped is started over this often.
MAX_ATTEMPTS = 2
# A job directory without a job is an upload that was cut off. It may be in
# progress still, so it is left alone for this long.
_ORPHAN_AGE_S = 3600.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT,
    outcome TEXT,
    request TEXT NOT NULL,
    summary TEXT,
    error TEXT,
    cost_usd REAL NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_by_status ON jobs (status, created_at);
CREATE INDEX IF NOT EXISTS jobs_by_owner ON jobs (owner, created_at);
CREATE TABLE IF NOT EXISTS spend (
    day TEXT NOT NULL,
    owner TEXT NOT NULL,
    cost_usd REAL NOT NULL DEFAULT 0,
    jobs INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, owner)
);
"""
_COLUMNS = (
    "id, owner, status, stage, outcome, request, summary, error, cost_usd, attempts, "
    "created_at, started_at, finished_at, expires_at"
)


@dataclass(frozen=True)
class Job:
    id: str
    owner: str
    status: str
    stage: str | None
    outcome: str | None
    request: dict[str, Any]
    summary: dict[str, Any] | None
    error: dict[str, str] | None
    cost_usd: float
    attempts: int
    created_at: float
    started_at: float | None
    finished_at: float | None
    expires_at: float


def new_job_id() -> str:
    return uuid.uuid4().hex


def is_job_id(value: str) -> bool:
    return len(value) == 32 and all(char in "0123456789abcdef" for char in value)


def _job(row: tuple) -> Job:
    return Job(
        id=row[0],
        owner=row[1],
        status=row[2],
        stage=row[3],
        outcome=row[4],
        request=json.loads(row[5]),
        summary=json.loads(row[6]) if row[6] else None,
        error=json.loads(row[7]) if row[7] else None,
        cost_usd=row[8],
        attempts=row[9],
        created_at=row[10],
        started_at=row[11],
        finished_at=row[12],
        expires_at=row[13],
    )


def _day(now: float) -> str:
    return datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%d")


class JobStore:
    """Safe to use from several threads and from several processes."""

    def __init__(self, data_dir: Path, retention_s: float, clock=time.time) -> None:
        self.data_dir = Path(data_dir)
        self.jobs_dir = self.data_dir / "jobs"
        self.retention_s = retention_s
        self._clock = clock
        self.jobs_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.data_dir / "jobs.sqlite3"
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(_SCHEMA)
        self.path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        # Transactions are opened by hand, so that a claim is one step for everyone.
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    # Files

    def directory(self, job_id: str) -> Path:
        if not is_job_id(job_id):
            raise ValueError("not a job id")
        return self.jobs_dir / job_id

    def new_directory(self) -> tuple[str, Path]:
        """A fresh id and its empty directory, for the uploads of a job that is not yet one."""
        job_id = new_job_id()
        directory = self.directory(job_id)
        directory.mkdir(mode=0o700)
        return job_id, directory

    def discard_directory(self, job_id: str) -> None:
        shutil.rmtree(self.directory(job_id), ignore_errors=True)

    def file(self, job_id: str, name: str) -> Path | None:
        path = self.directory(job_id) / name
        return path if path.is_file() else None

    # Jobs

    def add(self, job_id: str, owner: str, request: dict[str, Any]) -> Job:
        """Queue the job whose uploads are in the directory of `job_id`."""
        now = self._clock()
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO jobs (id, owner, status, request, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (job_id, owner, QUEUED, json.dumps(request), now, now + self.retention_s),
            )
        job = self.get(job_id)
        assert job is not None
        return job

    def get(self, job_id: str, owner: str | None = None) -> Job | None:
        """The job, or None. With `owner`, also None when it belongs to someone else."""
        if not is_job_id(job_id):
            return None
        query = f"SELECT {_COLUMNS} FROM jobs WHERE id = ?"
        args: list[Any] = [job_id]
        if owner is not None:
            query += " AND owner = ?"
            args.append(owner)
        with closing(self._connect()) as connection:
            row = connection.execute(query, args).fetchone()
        return None if row is None else _job(row)

    def list(self, owner: str, limit: int = 50) -> list[Job]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM jobs WHERE owner = ? ORDER BY created_at DESC LIMIT ?",
                (owner, limit),
            ).fetchall()
        return [_job(row) for row in rows]

    def active(self, owner: str) -> int:
        """How many jobs of `owner` are queued or running."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE owner = ? AND status != ?", (owner, FINISHED)
            ).fetchone()
        return int(row[0])

    def claim(self) -> Job | None:
        """Take the oldest queued job and mark it running. None when the queue is empty."""
        now = self._clock()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "UPDATE jobs SET status = ?, stage = NULL, started_at = ?, "
                    "attempts = attempts + 1 WHERE id = ("
                    "SELECT id FROM jobs WHERE status = ? ORDER BY created_at, id LIMIT 1) "
                    f"RETURNING {_COLUMNS}",
                    (RUNNING, now, QUEUED),
                ).fetchone()
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        return None if row is None else _job(row)

    def set_stage(self, job_id: str, stage: str) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                "UPDATE jobs SET stage = ? WHERE id = ? AND status = ?", (stage, job_id, RUNNING)
            )

    def finish(
        self,
        job_id: str,
        outcome: str,
        *,
        summary: dict[str, Any] | None = None,
        error: dict[str, str] | None = None,
        cost_usd: float = 0.0,
    ) -> None:
        """Close the job. Its audio or image subtitle is deleted now, the rest when it expires."""
        now = self._clock()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "UPDATE jobs SET status = ?, stage = NULL, outcome = ?, summary = ?, "
                    "error = ?, cost_usd = ?, finished_at = ?, expires_at = ? "
                    "WHERE id = ? AND status != ? RETURNING owner",
                    (
                        FINISHED,
                        outcome,
                        json.dumps(summary) if summary is not None else None,
                        json.dumps(error) if error is not None else None,
                        cost_usd,
                        now,
                        now + self.retention_s,
                        job_id,
                        FINISHED,
                    ),
                ).fetchone()
                if row is not None:
                    connection.execute(
                        "INSERT INTO spend (day, owner, cost_usd, jobs) VALUES (?, ?, ?, 1) "
                        "ON CONFLICT (day, owner) DO UPDATE SET "
                        "cost_usd = cost_usd + excluded.cost_usd, jobs = jobs + 1",
                        (_day(now), row[0], cost_usd),
                    )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        if is_job_id(job_id):
            for name in (REFERENCE_AUDIO, IMAGE_SUBTITLE):
                (self.directory(job_id) / name).unlink(missing_ok=True)

    def delete(self, job_id: str, owner: str) -> str | None:
        """Delete a job and its files. Returns "deleted", "running", or None when unknown.

        A running job is left alone: its worker is using the files.
        """
        if not is_job_id(job_id):
            return None
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT status FROM jobs WHERE id = ? AND owner = ?", (job_id, owner)
                ).fetchone()
                if row is not None and row[0] != RUNNING:
                    connection.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        if row is None:
            return None
        if row[0] == RUNNING:
            return RUNNING
        self.discard_directory(job_id)
        return "deleted"

    def spent_today(self) -> float:
        """What the finished jobs of the current day (UTC) cost, over all keys."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM spend WHERE day = ?",
                (_day(self._clock()),),
            ).fetchone()
        return float(row[0])

    # Housekeeping

    def recover(self) -> tuple[int, int]:
        """Deal with jobs that were running when the service stopped.

        A job is started over once. One that was interrupted again is closed as
        failed, so that a file which brings the worker down cannot do so forever.
        Returns how many were queued again and how many were closed.
        """
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT id, attempts FROM jobs WHERE status = ?", (RUNNING,)
            ).fetchall()
            again = [job_id for job_id, attempts in rows if attempts < MAX_ATTEMPTS]
            connection.executemany(
                "UPDATE jobs SET status = ?, stage = NULL, started_at = NULL WHERE id = ?",
                [(QUEUED, job_id) for job_id in again],
            )
        closed = [job_id for job_id, attempts in rows if attempts >= MAX_ATTEMPTS]
        for job_id in closed:
            self.finish(
                job_id,
                "failed",
                error={
                    "code": "interrupted",
                    "message": "The service stopped twice while it worked on this job.",
                },
            )
        return len(again), len(closed)

    def sweep(self) -> int:
        """Delete what has expired: jobs past their time with their files, and stray uploads.

        Returns how many jobs were deleted.
        """
        now = self._clock()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "DELETE FROM jobs WHERE expires_at < ? AND status != ? RETURNING id",
                (now, RUNNING),
            ).fetchall()
            connection.execute("DELETE FROM spend WHERE day < ?", (_day(now - 40 * 86400),))
            known = {row[0] for row in connection.execute("SELECT id FROM jobs")}
        for (job_id,) in rows:
            self.discard_directory(job_id)
        for directory in self.jobs_dir.iterdir():
            if directory.name in known or not is_job_id(directory.name):
                continue
            try:
                age = time.time() - directory.stat().st_mtime
            except OSError:
                continue
            if age > _ORPHAN_AGE_S:
                shutil.rmtree(directory, ignore_errors=True)
        return len(rows)
