from pathlib import Path

import pytest

from retimer import load

DATA = Path(__file__).parent / "data"


@pytest.fixture
def ass():
    return load(DATA / "sample.ass")


@pytest.fixture
def srt():
    return load(DATA / "sample.srt")


@pytest.fixture(autouse=True)
def no_provider_keys(monkeypatch):
    """No test reaches a hosted service: the keys of the machine are out of sight."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_RETIMER_API_KEY", raising=False)
