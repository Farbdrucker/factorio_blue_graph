"""Raw-input provisioning check.

For every entry in `FlowGraph.external_in_edges`, there must be a chest
or a routed boundary belt feeding the consumer machine through a sink
inserter. The repair pass emits a `Chest` at the canvas edge plus the
inserter chain when this check fails.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from factorio_blue_graph.planning.lp import FLUID_INPUTS
from factorio_blue_graph.verify.grid import TileGrid
from factorio_blue_graph.verify.report import Issue, Severity, VerifyReport

if TYPE_CHECKING:
    from factorio_blue_graph.layout.placement import Placement
    from factorio_blue_graph.model.blueprint import Chest, IOPort
    from factorio_blue_graph.model.graph import BlockGraph, FlowGraph


def check(
    grid: TileGrid,
    placement: Placement,
    block_graph: BlockGraph,
    flow_graph: FlowGraph,
    external_ports: tuple[IOPort, ...],
    chests: tuple[Chest, ...],
    report: VerifyReport,
) -> None:
    # Index existing chest items.
    chests_by_item: dict[str, list[tuple[int, int]]] = {}
    for chest in chests:
        chests_by_item.setdefault(chest.item_id, []).append((chest.x, chest.y))
    # Also fold in chests already in the grid (e.g. emitted by a prior
    # repair pass that mutated the IR).
    for tile, cell in grid.chests():
        chests_by_item.setdefault(cell.item_id, []).append(tile)

    for ext in flow_graph.external_in_edges:
        # Fluids arrive by pipe, which the v1 planner does not emit —
        # same exemption as MISSING_INSERTER in verify/machines.py.
        if ext.item_id in FLUID_INPUTS:
            continue
        try:
            block = block_graph.block_of(ext.consumer_node)
        except KeyError:
            continue
        placed = placement.blocks.get(block.id)
        if placed is None:
            continue
        # Find the consumer machine.
        mtile = None
        for mid, mx, my in placed.machine_tiles:
            if mid == ext.consumer_node:
                mtile = (mx, my)
                break
        if mtile is None:
            continue
        # Is there a chest holding `ext.item_id` close enough to be reached
        # by a sink inserter (distance ≤ ~5 tiles to allow for a short belt
        # stub)?
        candidates = chests_by_item.get(ext.item_id, [])
        if not candidates:
            report.issues.append(
                Issue(
                    code="STARVED_INPUT",
                    severity=Severity.ERROR,
                    message=(
                        f"machine {ext.consumer_node} needs raw input {ext.item_id} "
                        f"but no source chest exists"
                    ),
                    location=mtile,
                    payload={
                        "machine_id": ext.consumer_node,
                        "block_id": block.id,
                        "item_id": ext.item_id,
                        "rate": ext.rate,
                        "machine_top_left": mtile,
                    },
                )
            )


__all__ = ["check"]
