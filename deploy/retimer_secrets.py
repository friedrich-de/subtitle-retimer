#!/usr/bin/env python3
"""Puts the keys of the Subtitle Retimer service on the server it runs on.

Installed as /usr/local/sbin/retimer-secrets and run as root, by a person at a
terminal:

    sudo retimer-secrets                 enter the provider keys, make the first API key
    sudo retimer-secrets status          what is set, without showing any of it
    sudo retimer-secrets new-key LABEL   one more API key, for another client
    sudo retimer-secrets revoke LABEL    stop accepting a key

Keys are typed or pasted here and nowhere else. Input is hidden, nothing is
echoed, and the file they go to can be read by root alone. The service gets
them from the service manager when it starts.

This file runs with the Python of the system, which may be older than the one
the service uses. It needs nothing but the standard library.
"""

import argparse
import contextlib
import getpass
import hashlib
import os
import pwd
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request

CONFIG_DIR = "/etc/retimer"
SECRETS_FILE = "secrets.env"
SELFCHECK_FILE = "selfcheck.key"
CODE_DIR = "/opt/retimer"
INSTALLED = CODE_DIR + "/current/.venv/bin/retimer"
UNIT = "retimer.service"
HEALTH_URL = "http://127.0.0.1:8712/healthz"

OPENROUTER = "OPENROUTER_API_KEY"
ANTHROPIC = "ANTHROPIC_RETIMER_API_KEY"
API_KEYS = "RETIMER_API_KEYS"

# The same shapes as retimer.api.auth, which is what reads the entries.
KEY_PREFIX = "rt_"
LABEL = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
ENTRY = re.compile(r"^([a-z0-9][a-z0-9_-]{0,31}):([0-9a-f]{64})$")
# What a provider key is made of. Anything else is a paste gone wrong.
PROVIDER_KEY = re.compile(r"^[A-Za-z0-9._-]{16,400}$")

FIRST_LABEL = "al"
# The key the deploying account tests the running service with.
SELFCHECK_LABEL = "selfcheck"


class Refused(Exception):
    """Something the person at the terminal has to know. Nothing was changed."""


# Keys of this service


def make_key():
    """A fresh API key, and the hash the server keeps of it."""
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    return key, hashlib.sha256(key.encode("utf-8")).hexdigest()


def parse_entries(text):
    """`label:hash` entries as a dict, in their order."""
    entries = {}
    for entry in re.split(r"[,\s]+", text.strip()):
        if not entry:
            continue
        match = ENTRY.match(entry)
        if match is None:
            raise Refused(API_KEYS + " in the secrets file holds an entry that is not label:hash.")
        entries[match.group(1)] = match.group(2)
    return entries


def format_entries(entries):
    return ",".join(label + ":" + digest for label, digest in entries.items())


# The secrets file


def read_env(path):
    """NAME=value lines of a file as a dict. A file that is not there is empty."""
    values = {}
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, _, value = line.partition("=")
                values[name.strip()] = value.strip()
    except FileNotFoundError:
        pass
    return values


def write_private(path, text, gid=0, mode=0o600):
    """Write a file so that it is never there half, and never readable by more than `mode`."""
    temporary = path + ".new"
    with contextlib.suppress(FileNotFoundError):
        os.unlink(temporary)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fchown(handle.fileno(), 0, gid)
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def write_env(path, values):
    lines = [
        "# Keys of the Subtitle Retimer service. Written by retimer-secrets.",
        "# Root alone may read this file. Change it with: sudo retimer-secrets",
    ]
    lines += [name + "=" + value for name, value in values.items()]
    write_private(path, "\n".join(lines) + "\n")


def deploy_gid(code_dir):
    """The group of the account that deploys the code, which also tests the service."""
    try:
        return pwd.getpwuid(os.stat(code_dir).st_uid).pw_gid
    except (FileNotFoundError, KeyError):
        return 0


# The providers


def check_provider(name, key, timeout_s=15):
    """Ask the provider whether it knows the key: True, False, or None when it cannot be told."""
    if name == OPENROUTER:
        request = urllib.request.Request(
            "https://openrouter.ai/api/v1/key", headers={"Authorization": "Bearer " + key}
        )
    else:
        request = urllib.request.Request(
            "https://api.anthropic.com/v1/models?limit=1",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
        )
    request.add_header("User-Agent", "retimer-secrets")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return True if response.status == 200 else None
    except urllib.error.HTTPError as error:
        return False if error.code in (401, 403) else None
    except (OSError, ValueError):
        return None


def ask_provider_key(name, title, current, ask=getpass.getpass, check=check_provider, out=print):
    """Ask for one provider key until it is accepted, kept as it is, or skipped."""
    state = "set, Enter keeps it" if current else "not set, Enter skips it"
    for _ in range(5):
        value = ask(title + " (" + state + "): ").strip()
        if not value:
            return current
        if not PROVIDER_KEY.match(value):
            out("  That is not a key: it has spaces or characters no key has. Try again.")
            continue
        known = check(name, value)
        if known is False:
            out("  The provider does not accept this key. Try again.")
            continue
        if known is None:
            out("  The provider could not be asked right now. The key is saved unchecked.")
        else:
            out("  Accepted by the provider.")
        return value
    raise Refused("No usable key after five tries.")


# The service


def installed():
    return os.path.exists(INSTALLED)


def restart(out=print):
    """Start the service over with the new keys and say whether it came up."""
    if not installed():
        out("Saved. The code is not installed yet, so the service starts later.")
        return
    subprocess.run(["systemctl", "restart", UNIT], check=False)
    for _ in range(30):
        time.sleep(1)
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=2) as response:
                if response.status == 200:
                    out("Saved. The service was restarted and is running.")
                    return
        except (OSError, ValueError):
            pass
    out("Saved, but the service did not come up. Look at: journalctl -u retimer -n 30")


def show_key(label, key, out=print):
    out("")
    out("API key '" + label + "'. It is shown once and stored nowhere:")
    out("")
    out("  " + key)
    out("")
    out("Keep it in a password manager. A client sends it as:")
    out("  Authorization: Bearer <key>")
    out("")


# Commands


class Secrets:
    """The keys of one server: its secrets file and the key for the self check."""

    def __init__(self, config_dir=CONFIG_DIR, code_dir=CODE_DIR):
        self.path = os.path.join(config_dir, SECRETS_FILE)
        self.selfcheck_path = os.path.join(config_dir, SELFCHECK_FILE)
        self.code_dir = code_dir
        self.values = read_env(self.path)
        self.entries = parse_entries(self.values.get(API_KEYS, ""))

    def save(self):
        self.values[API_KEYS] = format_entries(self.entries)
        ordered = {
            name: self.values[name]
            for name in (OPENROUTER, ANTHROPIC, API_KEYS)
            if self.values.get(name)
        }
        write_env(self.path, ordered)

    def add_key(self, label):
        if not LABEL.match(label):
            raise Refused(
                "A label is 1 to 32 characters: lower-case letters, digits, hyphen, underscore."
            )
        if label in self.entries:
            raise Refused(
                "There is a key named '" + label + "' already. Revoke it first, or pick a name."
            )
        key, digest = make_key()
        self.entries[label] = digest
        return key

    def ensure_selfcheck(self):
        """Make sure the key for the self check exists and the server accepts it."""
        try:
            with open(self.selfcheck_path, encoding="utf-8") as handle:
                held = handle.read().strip()
        except FileNotFoundError:
            held = ""
        digest = hashlib.sha256(held.encode("utf-8")).hexdigest() if held else None
        if digest is not None and self.entries.get(SELFCHECK_LABEL) == digest:
            return
        self.entries.pop(SELFCHECK_LABEL, None)
        key = self.add_key(SELFCHECK_LABEL)
        write_private(self.selfcheck_path, key + "\n", gid=deploy_gid(self.code_dir), mode=0o640)


def command_setup(secrets_, ask=getpass.getpass, check=check_provider, out=print):
    out("Keys for the Subtitle Retimer service on this server.")
    out("What you type or paste is hidden. Finish each with Enter.")
    out("")
    for name, title in ((OPENROUTER, "OpenRouter API key"), (ANTHROPIC, "Anthropic API key")):
        value = ask_provider_key(name, title, secrets_.values.get(name), ask, check, out)
        if value:
            secrets_.values[name] = value
    if not secrets_.values.get(OPENROUTER):
        out("")
        out("Without an OpenRouter key the service does not start. Run this again when you")
        out("have it.")
    if not secrets_.values.get(ANTHROPIC):
        out("")
        out("Without an Anthropic key jobs run the fixed pipeline, without the agent.")
    clients = [label for label in secrets_.entries if label != SELFCHECK_LABEL]
    key = None if clients else secrets_.add_key(FIRST_LABEL)
    secrets_.ensure_selfcheck()
    secrets_.save()
    if key is not None:
        show_key(FIRST_LABEL, key, out)
    else:
        out("")
        out("API keys stay as they are: " + ", ".join(clients) + ".")
    return True


def command_status(secrets_, out=print):
    for name, title in ((OPENROUTER, "OpenRouter API key"), (ANTHROPIC, "Anthropic API key")):
        out(title + ": " + ("set" if secrets_.values.get(name) else "not set"))
    labels = [label for label in secrets_.entries if label != SELFCHECK_LABEL]
    out("API keys: " + (", ".join(labels) if labels else "none"))
    out("Key for the self check: " + ("yes" if SELFCHECK_LABEL in secrets_.entries else "no"))
    out("Code installed: " + ("yes" if installed() else "no"))
    state = subprocess.run(
        ["systemctl", "is-active", UNIT], capture_output=True, text=True, check=False
    )
    out("Service: " + (state.stdout.strip() or "unknown"))
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="retimer-secrets", description="The keys of the Subtitle Retimer service."
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("setup", help="enter the provider keys and make the first API key")
    commands.add_parser("status", help="what is set, without showing any of it")
    new_key = commands.add_parser("new-key", help="one more API key, for another client")
    new_key.add_argument("label")
    revoke = commands.add_parser("revoke", help="stop accepting a key")
    revoke.add_argument("label")
    args = parser.parse_args(argv)
    command = args.command or "setup"

    if os.geteuid() != 0:
        print("error: run this as root: sudo retimer-secrets", file=sys.stderr)
        return 1
    if command != "status" and not sys.stdin.isatty():
        print("error: run this at a terminal, so that keys are typed, not piped", file=sys.stderr)
        return 1
    os.umask(0o077)
    try:
        held = Secrets()
        if command == "setup":
            changed = command_setup(held)
        elif command == "status":
            changed = command_status(held)
        elif command == "new-key":
            key = held.add_key(args.label)
            held.save()
            show_key(args.label, key)
            changed = True
        else:
            if args.label not in held.entries:
                raise Refused("There is no key named '" + args.label + "'.")
            del held.entries[args.label]
            if args.label == SELFCHECK_LABEL:
                held.ensure_selfcheck()
            held.save()
            print("The key '" + args.label + "' is no longer accepted.")
            changed = True
    except Refused as error:
        print("error: " + str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped. Nothing was changed.", file=sys.stderr)
        return 130
    if changed:
        restart()
    return 0


if __name__ == "__main__":
    sys.exit(main())
