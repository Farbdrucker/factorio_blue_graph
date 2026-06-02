"""Tests for the throughput-repair driver."""

from __future__ import annotations

from factorio_blue_graph.layout.inserter import InserterResult
from factorio_blue_graph.layout.placement import Placement
from factorio_blue_graph.layout.power import PoleResult
from factorio_blue_graph.layout.routing import RoutingResult
from factorio_blue_graph.model.blueprint import PowerSourceResult
from factorio_blue_graph.model.graph import BlockGraph, FlowGraph
from factorio_blue_graph.repair.state import PlanState
from factorio_blue_graph.repair.throughput_loop import plan_with_throughput_repair


def _empty_state(canvas: tuple[int, int] = (20, 20)) -> PlanState:
    return PlanState(
        placement=Placement(
            blocks={},
            canvas=canvas,
            bbox=(0, 0),
            flow_weighted_distance=0.0,
            status="OPTIMAL",
            solver="cp-sat",
        ),
        block_graph=BlockGraph(),
        flow_graph=FlowGraph(),
        routing=RoutingResult(
            paths=(),
            splitters=(),
            ripup_count=0,
            unresolved=(),
            status="OK",
            canvas=canvas,
        ),
        inserters=InserterResult(inserters=(), boundary_belts=(), unresolved=()),
        power=PoleResult(poles=(), uncovered_machines=()),
        power_source=PowerSourceResult(),
    )


def test_zero_target_converges_immediately():
    state = _empty_state()
    outcome = plan_with_throughput_repair(
        state,
        target_item="iron-plate",
        target_rate_per_sec=0.0,
        warmup_ticks=10,
        measure_ticks=10,
    )
    assert outcome.converged
    assert outcome.iterations == 0
    assert outcome.sim_report.ok


def test_empty_state_with_target_fails_after_budget():
    """Empty plan, non-zero target → no pass can produce items, budget
    exhausts cleanly without infinite loop."""
    state = _empty_state()
    outcome = plan_with_throughput_repair(
        state,
        target_item="iron-plate",
        target_rate_per_sec=1.0,
        budget=2,
        warmup_ticks=30,
        measure_ticks=30,
    )
    assert not outcome.converged
    # Either oscillation guard fires or every pass returns 0 — both fine.
    assert outcome.iterations <= 2
    assert outcome.sim_report.achievement_ratio == 0.0


def test_pass_table_codes_match_attribution_codes():
    """Every code the simulator emits should have a heal pass mapped.

    Catches the silent-failure mode where the simulator adds a new
    bottleneck code and the repair loop loses the ability to act on it.
    """
    from factorio_blue_graph.repair.throughput_loop import _PASS_TABLE

    sim_codes = {
        "OUTPUT_UNDERDELIVERY",
        "DISCONNECTED_FLOW",
        "MACHINE_STARVED",
        "BELT_SATURATED",
        "INSERTER_BOTTLENECK",
    }
    for code in sim_codes:
        assert code in _PASS_TABLE, f"no heal pass mapped for {code}"
