"""Layout phases (4-7) of the factorio_blue_graph pipeline.

This module exposes the orchestration entrypoint `finalize_layout` that
chains Phase 7's inserter placement and pole coverage on top of the
`Placement` + `RoutingResult` produced by Phases 5-6.
"""

from __future__ import annotations

from dataclasses import dataclass

from factorio_blue_graph.layout.inserter import (
    InserterResult,
    place_inserters,
)
from factorio_blue_graph.layout.placement import Placement
from factorio_blue_graph.layout.power import PoleResult, cover_with_poles
from factorio_blue_graph.layout.routing import RoutingResult
from factorio_blue_graph.model.graph import BlockGraph, FlowGraph

# Footprint assumed for each machine when assembling occupied_tiles. Matches
# the vanilla 1.1 assembler / chemical-plant size.
_MACHINE_FOOTPRINT = (3, 3)


@dataclass(frozen=True)
class CoverageResult:
    """Bundle of Phase 7 outputs ready for Phase 9 blueprint encoding."""

    inserters: InserterResult
    power: PoleResult
    occupied_tiles: frozenset[tuple[int, int]]
    machine_positions: dict[str, tuple[int, int]]


def finalize_layout(
    placement: Placement,
    block_graph: BlockGraph,
    flow_graph: FlowGraph,
    routing_result: RoutingResult,
) -> CoverageResult:
    """Run Phase 7: inserters + power coverage on top of the routed canvas."""
    inserters = place_inserters(placement, block_graph, flow_graph, routing_result)

    machine_positions: dict[str, tuple[int, int]] = {}
    machine_tiles: set[tuple[int, int]] = set()
    for pb in placement.blocks.values():
        block = block_graph.blocks.get(pb.block_id)
        if block is not None and block.members:
            mw = max(m.machine.footprint[0] for m in block.members)
            mh = max(m.machine.footprint[1] for m in block.members)
        else:
            mw, mh = _MACHINE_FOOTPRINT
        for mid, mx, my in pb.machine_tiles:
            machine_positions[mid] = (mx, my)
            for dx in range(mw):
                for dy in range(mh):
                    machine_tiles.add((mx + dx, my + dy))

    belt_tiles: set[tuple[int, int]] = set()
    for path in routing_result.paths:
        belt_tiles.update((s.x, s.y) for s in path.segments)
        belt_tiles.update((u.x, u.y) for u in path.undergrounds)
    for sp in routing_result.splitters:
        belt_tiles.add((sp.x, sp.y))
        # Splitters span two tiles; the second tile is one step right of the
        # facing direction. Direction encoding: 0=N,2=E,4=S,6=W.
        if sp.direction == 0:
            belt_tiles.add((sp.x + 1, sp.y))
        elif sp.direction == 4:
            belt_tiles.add((sp.x - 1, sp.y))
        elif sp.direction == 2:
            belt_tiles.add((sp.x, sp.y + 1))
        elif sp.direction == 6:
            belt_tiles.add((sp.x, sp.y - 1))

    inserter_tiles = {(ins.x, ins.y) for ins in inserters.inserters}
    boundary_tiles = {(b.x, b.y) for b in inserters.boundary_belts}

    occupied = frozenset(machine_tiles | belt_tiles | inserter_tiles | boundary_tiles)
    power = cover_with_poles(placement, block_graph, occupied)

    return CoverageResult(
        inserters=inserters,
        power=power,
        occupied_tiles=occupied,
        machine_positions=machine_positions,
    )


__all__ = [
    "CoverageResult",
    "finalize_layout",
]
