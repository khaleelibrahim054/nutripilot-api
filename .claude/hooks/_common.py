"""Shared helpers for the project's Claude Code hooks (stdlib only)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

PROJECT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
STATE = PROJECT / ".claude" / "state"
TESTS = PROJECT / "tests"
LOCK_FILE = STATE / "tests.lock"
SNAPSHOT_DIR = STATE / "tests_snapshot"
LAST_GREEN = STATE / "last_green"
ATTEMPTS = STATE / "fix_attempts"
ESCALATION = STATE / "escalation.md"

MAX_FIX_ATTEMPTS = 8

_SKIP_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache"}


def read_event() -> dict:
    try:
        return json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return {}


def _iter_files(root: Path):
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        if path.is_file() and not (_SKIP_DIRS & set(path.parts)):
            yield path


def _pytest_config_text() -> str:
    pyproject = PROJECT / "pyproject.toml"
    if not pyproject.exists():
        return ""
    import tomllib

    data = tomllib.loads(pyproject.read_text())
    return json.dumps(data.get("tool", {}).get("pytest", {}), sort_keys=True)


def tests_hash() -> str:
    """Fingerprint of everything that defines the test suite."""
    h = hashlib.sha256()
    for path in _iter_files(TESTS):
        h.update(str(path.relative_to(PROJECT)).encode())
        h.update(path.read_bytes())
    h.update(_pytest_config_text().encode())
    return h.hexdigest()


def code_hash() -> str:
    """Fingerprint of app code + tests + config, used to skip re-running an unchanged green suite."""
    h = hashlib.sha256()
    for sub in ("app", "tests", "migrations"):
        for path in _iter_files(PROJECT / sub):
            h.update(str(path.relative_to(PROJECT)).encode())
            h.update(path.read_bytes())
    for name in ("pyproject.toml", "uv.lock"):
        p = PROJECT / name
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()


def is_locked() -> bool:
    return LOCK_FILE.exists()


def lock() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    if SNAPSHOT_DIR.exists():
        shutil.rmtree(SNAPSHOT_DIR)
    if TESTS.exists():
        shutil.copytree(TESTS, SNAPSHOT_DIR, ignore=shutil.ignore_patterns(*_SKIP_DIRS))
    LOCK_FILE.write_text(tests_hash())


def unlock() -> None:
    LOCK_FILE.unlink(missing_ok=True)
    ESCALATION.unlink(missing_ok=True)
    if SNAPSHOT_DIR.exists():
        shutil.rmtree(SNAPSHOT_DIR)


def restore() -> bool:
    """Restore tests/ to the locked snapshot. Returns False if there is no snapshot."""
    if not SNAPSHOT_DIR.exists():
        return False
    if TESTS.exists():
        shutil.rmtree(TESTS)
    shutil.copytree(SNAPSHOT_DIR, TESTS)
    return True


def tests_tampered() -> bool:
    return is_locked() and LOCK_FILE.read_text().strip() != tests_hash()


def emit(obj: dict) -> None:
    print(json.dumps(obj))
