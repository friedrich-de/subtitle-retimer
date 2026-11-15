"""The systems under test. Each runner syncs one case and says what it did.

External tools are run as commands, exactly as a user would run them. They take
one reference, so a case with both audio and a reference subtitle gives them the
audio.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from retimer.bench.cases import LoadedCase
from retimer.engine import PlanError, apply_plan
from retimer.io import load, save

DEFAULT_TIMEOUT_S = 600
_ERROR_CHARS = 600


class RunOutput(BaseModel):
    """What a system produced for one case.

    `outcome` is None for tools that do not decide one. `kept_ids` names the
    target line behind every output line, for systems that know it.
    """

    outcome: str | None = None
    output_path: Path | None = None
    kept_ids: list[int] | None = None
    seconds: float = 0.0
    cost_usd: float = 0.0
    error: str | None = None
    details: dict = Field(default_factory=dict)


class Runner(Protocol):
    name: str

    def unavailable(self) -> str | None:
        """Why this system cannot run here, or None when it can."""

    def supports(self, case: LoadedCase) -> bool: ...

    def run(self, case: LoadedCase, work_dir: Path) -> RunOutput: ...


class InputRunner:
    """Returns the target untouched. Shows how far off the cases are to begin with."""

    name = "input"

    def unavailable(self) -> str | None:
        return None

    def supports(self, case: LoadedCase) -> bool:
        return True

    def run(self, case: LoadedCase, work_dir: Path) -> RunOutput:
        output = work_dir / case.case.target
        shutil.copyfile(case.target_path, output)
        return RunOutput(output_path=output)


class OracleRunner:
    """Applies the ideal plan stored with a generated case. Checks the harness itself."""

    name = "oracle"

    def unavailable(self) -> str | None:
        return None

    def supports(self, case: LoadedCase) -> bool:
        return case.case.ideal_plan is not None or case.case.family == "wrong_content"

    def run(self, case: LoadedCase, work_dir: Path) -> RunOutput:
        if case.case.family == "wrong_content":
            return RunOutput(outcome="rejected_no_match")
        plan = case.case.ideal_plan
        try:
            result = apply_plan(load(case.target_path), plan)
        except PlanError as error:
            return RunOutput(outcome="failed", error=str(error))
        output = work_dir / case.case.target
        save(result.subtitle, output)
        return RunOutput(
            outcome=plan.outcome,
            output_path=output,
            kept_ids=result.subtitle.ids,
            details={"plan": plan.model_dump(mode="json", by_alias=True, exclude_none=True)},
        )


class CommandRunner:
    """Base for tools that are run as a command line program."""

    name: str
    executable: str
    env_var: str

    def __init__(
        self,
        name: str | None = None,
        extra_args: tuple[str, ...] = (),
        timeout_s: int = DEFAULT_TIMEOUT_S,
    ) -> None:
        if name:
            self.name = name
        self.extra_args = tuple(extra_args)
        self.timeout_s = timeout_s

    def _path(self) -> str | None:
        return os.environ.get(self.env_var) or shutil.which(self.executable)

    def unavailable(self) -> str | None:
        if self._path() is None:
            return f"{self.executable} is not installed (or set {self.env_var})"
        return None

    def version(self) -> str | None:
        path = self._path()
        if path is None:
            return None
        try:
            done = subprocess.run(
                [path, "--version"], capture_output=True, text=True, timeout=60, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        lines = (done.stdout or done.stderr).strip().splitlines()
        return lines[-1] if lines else None

    def supports(self, case: LoadedCase) -> bool:
        return True

    def reference_path(self, case: LoadedCase) -> Path:
        path = case.audio_path or case.reference_subtitle_path
        if path is None:
            raise ValueError(f"case {case.case.id} has no reference")
        return path

    def command(self, reference: Path, target: Path, output: Path) -> list[str]:
        raise NotImplementedError

    def run(self, case: LoadedCase, work_dir: Path) -> RunOutput:
        # The tool runs inside the work directory, so every path has to be absolute.
        output = (work_dir / case.case.target).resolve()
        command = self.command(self.reference_path(case), case.target_path.resolve(), output)
        started = time.monotonic()
        try:
            done = subprocess.run(
                command,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=self.timeout_s,
                cwd=work_dir,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return RunOutput(
                outcome="failed",
                seconds=time.monotonic() - started,
                error=f"timed out after {self.timeout_s} s",
            )
        seconds = time.monotonic() - started
        log = (done.stderr or done.stdout or "").strip()
        if done.returncode != 0 or not output.exists():
            return RunOutput(
                outcome="failed",
                seconds=seconds,
                error=f"exit code {done.returncode}: {log[-_ERROR_CHARS:]}",
            )
        return RunOutput(output_path=output, seconds=seconds, details={"command": command[1:]})


class FfsubsyncRunner(CommandRunner):
    """ffsubsync: correlates speech activity with line timing. Global offset and rate.

    With `--split-penalty` it also looks for breaks, as alass does.
    """

    name = "ffsubsync"
    executable = "ffsubsync"
    env_var = "RETIMER_FFSUBSYNC"

    def command(self, reference: Path, target: Path, output: Path) -> list[str]:
        return [
            self._path(),
            str(reference),
            "-i",
            str(target),
            "-o",
            str(output),
            *self.extra_args,
        ]


class AlassRunner(CommandRunner):
    """alass: matches timing spans and detects splits, so it handles block shifts."""

    name = "alass"
    executable = "alass-cli"
    env_var = "RETIMER_ALASS"

    def command(self, reference: Path, target: Path, output: Path) -> list[str]:
        return [self._path(), *self.extra_args, str(reference), str(target), str(output)]


def builtin_runners() -> dict[str, Runner]:
    runners: list[Runner] = [
        InputRunner(),
        OracleRunner(),
        FfsubsyncRunner(),
        FfsubsyncRunner("ffsubsync-split", ("--split-penalty",)),
        AlassRunner(),
    ]
    return {runner.name: runner for runner in runners}


DEFAULT_SYSTEMS = ("input", "ffsubsync", "ffsubsync-split", "alass")
