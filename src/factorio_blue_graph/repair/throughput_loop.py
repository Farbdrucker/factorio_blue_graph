"""Throughput-repair driver: simulate → heal → re-simulate.

Mirrors ``repair/loop.py::plan_with_repair`` in shape so the planner has
two parallel loops: a structural one and a throughput one. Each iteration
runs the tick simulator, picks the lowest-priority applicable heal pass,
and re-runs the structural repair loop afterwards to clean up fallout
(re-routing changes occupancy in ways the structural verifier cares about).
"""

from __future__ import annotations

from dataclasses import dataclass

from factorio_blue_graph.model.graph import RecipeHypergraph
from factorio_blue_graph.repair import passes_throughput
from factorio_blue_graph.repair.loop import plan_with_repair
from factorio_blue_graph.repair.state import PlanState
from factorio_blue_graph.simulate import (
    ACHIEVEMENT_THRESHOLD,
    MEASURE_TICKS,
    WARMUP_TICKS,
    SimReport,
    run_simulation,
)

_PASS_TABLE = {
    "DISCONNECTED_FLOW": (passes_throughput.enable_belt_routing, 1),
    "MACHINE_STARVED": (passes_throughput.enable_belt_routing, 2),
    "BELT_SATURATED": (passes_throughput.bump_belt_tier, 3),
    "INSERTER_BOTTLENECK": (passes_throughput.upgrade_inserter, 4),
    "OUTPUT_UNDERDELIVERY": (passes_throughput.respace_and_replace, 9),
}


@dataclass
class ThroughputOutcome:
    state: PlanState
    sim_report: SimReport
    iterations: int
    history: list[tuple[str, int]]
    converged: bool


def plan_with_throughput_repair(
    state: PlanState,
    *,
    target_item: str,
    target_rate_per_sec: float,
    threshold: float = ACHIEVEMENT_THRESHOLD,
    budget: int = 4,
    warmup_ticks: int = WARMUP_TICKS,
    measure_ticks: int = MEASURE_TICKS,
    recipe_graph: RecipeHypergraph | None = None,
) -> ThroughputOutcome:
    if recipe_graph is None:
        recipe_graph = RecipeHypergraph.load_default()

    history: list[tuple[str, int]] = []
    seen_multisets: set[tuple[tuple[str, int], ...]] = set()

    report = run_simulation(
        state,
        target_item=target_item,
        target_rate_per_sec=target_rate_per_sec,
        warmup_ticks=warmup_ticks,
        measure_ticks=measure_ticks,
        recipe_graph=recipe_graph,
    )
    best_report = report
    best_state = state

    if report.achievement_ratio >= threshold and report.ok:
        return ThroughputOutcome(
            state=state,
            sim_report=report,
            iterations=0,
            history=history,
            converged=True,
        )

    for it in range(budget):
        multiset = report.codes_multiset()
        if multiset in seen_multisets:
            break
        seen_multisets.add(multiset)

        by_code = report.by_code()
        chosen = _pick_code(by_code)
        if chosen is None:
            break

        fn, _prio = _PASS_TABLE[chosen]
        fixes = fn(by_code[chosen], state)
        history.append((chosen, fixes))

        if fixes == 0:
            # Mark this code as exhausted by inserting a sentinel into the
            # multiset cache so the next iteration picks a different code.
            seen_multisets.add(_with_extra(multiset, chosen))
            # Try a different code on the same report.
            report = report  # noqa: PLW0127 (explicit no-op)
        else:
            # Layout-mutating passes need a structural sweep before sim.
            plan_with_repair(state, budget=4)

        report = run_simulation(
            state,
            target_item=target_item,
            target_rate_per_sec=target_rate_per_sec,
            warmup_ticks=warmup_ticks,
            measure_ticks=measure_ticks,
            recipe_graph=recipe_graph,
        )

        if report.achievement_ratio > best_report.achievement_ratio:
            best_report = report
            best_state = state

        if report.achievement_ratio >= threshold and report.ok:
            return ThroughputOutcome(
                state=state,
                sim_report=report,
                iterations=it + 1,
                history=history,
                converged=True,
            )

    converged = best_report.achievement_ratio >= threshold and best_report.ok
    return ThroughputOutcome(
        state=best_state,
        sim_report=best_report,
        iterations=len(history),
        history=history,
        converged=converged,
    )


def _pick_code(by_code: dict[str, list]) -> str | None:
    best: tuple[int, str] | None = None
    for code in by_code:
        entry = _PASS_TABLE.get(code)
        if entry is None:
            continue
        _, prio = entry
        if best is None or prio < best[0]:
            best = (prio, code)
    return best[1] if best else None


def _with_extra(multiset: tuple[tuple[str, int], ...], code: str) -> tuple[tuple[str, int], ...]:
    return tuple(sorted((*multiset, (f"_skipped:{code}", 1))))


__all__ = ["ThroughputOutcome", "plan_with_throughput_repair"]
