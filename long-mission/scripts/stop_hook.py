#!/usr/bin/env python3
"""Stop hook for long-mission.

Blocks Claude Code from ending the turn while the mission governor says
CONTINUE. When the governor returns a terminal verdict, the hook allows the
session to stop.

The hook is fail-open: if anything is missing or broken, it allows the stop
and prints a warning instead of trapping the session.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def allow_stop(message: str | None = None) -> int:
    if message:
        print(message, file=sys.stderr)
    return 0


def main() -> int:
    try:
        raw = sys.stdin.read()
        hook_input = json.loads(raw) if raw.strip() else {}
    except Exception as exc:  # noqa: BLE001
        return allow_stop(f"long-mission stop hook: bad hook input: {exc}")

    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    mission_dir = project_dir / ".mission"
    state_path = mission_dir / "state.json"
    if not state_path.exists():
        return allow_stop()
    if (mission_dir / "PAUSE").exists():
        return allow_stop("long-mission: PAUSE exists; allowing stop (mission paused).")

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return allow_stop(f"long-mission stop hook: cannot read {state_path}: {exc}")

    session_id = hook_input.get("session_id")
    recorded = state.get("session_id")
    if recorded and session_id and recorded != session_id:
        return allow_stop()
    if not recorded and session_id:
        state["session_id"] = session_id
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    governor = Path(__file__).with_name("governor.py")
    try:
        proc = subprocess.run(
            [sys.executable, str(governor), "--state", str(state_path)],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(project_dir),
        )
        result = json.loads(proc.stdout)
    except Exception as exc:  # noqa: BLE001
        return allow_stop(f"long-mission stop hook: governor failed: {exc}")

    if result.get("verdict") != "CONTINUE":
        print(
            f"long-mission: {result.get('verdict')} "
            f"({result.get('recommended_outcome')}); allowing stop.",
            file=sys.stderr,
        )
        return 0

    reason = (
        "Mission still active. Read .mission/PROGRESS.md and .mission/state.json, "
        "run the governor, and execute the next control cycle. Do not ask the user. "
        "If you no longer have the long-mission protocol in context, read "
        "$HOME/.claude/skills/long-mission/SKILL.md first. "
        "If the governor returns a terminal verdict, finalize and stop."
    )
    print(
        json.dumps(
            {
                "decision": "block",
                "reason": reason,
                "systemMessage": "long-mission: continuing (governor says CONTINUE)",
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
