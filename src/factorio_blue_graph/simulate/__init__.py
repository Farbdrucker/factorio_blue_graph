"""Phase 8.6: discrete-tick throughput simulator.

Public entry point: ``run_simulation(state, target_item, target_rate_per_sec)``
returns a ``SimReport`` whose ``ok`` flag is the planner's signal that a
blueprint actually delivers the requested throughput.
"""

from __future__ import annotations

from factorio_blue_graph.model.graph import RecipeHypergraph
from factorio_blue_graph.repair.state import PlanState
from factorio_blue_graph.simulate.constants import (
    ACHIEVEMENT_THRESHOLD,
    MEASURE_TICKS,
    WARMUP_TICKS,
)
from factorio_blue_graph.simulate.report import Bottleneck, SimReport
from factorio_blue_graph.simulate.simulator import Simulator
from factorio_blue_graph.simulate.topology import Topology, build_topology


def run_simulation(
    state: PlanState,
    *,
    target_item: str,
    target_rate_per_sec: float,
    warmup_ticks: int = WARMUP_TICKS,
    measure_ticks: int = MEASURE_TICKS,
    recipe_graph: RecipeHypergraph | None = None,
) -> SimReport:
    if recipe_graph is None:
        recipe_graph = RecipeHypergraph.load_default()
    topology = build_topology(state, recipe_graph, target_item=target_item)
    sim = Simulator(
        topology,
        target_item=target_item,
        target_rate_per_sec=target_rate_per_sec,
    )
    return sim.run(warmup_ticks=warmup_ticks, measure_ticks=measure_ticks)


__all__ = [
    "ACHIEVEMENT_THRESHOLD",
    "Bottleneck",
    "MEASURE_TICKS",
    "SimReport",
    "Simulator",
    "Topology",
    "WARMUP_TICKS",
    "build_topology",
    "run_simulation",
]
