"""What is installed on the server next to the service: the keys tool and the unit."""

import ast
import importlib.util
import re
import stat
from pathlib import Path

import pytest

from retimer.api import ApiSettings
from retimer.api.auth import verify

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"


@pytest.fixture
def tool(monkeypatch):
    """The keys tool as a module. It is a script, and on the server it runs as root."""
    spec = importlib.util.spec_from_file_location("retimer_secrets", DEPLOY / "retimer_secrets.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Giving a file to root is for root to do.
    monkeypatch.setattr(module.os, "fchown", lambda *args: None)
    return module


def answers(*values):
    """Stands in for the person at the terminal."""
    remaining = list(values)
    return lambda prompt: remaining.pop(0)


def test_the_keys_tool_runs_on_the_python_of_an_older_system():
    source = (DEPLOY / "retimer_secrets.py").read_text()

    ast.parse(source, feature_version=(3, 10))
    imported = {
        node.names[0].name.split(".")[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
    }
    assert not any(isinstance(node, ast.ImportFrom) for node in ast.walk(ast.parse(source)))
    # Nothing but the standard library: root does not run the service's packages.
    assert imported <= {
        "argparse", "contextlib", "getpass", "hashlib", "os", "pwd", "re", "secrets",
        "subprocess", "sys", "time", "urllib",
    }  # fmt: skip


def test_a_first_run_leaves_a_setup_the_service_starts_with(tool, tmp_path):
    said = []
    held = tool.Secrets(str(tmp_path), str(tmp_path))

    tool.command_setup(
        held,
        ask=answers("pasted with a space", "sk-or-v1-" + "a" * 40),
        check=lambda name, key: True,
        out=said.append,
    )

    secrets_file = tmp_path / "secrets.env"
    assert stat.S_IMODE(secrets_file.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "selfcheck.key").stat().st_mode) == 0o640
    assert any("not a key" in line for line in said)
    # The service reads what the tool wrote, and takes both keys it made.
    values = tool.read_env(str(secrets_file))
    assert values["OPENROUTER_API_KEY"] == "sk-or-v1-" + "a" * 40
    # One provider key is all the service needs, and all that was asked for.
    assert "ANTHROPIC_RETIMER_API_KEY" not in values
    assert not any("Anthropic" in line for line in said)
    settings = ApiSettings.from_env({**values, "RETIMER_DATA_DIR": str(tmp_path)})
    assert [key.label for key in settings.api_keys] == ["al", "selfcheck"]
    shown = next(line.strip() for line in said if line.strip().startswith("rt_"))
    assert verify(shown, settings.api_keys).label == "al"
    selfcheck = (tmp_path / "selfcheck.key").read_text().strip()
    assert verify(selfcheck, settings.api_keys).label == "selfcheck"
    # No key is in the file the server keeps.
    assert shown not in secrets_file.read_text() and selfcheck not in secrets_file.read_text()


def test_a_second_run_keeps_what_is_there(tool, tmp_path):
    first = tool.Secrets(str(tmp_path), str(tmp_path))
    tool.command_setup(
        first, ask=answers("sk-or-v1-" + "a" * 40), check=lambda *_: None, out=lambda _: None
    )
    before = (tmp_path / "secrets.env").read_text()
    said = []

    again = tool.Secrets(str(tmp_path), str(tmp_path))
    tool.command_setup(again, ask=answers(""), check=lambda *_: None, out=said.append)

    assert (tmp_path / "secrets.env").read_text() == before
    assert not any(line.strip().startswith("rt_") for line in said)


def test_the_anthropic_key_is_asked_for_where_the_settings_call_anthropic(tool, tmp_path):
    (tmp_path / "retimer.env").write_text("RETIMER_AGENT_PROVIDER=anthropic\n")
    said = []
    held = tool.Secrets(str(tmp_path), str(tmp_path))

    tool.command_setup(
        held, ask=answers("sk-or-v1-" + "a" * 40, ""), check=lambda *_: True, out=said.append
    )
    assert any("Without an Anthropic key" in line for line in said)

    again = tool.Secrets(str(tmp_path), str(tmp_path))
    tool.command_setup(
        again, ask=answers("", "sk-ant-api03-" + "b" * 40), check=lambda *_: True, out=said.append
    )
    values = tool.read_env(str(tmp_path / "secrets.env"))
    assert values["ANTHROPIC_RETIMER_API_KEY"] == "sk-ant-api03-" + "b" * 40

    # A key that is there stays in sight after the setting is gone.
    (tmp_path / "retimer.env").write_text("")
    listed = []
    tool.command_status(tool.Secrets(str(tmp_path), str(tmp_path)), out=listed.append)
    assert "Anthropic API key: set" in listed


def test_a_key_the_provider_refuses_is_not_saved(tool, tmp_path):
    held = tool.Secrets(str(tmp_path), str(tmp_path))
    wrong, right = "sk-or-v1-" + "0" * 40, "sk-or-v1-" + "1" * 40

    tool.command_setup(
        held,
        ask=answers(wrong, right),
        check=lambda name, key: key != wrong,
        out=lambda _: None,
    )

    assert tool.read_env(str(tmp_path / "secrets.env"))["OPENROUTER_API_KEY"] == right


def test_keys_are_added_and_revoked_by_label(tool, tmp_path):
    held = tool.Secrets(str(tmp_path), str(tmp_path))
    key = held.add_key("phone")
    held.save()

    with pytest.raises(tool.Refused):
        held.add_key("phone")
    with pytest.raises(tool.Refused):
        held.add_key("Not A Label")
    read = tool.Secrets(str(tmp_path), str(tmp_path))
    assert read.entries == {"phone": tool.hashlib.sha256(key.encode()).hexdigest()}


def test_the_unit_and_the_tools_agree_on_where_the_service_is(tool):
    unit = (DEPLOY / "retimer.service").read_text()
    started = re.search(r"^ExecStart=(\S+) serve --host 127\.0\.0\.1 --port (\d+)$", unit, re.M)

    assert started is not None, "the service listens on this machine only"
    assert started.group(1) == tool.INSTALLED
    assert f"http://127.0.0.1:{started.group(2)}/healthz" == tool.HEALTH_URL
    assert f"EnvironmentFile={tool.CONFIG_DIR}/{tool.SECRETS_FILE}" in unit
