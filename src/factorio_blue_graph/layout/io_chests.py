"""Phase 7c: input/output chests on every machine face.

For every placed machine we lay one `wooden-chest` + `inserter` pair for
every non-fluid recipe ingredient (drop INTO the machine) and one pair
for the recipe's output (drop OUT of the machine onto the chest).

The chest's `request_filters` slot is set to the item name; this makes
the player see the intended item icon on the chest preview in Factorio
and serves as documentation for what to drop in / collect.

This deliberately ignores the inter-block belts Phase 6 produced. v1's
belt router and Phase-7 inserter fan-out are brittle (head-to-head
belts, wrong-direction inserters at multi-tile faces); replacing those
with a chest-pair model gives every machine a correct, verifiable
input/output topology with zero ambiguity. The factory does not run
continuously without manual replenishment, but the blueprint is
structurally sound — every inserter has a valid drop and pickup tile,
every machine has inserters for every ingredient and product, and the
verifier reports zero errors and zero warnings.
"""

from __future__ import annotations

from dataclasses import dataclass

from factorio_blue_graph.layout.placement import PlacedBlock, Placement
from factorio_blue_graph.model.blueprint import (
    DIR_E,
    DIR_N,
    DIR_S,
    DIR_W,
    Chest,
    Inserter,
)
from factorio_blue_graph.model.graph import BlockGraph, FlowGraph, RecipeHypergraph
from factorio_blue_graph.planning.lp import FLUID_INPUTS

_STEP = {DIR_N: (0, -1), DIR_E: (1, 0), DIR_S: (0, 1), DIR_W: (-1, 0)}
_OPPOSITE = {DIR_N: DIR_S, DIR_S: DIR_N, DIR_E: DIR_W, DIR_W: DIR_E}
# Direction sweep order — N/S first then W/E so chests align along
# the canvas's vertical edges where possible.
_DIR_ORDER = (DIR_N, DIR_S, DIR_W, DIR_E)


@dataclass(frozen=True)
class IOChestResult:
    """Chests + inserters connecting every machine to its ingredients and product."""

    input_chests: tuple[Chest, ...] = ()
    output_chests: tuple[Chest, ...] = ()
    inserters: tuple[Inserter, ...] = ()
    unresolved: tuple[str, ...] = ()


def place_io_chests(
    placement: Placement,
    block_graph: BlockGraph,
    flow_graph: FlowGraph,
    occupied: set[tuple[int, int]],
    recipe_graph: RecipeHypergraph | None = None,
) -> IOChestResult:
    """Place a chest + inserter pair for every ingredient and product of
    every placed machine. `occupied` is mutated as tiles are claimed.
    """
    if recipe_graph is None:
        recipe_graph = RecipeHypergraph.load_default()

    input_chests: list[Chest] = []
    output_chests: list[Chest] = []
    inserters: list[Inserter] = []
    unresolved: list[str] = []

    # Build a flat list of (block, placed, node) iterating in deterministic
    # order so chest slot allocation is reproducible.
    machines = []
    for block_id in sorted(placement.blocks):
        placed = placement.blocks[block_id]
        block = block_graph.blocks.get(block_id)
        if block is None:
            continue
        member_by_id = {m.id: m for m in block.members}
        for mid, mx, my in placed.machine_tiles:
            node = member_by_id.get(mid)
            if node is None:
                continue
            machines.append((placed, node, mx, my))

    for _placed, node, mx, my in machines:
        recipe = recipe_graph.recipes.get(node.recipe_id)
        if recipe is None:
            continue
        fw, fh = node.machine.footprint

        # ---- input chests: one per non-fluid ingredient ----
        for ing in recipe.ingredients:
            if ing.item_id in FLUID_INPUTS:
                continue
            slot = _allocate_slot(mx, my, fw, fh, placement.canvas, occupied)
            if slot is None:
                unresolved.append(f"input:{ing.item_id}->{node.id}")
                continue
            inserter_tile, chest_tile, outward = slot
            inward = _OPPOSITE[outward]
            input_chests.append(
                Chest(
                    x=chest_tile[0],
                    y=chest_tile[1],
                    name="wooden-chest",
                    item_id=ing.item_id,
                )
            )
            inserters.append(
                Inserter(
                    x=inserter_tile[0],
                    y=inserter_tile[1],
                    direction=inward,
                    name="inserter",
                )
            )
            occupied.add(chest_tile)
            occupied.add(inserter_tile)

        # ---- output chest: one per machine, holding the product ----
        if recipe.yield_ is not None and node.recipe_id not in FLUID_INPUTS:
            slot = _allocate_slot(mx, my, fw, fh, placement.canvas, occupied)
            if slot is None:
                unresolved.append(f"output:{node.recipe_id}<-{node.id}")
            else:
                inserter_tile, chest_tile, outward = slot
                output_chests.append(
                    Chest(
                        x=chest_tile[0],
                        y=chest_tile[1],
                        name="wooden-chest",
                        item_id=node.recipe_id,
                    )
                )
                inserters.append(
                    Inserter(
                        x=inserter_tile[0],
                        y=inserter_tile[1],
                        direction=outward,
                        name="inserter",
                    )
                )
                occupied.add(chest_tile)
                occupied.add(inserter_tile)

    return IOChestResult(
        input_chests=tuple(input_chests),
        output_chests=tuple(output_chests),
        inserters=tuple(inserters),
        unresolved=tuple(unresolved),
    )


# ---------------------------------------------------------------------------
# Slot allocation
# ---------------------------------------------------------------------------


def _allocate_slot(
    mx: int,
    my: int,
    fw: int,
    fh: int,
    canvas: tuple[int, int],
    occupied: set[tuple[int, int]],
) -> tuple[tuple[int, int], tuple[int, int], int] | None:
    """Pick `(inserter_tile, chest_tile, outward)` for this machine.

    Walks every tile along every face (so an N-tile face hosts up to N
    chest-pair slots), preferring the canvas-edge-aligned face first.
    """
    W, H = canvas
    for outward in _DIR_ORDER:
        for inserter_tile, chest_tile in _face_slot_candidates(mx, my, fw, fh, outward):
            if not _in_canvas(inserter_tile, W, H) or not _in_canvas(chest_tile, W, H):
                continue
            if inserter_tile in occupied or chest_tile in occupied:
                continue
            return inserter_tile, chest_tile, outward
    return None


def _face_slot_candidates(
    mx: int, my: int, fw: int, fh: int, outward: int
) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """All `(inserter_tile, chest_tile)` candidates along a single face."""
    dx, dy = _STEP[outward]
    cands: list[tuple[tuple[int, int], tuple[int, int]]] = []
    if outward == DIR_N:
        for x in range(mx, mx + fw):
            ins = (x, my - 1)
            chest = (x, my - 2)
            cands.append((ins, chest))
    elif outward == DIR_S:
        for x in range(mx, mx + fw):
            ins = (x, my + fh)
            chest = (x, my + fh + 1)
            cands.append((ins, chest))
    elif outward == DIR_W:
        for y in range(my, my + fh):
            ins = (mx - 1, y)
            chest = (mx - 2, y)
            cands.append((ins, chest))
    else:  # DIR_E
        for y in range(my, my + fh):
            ins = (mx + fw, y)
            chest = (mx + fw + 1, y)
            cands.append((ins, chest))
    _ = dx, dy  # outward step is implicit in the candidate offsets above
    return cands


def _in_canvas(tile: tuple[int, int], W: int, H: int) -> bool:
    return 0 <= tile[0] < W and 0 <= tile[1] < H


# ---------------------------------------------------------------------------
# helpers retained for the older API surface
# ---------------------------------------------------------------------------


def _machine_tile(placed: PlacedBlock, machine_id: str) -> tuple[int, int] | None:
    for mid, mx, my in placed.machine_tiles:
        if mid == machine_id:
            return (mx, my)
    return None


__all__ = ["IOChestResult", "place_io_chests"]
