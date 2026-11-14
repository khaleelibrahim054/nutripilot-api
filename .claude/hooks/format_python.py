#!/usr/bin/env python3
"""PostToolUse: auto-format + auto-fix an edited Python file, then report remaining lint issues."""

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import PROJECT, read_event  # noqa: E402


def ruff_cmd() -> list[str]:
    local = PROJECT / ".venv" / "bin" / "ruff"
    if local.exists():
        return [str(local)]
    if shutil.which("ruff"):
        return ["ruff"]
    return ["uv", "run", "--quiet", "ruff"]


def main() -> int:
    event = read_event()
    inp = event.get("tool_input", {}) or {}
    resp = event.get("tool_response", {}) or {}
    path_str = (resp.get("filePath") if isinstance(resp, dict) else None) or inp.get("file_path")
    if not path_str or not path_str.endswith(".py"):
        return 0
    path = Path(path_str).resolve()
    try:
        path.relative_to(PROJECT.resolve())
    except ValueError:
        return 0
    if not path.exists():
        return 0

    ruff = ruff_cmd()
    run = {"cwd": PROJECT, "capture_output": True, "text": True, "timeout": 60}
    subprocess.run([*ruff, "format", str(path)], **run, check=False)
    subprocess.run([*ruff, "check", "--fix", "--quiet", str(path)], **run, check=False)
    result = subprocess.run(
        [*ruff, "check", "--output-format", "concise", str(path)], **run, check=False
    )
    if result.returncode != 0:
        print(
            "Lint issues remain after auto-fix (fix them properly, don't add noqa unless "
            f"justified):\n{result.stdout.strip()}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
