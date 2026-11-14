#!/usr/bin/env python3
"""PreToolUse guard.

Always: blocks adding skip/xfail markers to tests and tampering with the hook machinery.
While tests are locked: blocks any edit to tests/, conftest.py or pytest configuration, so a
failing test can only be made to pass by fixing application code.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import ESCALATION, PROJECT, emit, is_locked, read_event  # noqa: E402

SKIP_PATTERN = re.compile(
    r"pytest\.mark\.(skip|skipif|xfail)|pytest\.skip\(|pytest\.xfail\(|unittest\.skip"
)
PYTEST_CONFIG_FILES = {"pytest.ini", "setup.cfg", "tox.ini", "conftest.py"}
BASH_WRITE_OPS = re.compile(
    r"(?<![0-9&])>(?!&)|\btee\b|\bsed\s+-i|\bperl\s+-[a-z]*i|\brm\b|\bmv\b|\bcp\b|\btruncate\b"
    r"|\bgit\s+(checkout|restore|stash|reset|apply)\b|\bpython3?\s+-c\b|<<|\bdd\b|\bln\b"
)
HARMLESS_REDIRECTS = re.compile(r"\d?>\s*/dev/null|2>&1|>&2")


def deny(reason: str) -> None:
    emit(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    )
    sys.exit(0)


LOCKED_MSG = (
    "Tests are LOCKED: the test suite is the specification. Fix the application code so the "
    "existing tests pass; do not edit tests/, conftest.py or pytest config. If you are certain a "
    "test contradicts the real-world requirement, write your reasoning to "
    ".claude/state/escalation.md and stop so the user can decide."
)


def rel(path_str: str) -> Path | None:
    try:
        return Path(path_str).resolve().relative_to(PROJECT.resolve())
    except ValueError:
        return None


def is_test_path(p: Path) -> bool:
    return (p.parts and p.parts[0] == "tests") or p.name in PYTEST_CONFIG_FILES


def check_file_edit(tool: str, inp: dict) -> None:
    p = rel(inp.get("file_path") or inp.get("notebook_path") or "")
    if p is None:
        return
    new_text = " ".join(
        str(inp.get(k, "")) for k in ("content", "new_string", "new_source")
    ) + " ".join(str(e.get("new_string", "")) for e in inp.get("edits", []) or [])
    old_text = str(inp.get("old_string", "")) + " ".join(
        str(e.get("old_string", "")) for e in inp.get("edits", []) or []
    )

    # Hook machinery is never editable by Claude (except the escalation note).
    if p.parts[:2] == (".claude", "state") and p != ESCALATION.relative_to(PROJECT):
        deny("Hook state files are managed by the hooks; do not edit them.")
    if p.parts[:2] == (".claude", "hooks") or p == Path(".claude/settings.json"):
        deny("Hook scripts and .claude/settings.json are protected; ask the user to change them.")

    if is_test_path(p):
        added_skips = len(SKIP_PATTERN.findall(new_text)) > len(SKIP_PATTERN.findall(old_text))
        if added_skips and tool != "Write":
            deny("Do not skip/xfail tests. Fix the code, or escalate to the user.")
        if tool == "Write" and SKIP_PATTERN.search(new_text):
            existing = (PROJECT / p).read_text() if (PROJECT / p).exists() else ""
            if len(SKIP_PATTERN.findall(new_text)) > len(SKIP_PATTERN.findall(existing)):
                deny("Do not skip/xfail tests. Fix the code, or escalate to the user.")

    if not is_locked():
        return
    if is_test_path(p):
        deny(LOCKED_MSG)
    if p == Path("pyproject.toml") and touches_pytest_config(tool, old_text, new_text):
        deny(LOCKED_MSG + " (pytest configuration in pyproject.toml is part of the lock.)")


def touches_pytest_config(tool: str, old_text: str, new_text: str) -> bool:
    if tool == "Write" or "pytest" in new_text or "pytest" in old_text:
        return True
    pyproject = PROJECT / "pyproject.toml"
    content = pyproject.read_text() if pyproject.exists() else ""
    idx = content.find(old_text.strip()) if old_text.strip() else -1
    if idx < 0:
        return False
    headers = re.findall(r"^\[([^\]]+)\]", content[:idx], flags=re.MULTILINE)
    return bool(headers) and headers[-1].startswith("tool.pytest")


def check_bash(cmd: str) -> None:
    if re.search(r"test_lock\.py\s+unlock", cmd):
        deny("Only the user can unlock tests (they run `! uv run .claude/hooks/test_lock.py unlock`).")
    if re.search(r"\.claude/(state|hooks|settings\.json)", cmd) and not re.search(
        r"test_lock\.py\s+(lock|status|restore)\b", cmd
    ):
        if BASH_WRITE_OPS.search(HARMLESS_REDIRECTS.sub("", cmd)):
            deny("Hook scripts/state are protected from shell modification.")
    if not is_locked():
        return
    touches_tests = re.search(r"(^|[\s'\"=/])tests/|conftest\.py|pytest\.ini|pyproject\.toml", cmd)
    if touches_tests and BASH_WRITE_OPS.search(HARMLESS_REDIRECTS.sub("", cmd)):
        deny(LOCKED_MSG)


def main() -> None:
    event = read_event()
    tool = event.get("tool_name", "")
    inp = event.get("tool_input", {}) or {}
    if tool in {"Edit", "Write", "MultiEdit", "NotebookEdit"}:
        check_file_edit(tool, inp)
    elif tool == "Bash":
        check_bash(str(inp.get("command", "")))


if __name__ == "__main__":
    main()
