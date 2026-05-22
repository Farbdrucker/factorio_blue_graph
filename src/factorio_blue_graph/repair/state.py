"""Mutable container for the planner IR that the repair loop hands around.

Wraps the dataclasses from Phases 5–7 in a mutable shell so repair
passes can rewrite lists (inserters, belts, poles, chests) without
threading them through a chain of constructors.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from factorio_blue_graph.layout.inserter import InserterResult
from factorio_blue_graph.layout.placement import Placement
from factorio_blue_graph.layout.power import PoleResult
from factorio_blue_graph.layout.routing import RoutingResult
from factorio_blue_graph.model.blueprint import (
    Chest,
    Inserter,
    IOPort,
    Pole,
    PowerSourceResult,
)
from factorio_blue_graph.model.graph import BlockGraph, FlowGraph


@dataclass
class PlanState:
    """Mutable bundle of phase 5–7 outputs plus the new chest/power layers."""

    placement: Placement
    block_graph: BlockGraph
    flow_graph: FlowGraph
    routing: RoutingResult
    inserters: InserterResult
    power: PoleResult
    power_source: PowerSourceResult
    chests: list[Chest] = field(default_factory=list)
    external_ports: list[IOPort] = field(default_factory=list)

    def replace_inserters(self, inserters: tuple[Inserter, ...]) -> None:
        self.inserters = InserterResult(
            inserters=inserters,
            boundary_belts=self.inserters.boundary_belts,
            unresolved=self.inserters.unresolved,
        )

    def add_inserter(self, inserter: Inserter) -> None:
        self.replace_inserters((*self.inserters.inserters, inserter))

    def remove_inserter(self, tile: tuple[int, int]) -> None:
        self.replace_inserters(tuple(i for i in self.inserters.inserters if (i.x, i.y) != tile))

    def add_pole(self, pole: Pole) -> None:
        self.power = PoleResult(
            poles=(*self.power.poles, pole),
            uncovered_machines=self.power.uncovered_machines,
        )


__all__ = ["PlanState"]
