"""Tests for Phase 7b power-pole coverage."""

from __future__ import annotations

from factorio_blue_graph.layout import finalize_layout
from factorio_blue_graph.layout.placement import PlacedBlock, Placement
from factorio_blue_graph.layout.power import cover_with_poles
from factorio_blue_graph.layout.routing import RoutingResult
from factorio_blue_graph.model.blueprint import POLE_RADIUS
from factorio_blue_graph.model.graph import (
    Block,
    BlockGraph,
    FlowGraph,
    MachineNode,
)
from factorio_blue_graph.model.recipe import Machine


def _machine() -> Machine:
    return Machine(id="assembling-machine-1", crafting_speed=0.5, footprint=(3, 3))


def _block(block_id: str, members: int = 1) -> Block:
    nodes = tuple(
        MachineNode(id=f"{block_id}-m{i}", recipe_id="r", machine=_machine(), crafts_per_sec=1.0)
        for i in range(members)
    )
    side = 7 if members <= 1 else 10  # matches clustering footprint formula
    return Block(
        id=block_id,
        members=nodes,
        recipes=("r",),
        footprint=(side, side),
        primary_recipe="r",
    )


def _placed_block(block_id: str, x: int, y: int, members: int = 1) -> PlacedBlock:
    import math

    side_n = math.ceil(math.sqrt(members))
    footprint = 7 if members <= 1 else 10
    machine_tiles = tuple(
        (f"{block_id}-m{i}", x + 2 + (i % side_n) * 4, y + 2 + (i // side_n) * 4)
        for i in range(members)
    )
    return PlacedBlock(
        block_id=block_id,
        x=x,
        y=y,
        rotated=False,
        width=footprint,
        height=footprint,
        machine_tiles=machine_tiles,
    )


def _machine_footprint_tiles(pb: PlacedBlock) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for _mid, mx, my in pb.machine_tiles:
        for dx in range(3):
            for dy in range(3):
                out.add((mx + dx, my + dy))
    return out


def _placement(*blocks: PlacedBlock, canvas: tuple[int, int]) -> Placement:
    return Placement(
        blocks={pb.block_id: pb for pb in blocks},
        canvas=canvas,
        bbox=(max(pb.x + pb.width for pb in blocks), max(pb.y + pb.height for pb in blocks)),
        flow_weighted_distance=0.0,
        status="OPTIMAL",
        solver="cp-sat",
    )


def _block_graph(*blocks: Block) -> BlockGraph:
    bg = BlockGraph()
    for b in blocks:
        bg.add_block(b)
    return bg


def test_four_assembler_block_one_pole_at_center() -> None:
    blk = _block("only", members=4)
    bg = _block_graph(blk)
    pb = _placed_block("only", 0, 0, members=4)
    placement = _placement(pb, canvas=(15, 15))
    occupied = frozenset(_machine_footprint_tiles(pb))
    result = cover_with_poles(placement, bg, occupied)
    # PLAN test: a 4-assembler block needs exactly 1 medium pole. With
    # 1-tile inter-machine spacing the four 3×3 machines sit at (2,2),
    # (6,2), (2,6), (6,6); the centre tile (5,5) is free and a pole there
    # covers x∈[2..8] × y∈[2..8] — every machine intersects.
    assert len(result.poles) == 1
    assert result.uncovered_machines == ()
    # The chosen pole tile must not be on a machine footprint.
    pole = result.poles[0]
    assert (pole.x, pole.y) not in occupied
    # And its 7×7 supply area must intersect every machine footprint.
    for _mid, mx, my in pb.machine_tiles:
        assert pole.x - POLE_RADIUS <= mx + 2 and pole.x + POLE_RADIUS >= mx
        assert pole.y - POLE_RADIUS <= my + 2 and pole.y + POLE_RADIUS >= my


def test_single_assembler_one_pole() -> None:
    blk = _block("only", members=1)
    bg = _block_graph(blk)
    pb = _placed_block("only", 0, 0, members=1)
    placement = _placement(pb, canvas=(10, 10))
    occupied = frozenset(_machine_footprint_tiles(pb))
    result = cover_with_poles(placement, bg, occupied)
    assert len(result.poles) == 1
    assert result.uncovered_machines == ()


def test_pole_avoids_occupied_tiles() -> None:
    blk = _block("only", members=1)
    bg = _block_graph(blk)
    pb = _placed_block("only", 0, 0, members=1)
    placement = _placement(pb, canvas=(10, 10))
    # Block every tile within Chebyshev 3 of the assembler centre except
    # one corner; still one pole should suffice on that corner tile.
    forbidden = _machine_footprint_tiles(pb)
    # Also block the obvious central candidates (3..5, 3..5) — force the
    # greedy to pick an off-centre tile that still covers the 3×3 machine.
    forbidden |= {(x, y) for x in range(2, 6) for y in range(2, 6)}
    result = cover_with_poles(placement, bg, frozenset(forbidden))
    assert len(result.poles) == 1
    assert result.uncovered_machines == ()
    pole = result.poles[0]
    assert (pole.x, pole.y) not in forbidden


def test_pole_cover_two_distant_blocks() -> None:
    a = _block("a", members=1)
    b = _block("b", members=1)
    bg = _block_graph(a, b)
    pa = _placed_block("a", 0, 0, members=1)
    pb = _placed_block("b", 20, 0, members=1)
    placement = _placement(pa, pb, canvas=(30, 10))
    occupied = frozenset(_machine_footprint_tiles(pa) | _machine_footprint_tiles(pb))
    result = cover_with_poles(placement, bg, occupied)
    assert len(result.poles) == 2
    assert result.uncovered_machines == ()


def test_power_integration_green_circuit(
    gc_routed: RoutingResult,
    gc_placement: Placement,
    gc_blocks_session: BlockGraph,
    gc_flow: FlowGraph,
) -> None:
    coverage = finalize_layout(gc_placement, gc_blocks_session, gc_flow, gc_routed)
    assert coverage.power.uncovered_machines == ()
    assert len(coverage.power.poles) >= 1
