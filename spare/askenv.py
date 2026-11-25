"""Ask for a missing secret on the terminal instead of making you set an environment variable.

Setting variables is different in every shell (export, $env:, set), so local commands ask for
what's missing with typing hidden. The value lives only in this process: it's never written to a
file, logged or shown. In GitHub Actions (no terminal) nothing is asked and the jobs behave as
before: they read the environment.
"""
from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path
from typing import Callable, MutableMapping

TELLER_FILES = {"TELLER_ACCESS_TOKEN": "token.txt", "TELLER_CERT": "certificate.pem",
                "TELLER_KEY": "private_key.pem"}


def fill(wanted: list[tuple[str, str, bool]], environ: MutableMapping[str, str] | None = None,
         interactive: bool | None = None, ask_secret: Callable[[str], str] = getpass.getpass,
         ask: Callable[[str], str] = input) -> None:
    """For each (name, prompt, hidden) that isn't set, ask for it when running in a terminal."""
    environ = os.environ if environ is None else environ
    if interactive is None:
        interactive = sys.stdin.isatty()
    if not interactive:
        return
    for name, prompt, hidden in wanted:
        if environ.get(name):
            continue
        value = ""
        while not value.strip():
            value = (ask_secret if hidden else ask)(f"{prompt}{' (hidden, paste and press Enter)' if hidden else ''}: ")
        environ[name] = value.strip()


def plaid_defaults(folder: str | Path = "plaid", environ: MutableMapping[str, str] | None = None) -> None:
    """Use the access token `spare enroll --bank plaid` saved in plaid/, if it's there."""
    environ = os.environ if environ is None else environ
    path = Path(folder) / "token.txt"
    if not environ.get("PLAID_ACCESS_TOKEN") and path.is_file():
        environ["PLAID_ACCESS_TOKEN"] = path.read_text(encoding="utf-8").strip()


def teller_defaults(folder: str | Path = "teller", environ: MutableMapping[str, str] | None = None) -> None:
    """Use the files `spare enroll` and the Teller dashboard left in teller/, if they're there."""
    environ = os.environ if environ is None else environ
    folder = Path(folder)
    for name, filename in TELLER_FILES.items():
        path = folder / filename
        if environ.get(name) or not path.is_file():
            continue
        environ[name] = path.read_text(encoding="utf-8").strip() if name == "TELLER_ACCESS_TOKEN" else str(path)
