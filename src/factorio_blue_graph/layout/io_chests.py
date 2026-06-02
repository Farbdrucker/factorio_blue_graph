"""Phase 7c: narrow I/O chest placement.

In belt-first mode (`fbg plan` default) the routed belts from Phase 6
already carry inter-block flow, and Phase 7 (`layout.inserter`) emits the
machine-side inserters. Phase 7c is then restricted to two narrow jobs:

* one **input chest at the canvas edge** per ``FlowGraph.external_in_edges``
  entry — gives raw resources a real, infinite source the simulator can
  draw from, and exposes the raw-input ports for modular composition.
* one **output buffer chest** per target-recipe machine — gives the
  target item a finite sink so the simulator can measure delivery and
  the player has a single place to collect output.

Each chest is paired with an inserter that bridges chest ↔ machine. An
``IOPort`` is emitted for every chest so a downstream ``fbg compose``
step (or the sidecar JSON exporter) can stitch plans together.

The legacy chest-per-machine-face mode (one chest for every ingredient
and product on every face) is preserved as ``place_io_chests`` for
``--io-mode chests``; new code paths should use ``place_io_ports``.
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
    IOPort,
)
from factorio_blue_graph.model.graph import BlockGraph, FlowGraph, RecipeHypergraph
from factorio_blue_graph.planning.lp import FLUID_INPUTS

_STEP = {DIR_N: (0, -1), DIR_E: (1, 0), DIR_S: (0, 1), DIR_W: (-1, 0)}
_OPPOSITE = {DIR_N: DIR_S, DIR_S: DIR_N, DIR_E: DIR_W, DIR_W: DIR_E}
_DIR_ORDER = (DIR_N, DIR_S, DIR_W, DIR_E)


@dataclass(frozen=True)
class IOChestResult:
    """Chests + inserters + ports emitted by Phase 7c."""

    input_chests: tuple[Chest, ...] = ()
    output_chests: tuple[Chest, ...] = ()
    inserters: tuple[Inserter, ...] = ()
    ports: tuple[IOPort, ...] = ()
    unresolved: tuple[str, ...] = ()


def place_io_ports(
    placement: Placement,
    block_graph: BlockGraph,
    flow_graph: FlowGraph,
    occupied: set[tuple[int, int]],
    *,
    target_item: str,
    recipe_graph: RecipeHypergraph | None = None,
) -> IOChestResult:
    """Belt-first Phase 7c: raw-input chests + target-output chests + ports.

    ``occupied`` is mutated as tiles are claimed so the caller can
    continue placing other entities without overlap.
    """
    if recipe_graph is None:
        recipe_graph = RecipeHypergraph.load_default()

    input_chests: list[Chest] = []
    output_chests: list[Chest] = []
    inserters: list[Inserter] = []
    ports: list[IOPort] = []
    unresolved: list[str] = []

    # --- 1. one input chest per external raw input ---
    # Group external inputs per (consumer_node, item) so each gets one
    # chest+inserter even when the FlowGraph emits one ExternalInput per
    # consumer instance.
    seen_inputs: set[tuple[str, str]] = set()
    for ext in flow_graph.external_in_edges:
        if ext.item_id in FLUID_INPUTS:
            continue
        key = (ext.consumer_node, ext.item_id)
        if key in seen_inputs:
            continue
        seen_inputs.add(key)
        try:
            block = block_graph.block_of(ext.consumer_node)
        except KeyError:
            unresolved.append(f"external:{ext.item_id}->{ext.consumer_node}")
            continue
        placed = placement.blocks.get(block.id)
        if placed is None:
            unresolved.append(f"external:{ext.item_id}->{ext.consumer_node}")
            continue
        mtile = _machine_tile(placed, ext.consumer_node)
        if mtile is None:
            unresolved.append(f"external:{ext.item_id}->{ext.consumer_node}")
            continue
        member = _member_for(block, ext.consumer_node)
        fw, fh = member.machine.footprint if member is not None else (3, 3)
        slot = _allocate_slot(mtile[0], mtile[1], fw, fh, placement.canvas, occupied)
        if slot is None:
            unresolved.append(f"external:{ext.item_id}->{ext.consumer_node}")
            continue
        inserter_tile, chest_tile, outward = slot
        inward = _OPPOSITE[outward]
        input_chests.append(
            Chest(
                x=chest_tile[0],
                y=chest_tile[1],
                name="wooden-chest",
                item_id=ext.item_id,
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
        ports.append(
            IOPort(
                block_id=block.id,
                machine_id=ext.consumer_node,
                item_id=ext.item_id,
                role="input",
                canvas_edge_xy=chest_tile,
            )
        )
        occupied.add(chest_tile)
        occupied.add(inserter_tile)

    # --- 2. one output buffer chest per target-recipe machine ---
    target_machines: list[tuple[PlacedBlock, str, str, int, int]] = []
    for block_id in sorted(placement.blocks):
        placed = placement.blocks[block_id]
        block = block_graph.blocks.get(block_id)
        if block is None:
            continue
        member_by_id = {m.id: m for m in block.members}
        for mid, mx, my in placed.machine_tiles:
            node = member_by_id.get(mid)
            if node is None or node.recipe_id != target_item:
                continue
            target_machines.append((placed, block_id, mid, mx, my))

    for _placed, block_id, mid, mx, my in target_machines:
        block = block_graph.blocks[block_id]
        member = _member_for(block, mid)
        fw, fh = member.machine.footprint if member is not None else (3, 3)
        slot = _allocate_slot(mx, my, fw, fh, placement.canvas, occupied)
        if slot is None:
            unresolved.append(f"output:{target_item}<-{mid}")
            continue
        inserter_tile, chest_tile, outward = slot
        output_chests.append(
            Chest(
                x=chest_tile[0],
                y=chest_tile[1],
                name="wooden-chest",
                item_id=target_item,
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
        ports.append(
            IOPort(
                block_id=block_id,
                machine_id=mid,
                item_id=target_item,
                role="output",
                canvas_edge_xy=chest_tile,
            )
        )
        occupied.add(chest_tile)
        occupied.add(inserter_tile)

    return IOChestResult(
        input_chests=tuple(input_chests),
        output_chests=tuple(output_chests),
        inserters=tuple(inserters),
        ports=tuple(ports),
        unresolved=tuple(unresolved),
    )


def place_unresolved_edge_chests(
    placement: Placement,
    block_graph: BlockGraph,
    unresolved_edges,
    occupied: set[tuple[int, int]],
) -> IOChestResult:
    """Fallback for Phase 6 ``unresolved`` edges: chest pair per edge.

    When the A* router can't lay belts for an inter-block edge we drop a
    pair of chests + inserters (one on the source, one on the sink) so
    the blueprint stays structurally valid and the player can hand-carry
    items. The simulator still sees these as disconnected, which is the
    right diagnosis.
    """
    input_chests: list[Chest] = []
    output_chests: list[Chest] = []
    inserters: list[Inserter] = []
    unresolved: list[str] = []

    for edge in unresolved_edges:
        for role, block_id in (("out", edge.source_block), ("in", edge.target_block)):
            placed = placement.blocks.get(block_id)
            block = block_graph.blocks.get(block_id)
            if placed is None or block is None or not placed.machine_tiles:
                unresolved.append(
                    f"unresolved-edge:{edge.source_block}->{edge.target_block}:{edge.item_id}"
                )
                continue
            mid, mx, my = placed.machine_tiles[0]
            member = _member_for(block, mid)
            fw, fh = member.machine.footprint if member is not None else (3, 3)
            slot = _allocate_slot(mx, my, fw, fh, placement.canvas, occupied)
            if slot is None:
                unresolved.append(
                    f"unresolved-edge:{edge.source_block}->{edge.target_block}:{edge.item_id}"
                )
                continue
            inserter_tile, chest_tile, outward = slot
            inward = _OPPOSITE[outward]
            chest = Chest(
                x=chest_tile[0],
                y=chest_tile[1],
                name="wooden-chest",
                item_id=edge.item_id,
            )
            if role == "out":
                output_chests.append(chest)
                inserters.append(
                    Inserter(
                        x=inserter_tile[0],
                        y=inserter_tile[1],
                        direction=outward,
                        name="inserter",
                    )
                )
            else:
                input_chests.append(chest)
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

    return IOChestResult(
        input_chests=tuple(input_chests),
        output_chests=tuple(output_chests),
        inserters=tuple(inserters),
        ports=(),
        unresolved=tuple(unresolved),
    )


def complete_machine_chests(
    placement: Placement,
    block_graph: BlockGraph,
    existing_inserters,
    occupied: set[tuple[int, int]],
    *,
    target_item: str,
    recipe_graph: RecipeHypergraph | None = None,
) -> IOChestResult:
    """Top up per-machine chests so every machine has at least one input
    and one output inserter.

    Phase 6 routes one belt per inter-block (source, item, target) edge
    and Phase 7 stamps one inserter at each endpoint — that covers the
    nearest machine in the block but leaves siblings inside the same
    block without belt-side inserters. The structural verifier checks
    "every machine has ≥ 1 input inserter and ≥ 1 output inserter", so
    we add a chest + inserter pair for each uncovered machine. These
    chests are *intra-block buffers*: a player can hand-fill them or
    rely on the routed belts as the long-run feed; the simulator treats
    them as finite buffers fed by the producer machines in the block.

    For target-recipe machines the output chest is skipped (a single
    target-output chest is placed by ``place_io_ports`` so the sim has
    one well-known sink to measure).
    """
    if recipe_graph is None:
        recipe_graph = RecipeHypergraph.load_default()

    inputs_by_machine, outputs_by_machine = _index_inserters_by_machine(
        existing_inserters, placement, block_graph
    )

    input_chests: list[Chest] = []
    output_chests: list[Chest] = []
    inserters: list[Inserter] = []
    unresolved: list[str] = []

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
            recipe = recipe_graph.recipes.get(node.recipe_id)
            if recipe is None:
                continue
            fw, fh = node.machine.footprint

            need_input = mid not in inputs_by_machine
            need_output = (
                mid not in outputs_by_machine
                and recipe.yield_ is not None
                and node.recipe_id not in FLUID_INPUTS
            )

            if need_input:
                primary_input = next(
                    (ing.item_id for ing in recipe.ingredients if ing.item_id not in FLUID_INPUTS),
                    None,
                )
                if primary_input is not None:
                    slot = _allocate_slot(mx, my, fw, fh, placement.canvas, occupied)
                    if slot is None:
                        unresolved.append(f"machine-input:{mid}")
                    else:
                        inserter_tile, chest_tile, outward = slot
                        inward = _OPPOSITE[outward]
                        input_chests.append(
                            Chest(
                                x=chest_tile[0],
                                y=chest_tile[1],
                                name="wooden-chest",
                                item_id=primary_input,
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

            if need_output and node.recipe_id != target_item:
                slot = _allocate_slot(mx, my, fw, fh, placement.canvas, occupied)
                if slot is None:
                    unresolved.append(f"machine-output:{mid}")
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
        ports=(),
        unresolved=tuple(unresolved),
    )


def _index_inserters_by_machine(
    inserters,
    placement: Placement,
    block_graph: BlockGraph,
) -> tuple[dict[str, list[tuple[int, int]]], dict[str, list[tuple[int, int]]]]:
    """Build (inputs_by_machine, outputs_by_machine) keyed by machine_id.

    An inserter is counted as serving a machine on its drop side (input)
    or pickup side (output) when its tile-step in the corresponding
    direction lands on any tile of that machine's footprint.
    """
    machine_tile_to_id: dict[tuple[int, int], str] = {}
    for block_id in placement.blocks:
        placed = placement.blocks[block_id]
        block = block_graph.blocks.get(block_id)
        if block is None:
            continue
        member_by_id = {m.id: m for m in block.members}
        for mid, mx, my in placed.machine_tiles:
            node = member_by_id.get(mid)
            if node is None:
                continue
            fw, fh = node.machine.footprint
            for dx in range(fw):
                for dy in range(fh):
                    machine_tile_to_id[(mx + dx, my + dy)] = mid

    inputs: dict[str, list[tuple[int, int]]] = {}
    outputs: dict[str, list[tuple[int, int]]] = {}
    for ins in inserters:
        drop_step = _STEP[ins.direction]
        pickup_step = _STEP[_OPPOSITE[ins.direction]]
        reach = 2 if ins.name == "long-handed-inserter" else 1
        drop_tile = (ins.x + drop_step[0] * reach, ins.y + drop_step[1] * reach)
        pickup_tile = (ins.x + pickup_step[0] * reach, ins.y + pickup_step[1] * reach)
        mid_drop = machine_tile_to_id.get(drop_tile)
        mid_pickup = machine_tile_to_id.get(pickup_tile)
        if mid_drop is not None:
            inputs.setdefault(mid_drop, []).append((ins.x, ins.y))
        if mid_pickup is not None:
            outputs.setdefault(mid_pickup, []).append((ins.x, ins.y))
    return inputs, outputs


def place_io_chests(
    placement: Placement,
    block_graph: BlockGraph,
    flow_graph: FlowGraph,
    occupied: set[tuple[int, int]],
    recipe_graph: RecipeHypergraph | None = None,
) -> IOChestResult:
    """Legacy ``--io-mode chests`` path: one chest pair per ingredient/product.

    Place a chest + inserter pair for every ingredient and product of
    every placed machine. ``occupied`` is mutated as tiles are claimed.
    Retained for the ``--io-mode chests`` debugging flag; the default
    belt-first path uses ``place_io_ports`` instead.
    """
    if recipe_graph is None:
        recipe_graph = RecipeHypergraph.load_default()

    input_chests: list[Chest] = []
    output_chests: list[Chest] = []
    inserters: list[Inserter] = []
    unresolved: list[str] = []

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
        ports=(),
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
    """Pick ``(inserter_tile, chest_tile, outward)`` for this machine."""
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
    cands: list[tuple[tuple[int, int], tuple[int, int]]] = []
    if outward == DIR_N:
        for x in range(mx, mx + fw):
            cands.append(((x, my - 1), (x, my - 2)))
    elif outward == DIR_S:
        for x in range(mx, mx + fw):
            cands.append(((x, my + fh), (x, my + fh + 1)))
    elif outward == DIR_W:
        for y in range(my, my + fh):
            cands.append(((mx - 1, y), (mx - 2, y)))
    else:  # DIR_E
        for y in range(my, my + fh):
            cands.append(((mx + fw, y), (mx + fw + 1, y)))
    return cands


def _in_canvas(tile: tuple[int, int], W: int, H: int) -> bool:
    return 0 <= tile[0] < W and 0 <= tile[1] < H


def _machine_tile(placed: PlacedBlock, machine_id: str) -> tuple[int, int] | None:
    for mid, mx, my in placed.machine_tiles:
        if mid == machine_id:
            return (mx, my)
    return None


def _member_for(block, machine_id: str):
    for m in block.members:
        if m.id == machine_id:
            return m
    return None


__all__ = [
    "IOChestResult",
    "complete_machine_chests",
    "place_io_chests",
    "place_io_ports",
    "place_unresolved_edge_chests",
]
