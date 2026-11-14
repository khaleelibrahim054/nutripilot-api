#!/usr/bin/env python3
"""Freeze the test suite so failing tests can only be fixed by changing application code.

  uv run .claude/hooks/test_lock.py lock     # snapshot tests/ and forbid edits to them
  uv run .claude/hooks/test_lock.py status   # show lock state
  uv run .claude/hooks/test_lock.py restore  # put tests/ back to the locked snapshot
  uv run .claude/hooks/test_lock.py unlock   # HUMAN ONLY (run as `! uv run .claude/hooks/test_lock.py unlock`)
"""

import sys

sys.path.insert(0, __import__("os").path.dirname(__file__))
from _common import ESCALATION, is_locked, lock, restore, tests_tampered, unlock  # noqa: E402


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "lock":
        lock()
        print("Tests locked. tests/, conftest.py and pytest config are now read-only for Claude.")
    elif cmd == "unlock":
        unlock()
        print("Tests unlocked.")
    elif cmd == "restore":
        print("Restored tests/ from locked snapshot." if restore() else "No snapshot to restore.")
    elif cmd == "status":
        state = "LOCKED" if is_locked() else "unlocked"
        if tests_tampered():
            state += " (tests differ from snapshot!)"
        if ESCALATION.exists():
            state += " (escalation pending: .claude/state/escalation.md)"
        print(state)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
