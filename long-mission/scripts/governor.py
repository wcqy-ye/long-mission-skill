#!/usr/bin/env python3
"""Deterministic termination governor for long-mission.

Reads .mission/state.json and prints a JSON verdict. The controller MUST stop
when verdict != CONTINUE. This script is intentionally dumb and mechanical:
it is the hard gate the LLM cannot argue with.

Exit code is always 0; parse the JSON on stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def criterion_verified(a: dict[str, Any]) -> bool:
    """A criterion counts only with evidence and an independent evaluator PASS."""
    return (
        bool(a.get("passed"))
        and bool(a.get("evidence"))
        and a.get("evaluator_verdict") == "PASS"
    )


def minimum_viable_achieved(state: dict[str, Any]) -> bool:
    ids = state.get("partial_success", {}).get("minimum_viable_criteria", [])
    if not ids:
        return False
    acceptance = {a.get("id"): a for a in state.get("acceptance", [])}
    return all(criterion_verified(acceptance.get(i, {})) for i in ids)


def evaluate(state: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []
    counters = state.get("counters", {})
    limits = state.get("limits", {})
    budget = state.get("budget", {})
    acceptance = state.get("acceptance", [])
    mandatory = [a for a in acceptance if a.get("mandatory", True)]
    mv_achieved = minimum_viable_achieved(state)

    # 1. Safety always wins.
    stop_file = state.get("safety", {}).get("stop_file", ".mission/AGENT_STOP")
    if os.path.exists(stop_file):
        return verdict("ABORTED", "ABORTED", [f"stop file exists: {stop_file}"])
    if state.get("safety", {}).get("unauthorized_action"):
        return verdict("ABORTED", "ABORTED", ["unauthorized action recorded"])
    if state.get("status") == "aborted":
        return verdict("ABORTED", "ABORTED", ["state marked aborted"])

    # 2. Success requires passed criteria, evidence for every one, an independent
    #    evaluator PASS for every one, and a final clean-state verification that
    #    is itself independently audited with evidence.
    all_passed = bool(mandatory) and all(a.get("passed") for a in mandatory)
    all_verified = bool(mandatory) and all(criterion_verified(a) for a in mandatory)
    fv = state.get("final_verification", {})
    fv_ok = (
        bool(fv.get("passed"))
        and bool(fv.get("clean_state"))
        and bool(fv.get("no_regressions"))
        and fv.get("evaluator_verdict") == "PASS"
        and bool(fv.get("evidence"))
    )
    if all_verified and fv_ok:
        return verdict("SUCCESS", "SUCCESS", ["all mandatory criteria are independently verified with evidence and final verification passed"])
    if not mandatory:
        reasons.append("contract has no mandatory acceptance criteria; define at least one before continuing")
    elif all_passed and not all_verified:
        reasons.append("acceptance marked passed but missing evidence or independent evaluator PASS; evidence gate blocks SUCCESS")
    elif all_verified and not fv_ok:
        reasons.append("final verification incomplete: needs passed, clean_state, no_regressions, evaluator PASS, and evidence")

    # 3. Hard budget.
    started = parse_dt(state.get("started_at"))
    elapsed_hours = ((now - started).total_seconds() / 3600) if started else 0.0
    over_budget: list[str] = []
    max_hours = budget.get("max_hours")
    max_iterations = budget.get("max_iterations")
    max_cost = budget.get("max_cost_usd")
    if max_hours and elapsed_hours >= max_hours:
        over_budget.append(f"elapsed {elapsed_hours:.2f}h >= max_hours {max_hours}")
    if max_iterations and counters.get("iterations", 0) >= max_iterations:
        over_budget.append(f"iterations {counters.get('iterations')} >= max_iterations {max_iterations}")
    if max_cost and counters.get("cost_usd", 0) >= max_cost:
        over_budget.append(f"cost {counters.get('cost_usd')} >= max_cost_usd {max_cost}")
    if over_budget:
        outcome = "PARTIAL" if mv_achieved else "FAILED"
        return verdict("BUDGET_EXHAUSTED", outcome, over_budget + [f"minimum_viable_achieved: {mv_achieved}"])

    # 4. Stagnation / replan thrash.
    no_progress_limit = limits.get("no_progress_limit", 5)
    same_failure_limit = limits.get("same_failure_limit", 3)
    max_replans = limits.get("max_replans", 6)
    if counters.get("consecutive_no_progress", 0) >= no_progress_limit:
        outcome = "PARTIAL" if mv_achieved else "FAILED"
        return verdict("STAGNATED", outcome, [f"no progress for {counters.get('consecutive_no_progress')} cycles", f"minimum_viable_achieved: {mv_achieved}"])
    if counters.get("same_failure_count", 0) >= same_failure_limit:
        outcome = "PARTIAL" if mv_achieved else "FAILED"
        return verdict("STAGNATED", outcome, [f"same failure {counters.get('same_failure_count')} times", f"minimum_viable_achieved: {mv_achieved}"])
    if counters.get("replan_count", 0) > max_replans:
        outcome = "PARTIAL" if mv_achieved else "FAILED"
        return verdict("STAGNATED", outcome, [f"replan_count {counters.get('replan_count')} > max_replans {max_replans}", f"minimum_viable_achieved: {mv_achieved}"])

    # 5. Critical-path blocker.
    blocker_limit = limits.get("critical_blocker_limit", 3)
    if counters.get("critical_blocker_attempts", 0) >= blocker_limit:
        outcome = "PARTIAL" if mv_achieved else "FAILED"
        return verdict("BLOCKED", outcome, [f"critical blocker attempts {counters.get('critical_blocker_attempts')} >= {blocker_limit}", f"minimum_viable_achieved: {mv_achieved}"])

    # 6. Explicit impossibility / blocked-by-user decision.
    if state.get("status") == "impossible":
        return verdict("IMPOSSIBLE", "FAILED", ["state marked impossible"])
    if state.get("status") == "blocked":
        outcome = "PARTIAL" if mv_achieved else "FAILED"
        return verdict("BLOCKED", outcome, ["all remaining work requires a user decision", f"minimum_viable_achieved: {mv_achieved}"])

    return verdict("CONTINUE", None, reasons or ["within budget and limits"], elapsed_hours=elapsed_hours)


def verdict(name: str, outcome: str | None, reasons: list[str], elapsed_hours: float = 0.0) -> dict[str, Any]:
    return {
        "verdict": name,
        "recommended_outcome": outcome,
        "reasons": reasons,
        "elapsed_hours": round(elapsed_hours, 3),
    }


def load_state(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def self_test() -> int:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    base: dict[str, Any] = {
        "started_at": "2026-01-01T08:00:00+00:00",
        "status": "running",
        "budget": {"max_hours": 8, "max_iterations": 100, "max_cost_usd": 50},
        "counters": {},
        "limits": {"no_progress_limit": 5, "same_failure_limit": 3, "critical_blocker_limit": 3, "max_replans": 6},
        "acceptance": [{"id": "A1", "mandatory": True, "passed": False, "evidence": [], "evaluator_verdict": None}],
        "final_verification": {"passed": False, "clean_state": False, "no_regressions": False, "evaluator_verdict": None, "evidence": []},
        "partial_success": {"minimum_viable_criteria": ["A1"]},
        "safety": {"stop_file": "", "unauthorized_action": False},
    }

    def case(name: str, mutate, expect_verdict: str, expect_outcome: str | None = None, expect_reason: str | None = None) -> bool:
        state = json.loads(json.dumps(base))
        mutate(state)
        got = evaluate(state, now)
        ok = got["verdict"] == expect_verdict
        if expect_outcome is not None:
            ok = ok and got["recommended_outcome"] == expect_outcome
        if expect_reason is not None:
            ok = ok and any(expect_reason in r for r in got["reasons"])
        print(("PASS" if ok else "FAIL"), name, "->", got)
        return ok

    results = [
        case("continue", lambda s: None, "CONTINUE"),
        case(
            "contract with no mandatory criteria cannot succeed",
            lambda s: s.update({"acceptance": []}),
            "CONTINUE",
            None,
            "no mandatory acceptance criteria",
        ),
        case(
            "fake completion is blocked by evidence gate",
            lambda s: s["acceptance"][0].update({"passed": True, "evidence": [], "evaluator_verdict": "PASS"}),
            "CONTINUE",
            None,
            "missing evidence or independent evaluator PASS",
        ),
        case(
            "passed with evidence but no independent evaluator is blocked",
            lambda s: s["acceptance"][0].update({"passed": True, "evidence": ["EVIDENCE/a.log"], "evaluator_verdict": None}),
            "CONTINUE",
            None,
            "missing evidence or independent evaluator PASS",
        ),
        case(
            "success needs evidence and final verification",
            lambda s: (
                s["acceptance"][0].update({"passed": True, "evidence": ["EVIDENCE/a.log"], "evaluator_verdict": "PASS"}),
                s["final_verification"].update({
                    "passed": True,
                    "clean_state": True,
                    "no_regressions": True,
                    "evaluator_verdict": "PASS",
                    "evidence": ["EVIDENCE/final.log"],
                }),
            ),
            "SUCCESS",
            "SUCCESS",
        ),
        case(
            "final verification without audit evidence blocks success",
            lambda s: (
                s["acceptance"][0].update({"passed": True, "evidence": ["EVIDENCE/a.log"], "evaluator_verdict": "PASS"}),
                s["final_verification"].update({"passed": True, "clean_state": True, "no_regressions": True}),
            ),
            "CONTINUE",
            None,
            "final verification incomplete",
        ),
        case(
            "stagnation stops",
            lambda s: s["counters"].update({"consecutive_no_progress": 5}),
            "STAGNATED",
            "FAILED",
        ),
        case(
            "same failure stops",
            lambda s: s["counters"].update({"same_failure_count": 3}),
            "STAGNATED",
            "FAILED",
        ),
        case(
            "budget exhaustion with minimum viable is partial",
            lambda s: (
                s["counters"].update({"iterations": 100}),
                s["acceptance"][0].update({"passed": True, "evidence": ["EVIDENCE/a.log"], "evaluator_verdict": "PASS"}),
            ),
            "BUDGET_EXHAUSTED",
            "PARTIAL",
        ),
        case(
            "budget exhaustion without minimum viable fails",
            lambda s: s["counters"].update({"iterations": 100}),
            "BUDGET_EXHAUSTED",
            "FAILED",
        ),
        case(
            "critical blocker stops",
            lambda s: s["counters"].update({"critical_blocker_attempts": 3}),
            "BLOCKED",
            "FAILED",
        ),
        case(
            "replan thrash stops",
            lambda s: s["counters"].update({"replan_count": 7}),
            "STAGNATED",
            "FAILED",
        ),
        case(
            "impossible",
            lambda s: s.update({"status": "impossible"}),
            "IMPOSSIBLE",
            "FAILED",
        ),
        case(
            "blocked on user decision",
            lambda s: s.update({"status": "blocked"}),
            "BLOCKED",
            "FAILED",
        ),
    ]

    # Stop-file abort uses a real temporary file.
    with tempfile.TemporaryDirectory() as tmp:
        stop = Path(tmp) / "AGENT_STOP"
        stop.write_text("stop", encoding="utf-8")
        state = json.loads(json.dumps(base))
        state["safety"]["stop_file"] = str(stop)
        got = evaluate(state, now)
        ok = got["verdict"] == "ABORTED" and got["recommended_outcome"] == "ABORTED"
        print(("PASS" if ok else "FAIL"), "stop file aborts", "->", got)
        results.append(ok)

    return 0 if all(results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="long-mission termination governor")
    parser.add_argument("--state", help="path to .mission/state.json")
    parser.add_argument("--now", help="ISO8601 override for deterministic tests")
    parser.add_argument("--self-test", action="store_true", help="run built-in scenario tests")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if not args.state:
        parser.error("--state is required unless --self-test is used")

    state = load_state(args.state)
    now = parse_dt(args.now) or datetime.now(timezone.utc)
    print(json.dumps(evaluate(state, now), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
