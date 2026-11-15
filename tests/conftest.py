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
