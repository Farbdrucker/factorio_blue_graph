"""Scalar objective helpers shared by Phase 8 (Pareto sweep) and Phase 10.

`footprint` and `throughput` keep one definition in the codebase so the
sweep, the CLI summary tables, and the eventual blueprint export all
agree on what they mean.
"""

from __future__ import annotations

from factorio_blue_graph.layout.placement import Placement
from factorio_blue_graph.layout.routing import RoutingResult
from factorio_blue_graph.planning.demand import DemandPlan


def footprint(placement: Placement) -> int:
    w, h = placement.bbox
    return w * h


def throughput(demand: DemandPlan) -> float:
    return demand.target_rate_per_sec


def belt_length(routing: RoutingResult) -> int:
    total = 0
    for path in routing.paths:
        total += len(path.segments) + len(path.undergrounds)
    return total


__all__ = ["belt_length", "footprint", "throughput"]
