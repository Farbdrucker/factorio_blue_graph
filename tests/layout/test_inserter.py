"""Tests for Phase 7a inserter placement."""

from __future__ import annotations

from factorio_blue_graph.layout.inserter import place_inserters
from factorio_blue_graph.layout.placement import PlacedBlock, Placement
from factorio_blue_graph.layout.routing import (
    RoutingResult,
    route_belts,
)
from factorio_blue_graph.model.graph import (
    Block,
    BlockEdge,
    BlockGraph,
    ExternalInput,
    FlowGraph,
    MachineNode,
)
from factorio_blue_graph.model.recipe import Machine

# Factorio direction enum
_DIR_N, _DIR_E, _DIR_S, _DIR_W = 0, 2, 4, 6
_OPPOSITE = {_DIR_N: _DIR_S, _DIR_S: _DIR_N, _DIR_E: _DIR_W, _DIR_W: _DIR_E}

# ----------------------------- helpers --------------------------------------


def _machine() -> Machine:
    return Machine(id="assembling-machine-1", crafting_speed=0.5, footprint=(3, 3))


def _block(block_id: str, members: int = 1, recipe: str = "r") -> Block:
    nodes = tuple(
        MachineNode(id=f"{block_id}-m{i}", recipe_id=recipe, machine=_machine(), crafts_per_sec=1.0)
        for i in range(members)
    )
    return Block(
        id=block_id,
        members=nodes,
        recipes=(recipe,),
        footprint=(7, 7),
        primary_recipe=recipe,
    )


def _placed(block_id: str, x: int, y: int, w: int, h: int, members: int = 1) -> PlacedBlock:
    """Construct a PlacedBlock with deterministic machine_tiles for tests."""
    # Same layout helper as placement._lay_machines: pack ceil(sqrt(k))
    # square in 3×3 cells starting at (x+2, y+2).
    import math

    side = math.ceil(math.sqrt(members))
    machine_tiles = tuple(
        (f"{block_id}-m{i}", x + 2 + (i % side) * 4, y + 2 + (i // side) * 4)
        for i in range(members)
    )
    return PlacedBlock(
        block_id=block_id,
        x=x,
        y=y,
        rotated=False,
        width=w,
        height=h,
        machine_tiles=machine_tiles,
    )


def _placement(*blocks: PlacedBlock, canvas: tuple[int, int]) -> Placement:
    return Placement(
        blocks={pb.block_id: pb for pb in blocks},
        canvas=canvas,
        bbox=(max(pb.x + pb.width for pb in blocks), max(pb.y + pb.height for pb in blocks)),
        flow_weighted_distance=0.0,
        status="OPTIMAL",
        solver="cp-sat",
    )


def _block_graph(blocks: list[Block], edges: list[BlockEdge]) -> BlockGraph:
    bg = BlockGraph()
    for b in blocks:
        bg.add_block(b)
    for e in edges:
        bg.add_edge(e)
    return bg


def _empty_flow_graph() -> FlowGraph:
    return FlowGraph()


# ----------------------------- tests ----------------------------------------


def test_single_edge_two_blocks_emits_endpoint_inserters() -> None:
    # Two 7×7 blocks side-by-side, edge "src -> tgt" at rate 0.5 item/s
    # — one basic inserter (0.83/s) suffices at each endpoint.
    src = _block("src")
    tgt = _block("tgt")
    bg = _block_graph(
        [src, tgt],
        [BlockEdge(item_id="x", source_block="src", target_block="tgt", rate=0.5)],
    )
    placement = _placement(_placed("src", 0, 0, 7, 7), _placed("tgt", 9, 0, 7, 7), canvas=(20, 10))
    routing = route_belts(placement, bg)
    assert routing.status == "OK"
    result = place_inserters(placement, bg, _empty_flow_graph(), routing)
    assert len(result.inserters) == 2
    assert result.unresolved == ()
    # Source faces east (out of src), sink also faces east (into tgt from
    # its west face) — both drop directions are DIR_E in this horizontal
    # layout.
    drop_dirs = {ins.direction for ins in result.inserters}
    assert drop_dirs == {_DIR_E}


def test_inserter_count_scales_with_high_flow() -> None:
    # At 1.0 items/sec we need a single basic inserter (~0.83/s → ceil=2).
    # Make it BIG: 30 items/sec on a blue belt — base "inserter" can't do
    # it within a 3-tile face, so the placer promotes to a stronger kind.
    src = _block("src")
    tgt = _block("tgt")
    bg = _block_graph(
        [src, tgt],
        [BlockEdge(item_id="x", source_block="src", target_block="tgt", rate=30.0)],
    )
    placement = _placement(_placed("src", 0, 0, 7, 7), _placed("tgt", 9, 0, 7, 7), canvas=(20, 10))
    routing = route_belts(placement, bg)
    assert routing.status == "OK"
    result = place_inserters(placement, bg, _empty_flow_graph(), routing)
    # Every inserter at this endpoint must be a faster kind than yellow.
    fast_names = {"fast-inserter", "stack-inserter"}
    assert all(ins.name in fast_names for ins in result.inserters)


def test_inserter_does_not_overlap_belt_or_machine() -> None:
    src = _block("src")
    tgt = _block("tgt")
    bg = _block_graph(
        [src, tgt],
        [BlockEdge(item_id="x", source_block="src", target_block="tgt", rate=1.0)],
    )
    placement = _placement(_placed("src", 0, 0, 7, 7), _placed("tgt", 9, 0, 7, 7), canvas=(20, 10))
    routing = route_belts(placement, bg)
    result = place_inserters(placement, bg, _empty_flow_graph(), routing)

    belt_tiles: set[tuple[int, int]] = set()
    for path in routing.paths:
        belt_tiles.update((s.x, s.y) for s in path.segments)
        belt_tiles.update((u.x, u.y) for u in path.undergrounds)
    machine_tiles: set[tuple[int, int]] = set()
    for pb in placement.blocks.values():
        for _mid, mx, my in pb.machine_tiles:
            for dx in range(3):
                for dy in range(3):
                    machine_tiles.add((mx + dx, my + dy))

    for ins in result.inserters:
        assert (ins.x, ins.y) not in belt_tiles
        assert (ins.x, ins.y) not in machine_tiles


def test_external_input_creates_boundary_stub() -> None:
    # One block in the middle of the canvas, no inter-block edges, one
    # external input for the single machine inside it. Expected: a
    # boundary BeltSegment at the canvas edge nearest the machine and
    # one inserter between that boundary and the machine.
    blk = _block("only")
    bg = _block_graph([blk], [])
    placement = _placement(_placed("only", 4, 0, 7, 7), canvas=(20, 10))
    fg = FlowGraph()
    # Manually attach a member node and external-input edge.
    fg.nodes[blk.members[0].id] = blk.members[0]
    fg.external_in_edges.append(
        ExternalInput(consumer_node=blk.members[0].id, item_id="iron-plate", rate=1.0)
    )
    # The block graph's member index needs the node registered too — it
    # already is via add_block().
    routing = RoutingResult(
        paths=(),
        splitters=(),
        ripup_count=0,
        unresolved=(),
        status="OK",
        canvas=(20, 10),
    )
    result = place_inserters(placement, bg, fg, routing)
    assert len(result.boundary_belts) == 1
    assert len(result.inserters) >= 1
    # Boundary belt sits on one of the canvas edges.
    b = result.boundary_belts[0]
    assert b.x in (0, 19) or b.y in (0, 9)


def test_inserter_integration_green_circuit(
    gc_routed, gc_placement, gc_blocks_session, gc_flow
) -> None:
    assert gc_routed.status == "OK"
    result = place_inserters(gc_placement, gc_blocks_session, gc_flow, gc_routed)
    # Every routed belt should produce at least 2 inserters (source + sink).
    expected_min = 2 * len(gc_routed.paths)
    assert len(result.inserters) >= expected_min
    # External inputs each contribute at least one boundary belt + inserter.
    if gc_flow.external_in_edges:
        assert len(result.boundary_belts) >= 1
