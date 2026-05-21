"""Tests for Phase 5 block placement."""

from __future__ import annotations

import pytest

from factorio_blue_graph.layout.clustering import cluster_into_blocks
from factorio_blue_graph.layout.placement import (
    PlacedBlock,
    Placement,
    PlacementError,
    place_blocks,
)
from factorio_blue_graph.model.graph import (
    Block,
    BlockEdge,
    BlockGraph,
    FlowGraph,
    MachineNode,
    RecipeHypergraph,
)
from factorio_blue_graph.model.recipe import Machine
from factorio_blue_graph.planning.demand import expand_demand
from factorio_blue_graph.planning.lp import solve_machine_counts

# -- helpers -----------------------------------------------------------------


def _rect_overlaps(a: PlacedBlock, b: PlacedBlock) -> bool:
    return not (
        a.x + a.width <= b.x
        or b.x + b.width <= a.x
        or a.y + a.height <= b.y
        or b.y + b.height <= a.y
    )


def _all_inside(p: Placement) -> bool:
    W, H = p.canvas
    return all(pb.x >= 0 and pb.x + pb.width <= W for pb in p.blocks.values()) and all(
        pb.y >= 0 and pb.y + pb.height <= H for pb in p.blocks.values()
    )


def _fake_machine() -> Machine:
    return Machine(id="assembling-machine-1", crafting_speed=0.5, footprint=(3, 3))


def _make_block(block_id: str, footprint: tuple[int, int], recipe: str = "r") -> Block:
    node = MachineNode(
        id=f"{block_id}-m0",
        recipe_id=recipe,
        machine=_fake_machine(),
        crafts_per_sec=1.0,
    )
    return Block(
        id=block_id,
        members=(node,),
        recipes=(recipe,),
        footprint=footprint,
        primary_recipe=recipe,
    )


def _make_graph(blocks: list[Block], edges: list[BlockEdge] | None = None) -> BlockGraph:
    bg = BlockGraph()
    for b in blocks:
        bg.add_block(b)
    for e in edges or []:
        bg.add_edge(e)
    return bg


# -- synthetic tests ---------------------------------------------------------


def test_empty_block_graph_returns_empty_placement() -> None:
    p = place_blocks(BlockGraph(), (20, 20))
    assert p.blocks == {}
    assert p.bbox == (0, 0)


def test_single_block_anchored_at_origin() -> None:
    bg = _make_graph([_make_block("b0", (5, 4))])
    p = place_blocks(bg, (20, 20))
    pb = p.blocks["b0"]
    assert (pb.x, pb.y) == (0, 0)
    assert (pb.width, pb.height) == (5, 4)
    assert p.bbox == (5, 4)
    # machine_tiles is populated for the single-block fast path and lands
    # inside the block's interior (BLOCK_MARGIN=2 offset from the corner).
    assert pb.machine_tiles == (("b0-m0", 2, 2),)


def test_two_blocks_do_not_overlap_and_fit_canvas() -> None:
    bg = _make_graph([_make_block("b0", (4, 4)), _make_block("b1", (4, 4))])
    p = place_blocks(bg, (12, 12))
    a, b = p.blocks["b0"], p.blocks["b1"]
    assert not _rect_overlaps(a, b)
    assert _all_inside(p)
    # Both blocks expose their member's tile in the post-CP-SAT placement.
    for pb in (a, b):
        assert len(pb.machine_tiles) == 1
        assert pb.machine_tiles[0][0].endswith("-m0")


def test_non_square_block_must_rotate_to_fit() -> None:
    # 6x2 footprint on a 3-tile-wide canvas — only rotated orientation fits.
    bg = _make_graph([_make_block("b0", (6, 2))])
    p = place_blocks(bg, (3, 10))
    pb = p.blocks["b0"]
    assert pb.rotated is True
    assert (pb.width, pb.height) == (2, 6)
    assert _all_inside(p)
    # Even on the rotated rectangle the machine sits inside the placed block.
    mid, mx, my = pb.machine_tiles[0]
    assert pb.x <= mx and pb.y <= my


def test_canvas_too_small_raises() -> None:
    bg = _make_graph([_make_block("b0", (10, 10))])
    with pytest.raises(PlacementError):
        place_blocks(bg, (5, 5))


def test_source_block_pulled_west_sink_east() -> None:
    a = _make_block("a", (4, 4), recipe="r_a")
    b = _make_block("b", (4, 4), recipe="r_b")
    c = _make_block("c", (4, 4), recipe="r_c")
    edges = [
        BlockEdge(item_id="ab", source_block="a", target_block="b", rate=10.0),
        BlockEdge(item_id="bc", source_block="b", target_block="c", rate=10.0),
    ]
    bg = _make_graph([a, b, c], edges)
    p = place_blocks(bg, (20, 8), beta=2.0, gamma=1.0)
    xa, xb, xc = p.blocks["a"].x, p.blocks["b"].x, p.blocks["c"].x
    assert xa < xb < xc


# -- integration tests -------------------------------------------------------


@pytest.fixture(scope="module")
def recipe_graph() -> RecipeHypergraph:
    return RecipeHypergraph.load_default()


@pytest.fixture(scope="module")
def gc_blocks(recipe_graph: RecipeHypergraph) -> BlockGraph:
    demand = expand_demand("electronic-circuit", 8.0, recipe_graph)
    plan = solve_machine_counts(demand, recipe_graph)
    fg: FlowGraph = FlowGraph.from_plan(demand, plan, recipe_graph)
    return cluster_into_blocks(fg)


def test_green_circuit_placement_fits_canvas(gc_blocks: BlockGraph) -> None:
    # Canvas expanded from 60×60 in Phase 7 to accommodate per-block
    # inter-machine spacing that lets a single medium pole cover a small
    # block; final blueprint-string smoke (PLAN.md line 252) still
    # targets 60×60 once Phases 8-10 land.
    p = place_blocks(gc_blocks, (80, 80), time_limit_s=20.0)
    assert p.status in ("OPTIMAL", "FEASIBLE")
    assert _all_inside(p)
    placed = list(p.blocks.values())
    for i in range(len(placed)):
        for j in range(i + 1, len(placed)):
            assert not _rect_overlaps(placed[i], placed[j])
    # Every block ships a populated machine layout matching its member count.
    for pb in placed:
        block = gc_blocks.blocks[pb.block_id]
        assert len(pb.machine_tiles) == len(block.members)
        for _mid, mx, my in pb.machine_tiles:
            # Machine origins land inside the placed rectangle's interior.
            assert pb.x <= mx and pb.y <= my
            assert mx + 3 <= pb.x + pb.width
            assert my + 3 <= pb.y + pb.height
