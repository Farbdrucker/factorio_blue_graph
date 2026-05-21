"""Tests for the Phase 3 per-machine FlowGraph."""

from __future__ import annotations

import math

import pytest

from factorio_blue_graph.layout.tier import BeltTier, assign_tiers
from factorio_blue_graph.model.graph import FlowGraph, RecipeHypergraph
from factorio_blue_graph.planning.demand import DemandPlan, expand_demand
from factorio_blue_graph.planning.lp import MachinePlan, solve_machine_counts


@pytest.fixture(scope="module")
def graph() -> RecipeHypergraph:
    return RecipeHypergraph.load_default()


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


def test_node_count_matches_plan_total(graph: RecipeHypergraph) -> None:
    demand = expand_demand("electronic-circuit", 1.0, graph)
    plan = solve_machine_counts(demand, graph)
    fg = FlowGraph.from_plan(demand, plan, graph)

    assert fg.total_machines == plan.total_machines
    per_recipe_total = sum(len(fg.nodes_for_recipe(r)) for r in plan.machine_counts)
    assert per_recipe_total == plan.total_machines


def test_per_machine_throughput_equals_share(graph: RecipeHypergraph) -> None:
    demand = expand_demand("electronic-circuit", 1.0, graph)
    plan = solve_machine_counts(demand, graph)
    fg = FlowGraph.from_plan(demand, plan, graph)

    for recipe_id, count in plan.machine_counts.items():
        if count == 0:
            continue
        expected = demand.recipe_crafts[recipe_id] / count
        for node in fg.nodes_for_recipe(recipe_id):
            assert _close(node.crafts_per_sec, expected)


def test_channels_conserve_item_rate(graph: RecipeHypergraph) -> None:
    demand = expand_demand("electronic-circuit", 1.0, graph)
    plan = solve_machine_counts(demand, graph)
    fg = FlowGraph.from_plan(demand, plan, graph)

    per_item: dict[str, float] = {}
    for ch in fg.channels:
        per_item[ch.item_id] = per_item.get(ch.item_id, 0.0) + ch.rate

    for item_id, total in per_item.items():
        assert _close(total, demand.item_rates[item_id]), item_id


def test_bipartite_edge_split(graph: RecipeHypergraph) -> None:
    demand = expand_demand("electronic-circuit", 1.0, graph)
    plan = solve_machine_counts(demand, graph)
    fg = FlowGraph.from_plan(demand, plan, graph)

    for ch in fg.channels:
        prod_count = plan.machine_counts[ch.producer_recipe]
        cons_count = plan.machine_counts[ch.consumer_recipe]
        matching_edges = [
            (u, v, data)
            for u, v, data in fg.graph.edges(data=True)
            if data["item"] == ch.item_id
            and fg.nodes[u].recipe_id == ch.producer_recipe
            and fg.nodes[v].recipe_id == ch.consumer_recipe
        ]
        assert len(matching_edges) == prod_count * cons_count
        edge_sum = sum(data["rate"] for _, _, data in matching_edges)
        assert _close(edge_sum, ch.rate)


def test_raw_input_surfaces_in_external_in_edges(graph: RecipeHypergraph) -> None:
    demand = expand_demand("electronic-circuit", 1.0, graph)
    plan = solve_machine_counts(demand, graph)
    fg = FlowGraph.from_plan(demand, plan, graph)

    iron_ore_inputs = [e for e in fg.external_in_edges if e.item_id == "iron-ore"]
    assert iron_ore_inputs, "iron-ore should appear as an external input"
    # Each iron-ore external edge feeds a node running the iron-plate recipe.
    for edge in iron_ore_inputs:
        assert fg.nodes[edge.consumer_node].recipe_id == "iron-plate"

    # No channels involve raw items as producer side (iron-ore has no producer recipe).
    assert not any(ch.item_id == "iron-ore" for ch in fg.channels)


def test_external_in_edges_conserve_raw_demand(graph: RecipeHypergraph) -> None:
    demand = expand_demand("electronic-circuit", 1.0, graph)
    plan = solve_machine_counts(demand, graph)
    fg = FlowGraph.from_plan(demand, plan, graph)

    per_item: dict[str, float] = {}
    for edge in fg.external_in_edges:
        per_item[edge.item_id] = per_item.get(edge.item_id, 0.0) + edge.rate

    for item_id, total in per_item.items():
        assert _close(total, demand.item_rates[item_id]), item_id


def test_empty_plan_yields_empty_graph(graph: RecipeHypergraph) -> None:
    demand = DemandPlan(
        target_item="electronic-circuit",
        target_rate_per_sec=0.0,
        item_rates={},
        recipe_crafts={},
        raw_inputs={},
    )
    plan = MachinePlan()
    fg = FlowGraph.from_plan(demand, plan, graph)
    assert fg.total_machines == 0
    assert fg.channels == []
    assert fg.external_in_edges == []
    assert fg.graph.number_of_nodes() == 0
    assert fg.graph.number_of_edges() == 0


def test_green_circuit_tier_assignment_is_yellow(graph: RecipeHypergraph) -> None:
    demand = expand_demand("electronic-circuit", 1.0, graph)
    plan = solve_machine_counts(demand, graph)
    fg = FlowGraph.from_plan(demand, plan, graph)
    tiers = assign_tiers(fg)

    cu_cable = next(
        ch
        for ch in fg.channels
        if ch.item_id == "copper-cable" and ch.consumer_recipe == "electronic-circuit"
    )
    assignment = tiers[cu_cable]
    assert assignment.tier is BeltTier.YELLOW
    assert assignment.lane_count == 1


def test_in_and_out_edges_helpers(graph: RecipeHypergraph) -> None:
    demand = expand_demand("electronic-circuit", 1.0, graph)
    plan = solve_machine_counts(demand, graph)
    fg = FlowGraph.from_plan(demand, plan, graph)

    circuit_nodes = fg.nodes_for_recipe("electronic-circuit")
    assert circuit_nodes
    consumer = circuit_nodes[0].id
    ins = fg.in_edges(consumer)
    items_in = {item for _, item, _ in ins}
    assert "iron-plate" in items_in
    assert "copper-cable" in items_in

    cable_nodes = fg.nodes_for_recipe("copper-cable")
    assert cable_nodes
    producer = cable_nodes[0].id
    outs = fg.out_edges(producer)
    assert all(item == "copper-cable" for _, item, _ in outs)
