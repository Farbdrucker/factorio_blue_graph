"""Unit tests for individual repair passes."""

from __future__ import annotations

from factorio_blue_graph.layout.inserter import InserterResult
from factorio_blue_graph.layout.placement import Placement
from factorio_blue_graph.layout.power import PoleResult
from factorio_blue_graph.layout.routing import RoutingResult
from factorio_blue_graph.model.blueprint import (
    DIR_N,
    DIR_S,
    Inserter,
    PowerSourceResult,
)
from factorio_blue_graph.model.graph import BlockGraph, FlowGraph
from factorio_blue_graph.repair import passes
from factorio_blue_graph.repair.state import PlanState
from factorio_blue_graph.verify.report import Issue, Severity


def _empty_state(canvas=(20, 20)) -> PlanState:
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


def test_drop_orphan_inserters_removes_named_tiles():
    state = _empty_state()
    state.replace_inserters(
        (
            Inserter(x=2, y=2, direction=DIR_N, name="inserter"),
            Inserter(x=3, y=3, direction=DIR_N, name="inserter"),
        )
    )
    issues = [Issue(code="ORPHAN_INSERTER", severity=Severity.ERROR, message="x", location=(2, 2))]
    removed = passes.drop_orphan_inserters(issues, state)
    assert removed == 1
    assert len(state.inserters.inserters) == 1
    assert state.inserters.inserters[0].x == 3


def test_flip_inserter_direction_uses_payload():
    state = _empty_state()
    state.replace_inserters((Inserter(x=2, y=2, direction=DIR_N, name="inserter"),))
    issues = [
        Issue(
            code="WRONG_INSERTER_DIRECTION",
            severity=Severity.ERROR,
            message="x",
            location=(2, 2),
            payload={"drop_dir": DIR_N},
        )
    ]
    n = passes.flip_inserter_direction(issues, state)
    assert n == 1
    assert state.inserters.inserters[0].direction == DIR_S


def test_emit_steam_cluster_with_no_machines_is_noop():
    state = _empty_state()
    result = passes.emit_steam_cluster([], state)
    # No machines → required_kw = 0 → no engines emitted.
    assert result == 0
    assert state.power_source.steam_engines == ()
