#!/usr/bin/env python3
"""Stop hook: the "done means green" gate.

When Claude tries to finish a turn and code has changed since the last green run:
  1. verify locked tests were not tampered with
  2. run ruff (lint + format check) and the full pytest suite
  3. all green  -> record it, release the test lock, allow stop
     any red    -> lock the tests (if not already), block the stop and feed the failures back
                   so Claude fixes the APPLICATION code and tries again.
Gives up after MAX_FIX_ATTEMPTS consecutive red runs, or when Claude writes an escalation note
explaining why a test itself looks wrong, and hands the decision to the user.
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import (  # noqa: E402
    ATTEMPTS,
    ESCALATION,
    LAST_GREEN,
    MAX_FIX_ATTEMPTS,
    PROJECT,
    STATE,
    code_hash,
    emit,
    is_locked,
    lock,
    read_event,
    tests_tampered,
    unlock,
)

MAX_OUTPUT_LINES = 160


def tool(*args: str) -> list[str]:
    local = PROJECT / ".venv" / "bin" / args[0]
    return [str(local), *args[1:]] if local.exists() else ["uv", "run", "--quiet", *args]


def run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        cmd, cwd=PROJECT, capture_output=True, text=True, timeout=540, check=False
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def tail(text: str, n: int = MAX_OUTPUT_LINES) -> str:
    lines = text.splitlines()
    return "\n".join(lines if len(lines) <= n else ["... (truncated) ...", *lines[-n:]])


def attempts(delta: int | None = None) -> int:
    current = int(ATTEMPTS.read_text()) if ATTEMPTS.exists() else 0
    if delta is None:
        ATTEMPTS.unlink(missing_ok=True)
        return 0
    current += delta
    ATTEMPTS.write_text(str(current))
    return current


def main() -> int:
    read_event()
    STATE.mkdir(parents=True, exist_ok=True)

    if ESCALATION.exists():
        emit(
            {
                "systemMessage": "⚠️ Claude escalated a test it believes is wrong - review "
                ".claude/state/escalation.md. Tests stay locked until you run "
                "`! uv run .claude/hooks/test_lock.py unlock`."
            }
        )
        return 0

    if tests_tampered():
        emit(
            {
                "decision": "block",
                "reason": "Locked test files were modified. Run "
                "`uv run .claude/hooks/test_lock.py restore` to put them back, then fix the "
                "application code instead.",
            }
        )
        return 0

    fingerprint = code_hash()
    if LAST_GREEN.exists() and LAST_GREEN.read_text().strip() == fingerprint:
        # Code is identical to a state already verified green (e.g. a fix that reverted a bug).
        if is_locked() or ATTEMPTS.exists():
            unlock()
            attempts(None)
            emit({"systemMessage": "✅ Quality gate green (matches last verified run). "
                  "Test lock released."})
        return 0

    failures: list[str] = []
    code, out = run(tool("ruff", "check", ".", "--output-format", "concise"))
    if code != 0:
        failures.append(f"## ruff check failed\n{tail(out, 60)}")
    code, out = run(tool("ruff", "format", "--check", "."))
    if code != 0:
        failures.append(f"## ruff format --check failed (run `uv run ruff format .`)\n{tail(out, 30)}")
    code, test_out = run(tool("pytest", "-q", "--no-header", "-p", "no:cacheprovider"))
    if code == 5:
        failures.append("## pytest collected no tests - every feature needs tests.")
    elif code != 0:
        failures.append(f"## pytest failed\n{tail(test_out)}")

    if not failures:
        LAST_GREEN.write_text(code_hash())
        attempts(None)
        was_locked = is_locked()
        unlock()
        summary = test_out.splitlines()[-1] if test_out else "tests passed"
        emit({"systemMessage": f"✅ Quality gate green: {summary}; lint clean."
              + (" Test lock released." if was_locked else "")})
        return 0

    n = attempts(1)
    if not is_locked():
        lock()
    if n > MAX_FIX_ATTEMPTS:
        emit(
            {
                "systemMessage": f"❌ Quality gate still red after {MAX_FIX_ATTEMPTS} fix attempts. "
                "Tests remain locked; review the failures and decide (unlock with "
                "`! uv run .claude/hooks/test_lock.py unlock`)."
            }
        )
        attempts(None)
        return 0

    emit(
        {
            "decision": "block",
            "reason": (
                f"Quality gate RED (fix attempt {n}/{MAX_FIX_ATTEMPTS}). Tests are now LOCKED.\n"
                "Diagnose the root cause and fix the APPLICATION code (app/, migrations/) so the "
                "tests pass. Do not modify, skip or weaken tests. If a test truly contradicts the "
                "real-world requirement, write your evidence to .claude/state/escalation.md and "
                "stop.\n\n" + "\n\n".join(failures)
            ),
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
