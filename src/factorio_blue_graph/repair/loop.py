"""Repair driver: verify → fix → re-verify."""

from __future__ import annotations

from dataclasses import dataclass

from factorio_blue_graph.repair import passes
from factorio_blue_graph.repair.state import PlanState
from factorio_blue_graph.verify import verify_plan
from factorio_blue_graph.verify.report import Severity, VerifyReport

# Code → (pass fn, priority). Lower priority runs first. STARVED_INPUT
# runs before MISSING_INSERTER because the chest pass also emits the
# inserter; running MISSING first would place an inserter that the chest
# pass would then have to align around.
_PASS_TABLE = {
    "ORPHAN_INSERTER": (passes.drop_orphan_inserters, 1),
    "WRONG_INSERTER_DIRECTION": (passes.flip_inserter_direction, 2),
    "STARVED_INPUT": (passes.add_external_source_chests, 3),
    "MISSING_INSERTER": (passes.add_inserters_for_missing, 4),
    "UNPOWERED_MACHINE": (passes.add_pole_for_unpowered, 7),
    "UNPOWERED_INSERTER": (passes.add_pole_for_unpowered, 7),
    "NO_POWER_SOURCE": (passes.emit_steam_cluster, 9),
}


@dataclass
class RepairOutcome:
    state: PlanState
    report: VerifyReport
    iterations: int
    history: list[tuple[str, int]]  # (code chosen, fixes applied)


def plan_with_repair(state: PlanState, *, budget: int = 8) -> RepairOutcome:
    """Iterate verify → repair → verify up to `budget` times.

    Bails when (a) the report is OK, (b) no applicable repair exists for
    the remaining errors, or (c) the issue-code multiset repeats (we'd
    be ping-ponging).
    """
    history: list[tuple[str, int]] = []
    seen_multisets: set[tuple[tuple[str, int], ...]] = set()
    best_report = _run_verify(state)
    best_state = state

    if best_report.ok:
        return RepairOutcome(state=state, report=best_report, iterations=0, history=history)

    for it in range(budget):
        report = _run_verify(state)
        if report.ok:
            return RepairOutcome(state=state, report=report, iterations=it, history=history)

        multiset = report.codes_multiset()
        if multiset in seen_multisets:
            # Oscillation: convert remaining errors to warnings and bail.
            return RepairOutcome(
                state=state,
                report=_downgrade_remaining(report),
                iterations=it,
                history=history,
            )
        seen_multisets.add(multiset)

        by_code = report.by_code()
        chosen = _pick_code(by_code)
        if chosen is None:
            # Errors exist but no pass can address them — return as-is.
            return RepairOutcome(state=state, report=report, iterations=it, history=history)
        fn, _prio = _PASS_TABLE[chosen]
        fixes = fn(by_code[chosen], state)
        history.append((chosen, fixes))
        if fixes == 0:
            # Pass declined to act; avoid an infinite loop.
            return RepairOutcome(state=state, report=report, iterations=it + 1, history=history)

        # Track the best (fewest errors) intermediate state.
        current_err = sum(1 for i in report.issues if i.severity is Severity.ERROR)
        best_err = sum(1 for i in best_report.issues if i.severity is Severity.ERROR)
        if current_err < best_err:
            best_report = report
            best_state = state

    final_report = _run_verify(state)
    if final_report.ok:
        return RepairOutcome(state=state, report=final_report, iterations=budget, history=history)
    # Budget exhausted; return the best snapshot we saw.
    return RepairOutcome(
        state=best_state,
        report=final_report,
        iterations=budget,
        history=history,
    )


def _run_verify(state: PlanState) -> VerifyReport:
    return verify_plan(
        state.placement,
        state.block_graph,
        state.flow_graph,
        state.routing,
        state.inserters,
        state.power,
        state.power_source,
        tuple(state.external_ports),
        tuple(state.chests),
    )


def _pick_code(by_code: dict) -> str | None:
    """Pick the issue code with the lowest pass priority that we know how
    to repair."""
    best: tuple[int, str] | None = None
    for code in by_code:
        entry = _PASS_TABLE.get(code)
        if entry is None:
            continue
        _, prio = entry
        if best is None or prio < best[0]:
            best = (prio, code)
    return best[1] if best else None


def _downgrade_remaining(report: VerifyReport) -> VerifyReport:
    """Demote ERROR severities to WARNING after the loop bails. Used by the
    oscillation guard so the planner still emits a blueprint with a
    descriptive report."""
    from factorio_blue_graph.verify.report import Issue

    downgraded: list[Issue] = []
    for issue in report.issues:
        if issue.severity is Severity.ERROR:
            downgraded.append(
                Issue(
                    code=issue.code,
                    severity=Severity.WARNING,
                    message=f"[unrepairable] {issue.message}",
                    location=issue.location,
                    payload=issue.payload,
                )
            )
        else:
            downgraded.append(issue)
    return VerifyReport(issues=downgraded)


__all__ = ["RepairOutcome", "plan_with_repair"]
