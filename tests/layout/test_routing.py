"""Tests for the Phase 6 belt router."""

from __future__ import annotations

import pytest

from factorio_blue_graph.layout.clustering import cluster_into_blocks
from factorio_blue_graph.layout.placement import (
    PlacedBlock,
    Placement,
    place_blocks,
)
from factorio_blue_graph.layout.routing import (
    BeltPath,
    RoutingResult,
    route_belts,
)
from factorio_blue_graph.layout.tier import BeltTier
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


def _placed(blocks: list[tuple[str, int, int, int, int]], canvas: tuple[int, int]) -> Placement:
    """Build a Placement directly from explicit rectangles, skipping CP-SAT."""
    pbs: dict[str, PlacedBlock] = {}
    for bid, x, y, w, h in blocks:
        pbs[bid] = PlacedBlock(block_id=bid, x=x, y=y, rotated=False, width=w, height=h)
    return Placement(
        blocks=pbs,
        canvas=canvas,
        bbox=(max(p.x + p.width for p in pbs.values()), max(p.y + p.height for p in pbs.values())),
        flow_weighted_distance=0.0,
        status="OPTIMAL",
        solver="cp-sat",
    )


def _all_tiles(path: BeltPath) -> set[tuple[int, int]]:
    out = {(s.x, s.y) for s in path.segments}
    out.update((u.x, u.y) for u in path.undergrounds)
    return out


def _is_inside_machine_core(t: tuple[int, int], placement: Placement) -> bool:
    """True iff `t` lands on a block's inner machine area (1-tile inset).

    The 1-tile boundary ring around each block is FREE for belt routing.
    """
    inset = 1
    for pb in placement.blocks.values():
        if (
            pb.x + inset <= t[0] < pb.x + pb.width - inset
            and pb.y + inset <= t[1] < pb.y + pb.height - inset
        ):
            return True
    return False


# -- synthetic tests ---------------------------------------------------------


def test_single_edge_routes_rectilinearly() -> None:
    placement = _placed(
        [("a", 0, 5, 5, 5), ("b", 12, 5, 5, 5)],
        canvas=(20, 20),
    )
    bg = _make_graph(
        [_make_block("a", (5, 5)), _make_block("b", (5, 5))],
        edges=[BlockEdge(item_id="x", source_block="a", target_block="b", rate=5.0)],
    )

    res = route_belts(placement, bg)
    assert res.status == "OK"
    assert len(res.paths) == 1
    path = res.paths[0]
    assert path.source_block == "a"
    assert path.target_block == "b"
    assert path.tier == BeltTier.YELLOW

    tiles = _all_tiles(path)
    assert tiles, "path must have at least one belt tile"
    # No tile lies inside either block rectangle.
    for t in tiles:
        assert not _is_inside_machine_core(t, placement), f"tile {t} is inside a block"
    # Path tiles are connected step-by-step (no diagonal jumps except via UG).
    pts = [(s.x, s.y) for s in path.segments] + [(u.x, u.y) for u in path.undergrounds]
    assert len(pts) >= 1


def test_two_parallel_flows_no_overlap() -> None:
    placement = _placed(
        [
            ("a", 0, 2, 4, 4),
            ("b", 14, 2, 4, 4),
            ("c", 0, 12, 4, 4),
            ("d", 14, 12, 4, 4),
        ],
        canvas=(20, 20),
    )
    bg = _make_graph(
        [
            _make_block("a", (4, 4), recipe="r0"),
            _make_block("b", (4, 4), recipe="r1"),
            _make_block("c", (4, 4), recipe="r2"),
            _make_block("d", (4, 4), recipe="r3"),
        ],
        edges=[
            BlockEdge(item_id="x", source_block="a", target_block="b", rate=5.0),
            BlockEdge(item_id="y", source_block="c", target_block="d", rate=5.0),
        ],
    )

    res = route_belts(placement, bg)
    assert res.status == "OK"
    assert len(res.paths) == 2

    tiles_per_path = [_all_tiles(p) for p in res.paths]
    overlap = tiles_per_path[0] & tiles_per_path[1]
    assert overlap == set(), f"parallel paths overlap at {overlap}"
    for path_tiles in tiles_per_path:
        for t in path_tiles:
            assert not _is_inside_machine_core(t, placement)


def test_high_flow_picks_blue_tier() -> None:
    placement = _placed(
        [("a", 0, 5, 4, 4), ("b", 10, 5, 4, 4)],
        canvas=(20, 20),
    )
    bg = _make_graph(
        [_make_block("a", (4, 4)), _make_block("b", (4, 4))],
        edges=[BlockEdge(item_id="x", source_block="a", target_block="b", rate=40.0)],
    )

    res = route_belts(placement, bg)
    assert res.status == "OK"
    assert len(res.paths) == 1
    path = res.paths[0]
    assert path.tier == BeltTier.BLUE
    for seg in path.segments:
        assert seg.name == "express-transport-belt"


def test_obstacle_forces_underground() -> None:
    # Source on the west, sink on the east, with a wall block between them
    # that's too tall to walk around without a major detour. The router
    # should drop an underground pair to bridge it.
    placement = _placed(
        [
            ("a", 0, 8, 3, 3),
            ("wall", 6, 0, 3, 18),
            ("b", 14, 8, 3, 3),
        ],
        canvas=(20, 20),
    )
    bg = _make_graph(
        [
            _make_block("a", (3, 3)),
            _make_block("wall", (3, 18), recipe="r_wall"),
            _make_block("b", (3, 3), recipe="r_b"),
        ],
        edges=[BlockEdge(item_id="x", source_block="a", target_block="b", rate=5.0)],
    )

    res = route_belts(placement, bg)
    assert res.status == "OK"
    path = res.paths[0]
    assert len(path.undergrounds) >= 2, (
        "expected at least one underground pair to bridge the wall, "
        f"got undergrounds={path.undergrounds}"
    )
    io_types = {u.io_type for u in path.undergrounds}
    assert io_types == {"input", "output"}


def test_steiner_fanout_emits_splitter() -> None:
    # One source feeding two sinks of the same item should produce one
    # splitter at the branch tile.
    placement = _placed(
        [
            ("a", 0, 8, 3, 3),
            ("b", 14, 2, 4, 4),
            ("c", 14, 12, 4, 4),
        ],
        canvas=(20, 20),
    )
    bg = _make_graph(
        [
            _make_block("a", (3, 3)),
            _make_block("b", (4, 4), recipe="r_b"),
            _make_block("c", (4, 4), recipe="r_c"),
        ],
        edges=[
            BlockEdge(item_id="x", source_block="a", target_block="b", rate=5.0),
            BlockEdge(item_id="x", source_block="a", target_block="c", rate=5.0),
        ],
    )

    res = route_belts(placement, bg)
    assert res.status == "OK"
    assert len(res.paths) == 2
    assert len(res.splitters) == 1
    sp = res.splitters[0]
    assert sp.name == "splitter"


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


def test_full_green_circuit_pipeline_routes(gc_blocks: BlockGraph) -> None:
    placement = place_blocks(gc_blocks, (60, 60), time_limit_s=20.0)
    res: RoutingResult = route_belts(placement, gc_blocks)
    assert res.canvas == (60, 60)
    assert res.status == "OK", f"unresolved edges: {res.unresolved}"
    # Every BlockEdge should be covered by a BeltPath.
    covered = {(p.source_block, p.target_block, p.item_id) for p in res.paths}
    for e in gc_blocks.edges:
        assert (e.source_block, e.target_block, e.item_id) in covered, f"edge {e} not routed"
