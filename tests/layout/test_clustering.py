"""Tests for the Phase 4 Louvain block clustering."""

from __future__ import annotations

import math

import pytest

from factorio_blue_graph.layout.clustering import (
    BLOCK_MARGIN,
    cluster_into_blocks,
)
from factorio_blue_graph.model.graph import FlowGraph, RecipeHypergraph
from factorio_blue_graph.planning.demand import expand_demand
from factorio_blue_graph.planning.lp import solve_machine_counts


@pytest.fixture(scope="module")
def graph() -> RecipeHypergraph:
    return RecipeHypergraph.load_default()


@pytest.fixture(scope="module")
def gc_flow(graph: RecipeHypergraph) -> FlowGraph:
    # assembling-machine-1 yields 1 EC/sec → rate=8 ⇒ exactly 8 GC assemblers.
    demand = expand_demand("electronic-circuit", 8.0, graph)
    plan = solve_machine_counts(demand, graph)
    return FlowGraph.from_plan(demand, plan, graph)


def test_eight_green_circuit_assemblers_collapse_to_one_block(
    gc_flow: FlowGraph,
) -> None:
    gc_nodes = gc_flow.nodes_for_recipe("electronic-circuit")
    assert len(gc_nodes) == 8, "fixture must produce 8 GC assemblers"

    bg = cluster_into_blocks(gc_flow)
    block = bg.block_of(gc_nodes[0].id)
    assert block.size == 8
    assert block.recipes == ("electronic-circuit",)
    assert block.is_single_recipe
    assert block.primary_recipe == "electronic-circuit"
    # All 8 GC machines land in the same block.
    assert all(bg.block_of(n.id).id == block.id for n in gc_nodes)


def test_footprint_is_square_pack_plus_margin(gc_flow: FlowGraph) -> None:
    bg = cluster_into_blocks(gc_flow)
    gc_block = bg.block_of(gc_flow.nodes_for_recipe("electronic-circuit")[0].id)
    # 8 assemblers, footprint 3×3 each → ceil(sqrt(8))=3, side=3
    side = 3
    mw = mh = 3
    expected = (side * mw + 2 * BLOCK_MARGIN, side * mh + 2 * BLOCK_MARGIN)
    assert gc_block.footprint == expected == (13, 13)


def test_max_block_size_caps_chunk(gc_flow: FlowGraph) -> None:
    bg = cluster_into_blocks(gc_flow, max_block_size=4)
    for block in bg.blocks.values():
        assert block.size <= 4

    gc_nodes = gc_flow.nodes_for_recipe("electronic-circuit")
    gc_block_ids = {bg.block_of(n.id).id for n in gc_nodes}
    # 8 machines split into ceil(8/4)=2 blocks; each block is purely GC.
    assert len(gc_block_ids) == 2
    for bid in gc_block_ids:
        b = bg.blocks[bid]
        assert b.size == 4
        assert b.recipes == ("electronic-circuit",)


def test_every_machine_assigned_to_exactly_one_block(gc_flow: FlowGraph) -> None:
    bg = cluster_into_blocks(gc_flow)
    seen: set[str] = set()
    for block in bg.blocks.values():
        for m in block.members:
            assert m.id not in seen, f"{m.id} appears in multiple blocks"
            seen.add(m.id)
    assert seen == set(gc_flow.nodes)
    assert bg.total_machines == gc_flow.total_machines


def test_block_edges_conserve_inter_block_flow(gc_flow: FlowGraph) -> None:
    bg = cluster_into_blocks(gc_flow)

    expected: dict[tuple[str, str, str], float] = {}
    for u, v, data in gc_flow.graph.edges(data=True):
        bu = bg.block_of(u).id
        bv = bg.block_of(v).id
        if bu == bv:
            continue
        key = (bu, bv, data["item"])
        expected[key] = expected.get(key, 0.0) + data["rate"]

    actual = {(e.source_block, e.target_block, e.item_id): e.rate for e in bg.edges}
    assert set(actual) == set(expected)
    for key, rate in expected.items():
        assert math.isclose(actual[key], rate, rel_tol=1e-9, abs_tol=1e-9), key


def test_empty_flow_graph_yields_empty_block_graph() -> None:
    bg = cluster_into_blocks(FlowGraph())
    assert bg.block_count == 0
    assert bg.edges == []
    assert bg.graph.number_of_nodes() == 0


def test_determinism(gc_flow: FlowGraph) -> None:
    bg_a = cluster_into_blocks(gc_flow, random_state=42)
    bg_b = cluster_into_blocks(gc_flow, random_state=42)

    membership_a = {m.id: bid for bid, blk in bg_a.blocks.items() for m in blk.members}
    membership_b = {m.id: bid for bid, blk in bg_b.blocks.items() for m in blk.members}
    assert membership_a == membership_b


def test_invalid_max_block_size() -> None:
    with pytest.raises(ValueError):
        cluster_into_blocks(FlowGraph(), max_block_size=0)
