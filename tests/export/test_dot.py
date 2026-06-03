"""Tests for the Graphviz `.dot` flow-graph export."""

from __future__ import annotations

import pytest

from factorio_blue_graph.export.dot import to_dot
from factorio_blue_graph.model.graph import FlowGraph, RecipeHypergraph
from factorio_blue_graph.planning.demand import expand_demand
from factorio_blue_graph.planning.lp import solve_machine_counts


@pytest.fixture(scope="module")
def small_plan():
    graph = RecipeHypergraph.load_default()
    demand = expand_demand("electronic-circuit", 30.0 / 60.0, graph)
    machine_plan = solve_machine_counts(demand, graph)
    flow_graph = FlowGraph.from_plan(demand, machine_plan, graph)
    return demand, machine_plan, flow_graph, graph


def test_to_dot_structure(small_plan):
    demand, machine_plan, flow_graph, graph = small_plan
    dot = to_dot(demand, machine_plan, flow_graph, graph, label="electronic-circuit")

    assert dot.startswith("digraph factory {")
    assert dot.rstrip().endswith("}")
    # Title from the label.
    assert "flow graph" in dot
    # Output sink node is present.
    assert "doublecircle" in dot
    # At least one machine node id appears.
    sample_node = next(iter(flow_graph.nodes))
    assert f'"{sample_node}"' in dot


def test_to_dot_has_raw_nodes_and_tiers(small_plan):
    demand, machine_plan, flow_graph, graph = small_plan
    dot = to_dot(demand, machine_plan, flow_graph, graph)

    # Every raw input becomes a cylinder ore node.
    for ext in flow_graph.external_in_edges:
        assert f'"raw:{ext.item_id}"' in dot
    if flow_graph.external_in_edges:
        assert "cylinder" in dot

    # Edges are tier-coloured; the smallest per-machine flows land on yellow.
    assert "gold" in dot
    # Edge labels carry rate and lane/tier annotations.
    assert "/s" in dot
    assert "yellow" in dot


def test_to_dot_emits_output_edges(small_plan):
    demand, machine_plan, flow_graph, graph = small_plan
    dot = to_dot(demand, machine_plan, flow_graph, graph)

    target_count = machine_plan.machine_counts["electronic-circuit"]
    assert target_count > 0
    # Target machines now feed a single output merge node, which carries the
    # consolidated belt onward to the sink: exactly one edge into the sink.
    assert dot.count('-> "__output__"') == 1
    merge_id = "merge:out:electronic-circuit"
    assert f'"{merge_id}" -> "__output__"' in dot
    # Every target machine feeds the merge node.
    for i in range(target_count):
        assert f'"electronic-circuit#{i}" -> "{merge_id}"' in dot


def test_to_dot_inserts_merge_nodes(small_plan):
    demand, machine_plan, flow_graph, graph = small_plan
    dot = to_dot(demand, machine_plan, flow_graph, graph)

    # Merge nodes are hexagons tagged with a "merge:" id.
    assert "merge:" in dot
    assert "hexagon" in dot
    # A real channel gets a merge node carrying the consolidated belt.
    ch = flow_graph.channels[0]
    merge_id = f"merge:{ch.item_id}:{ch.producer_recipe}->{ch.consumer_recipe}"
    assert f'"{merge_id}"' in dot
    assert "merge" in dot  # label vocabulary ("... yellow merge")


def test_merge_nodes_replace_direct_edges(small_plan):
    demand, machine_plan, flow_graph, graph = small_plan
    dot = to_dot(demand, machine_plan, flow_graph, graph)

    ch = flow_graph.channels[0]
    merge_id = f"merge:{ch.item_id}:{ch.producer_recipe}->{ch.consumer_recipe}"
    # The old direct producer-instance -> consumer-instance edge is gone.
    assert f'"{ch.producer_recipe}#0" -> "{ch.consumer_recipe}#0"' not in dot
    # Flow now routes through the merge node instead.
    assert f'"{ch.producer_recipe}#0" -> "{merge_id}"' in dot
    assert f'"{merge_id}" -> "{ch.consumer_recipe}#0"' in dot


def test_merge_node_shows_consolidated_belt(small_plan):
    from factorio_blue_graph.layout.tier import assign_tiers

    demand, machine_plan, flow_graph, graph = small_plan
    dot = to_dot(demand, machine_plan, flow_graph, graph)

    assignments = assign_tiers(flow_graph)
    ch = max(flow_graph.channels, key=lambda c: c.rate)
    merge_id = f"merge:{ch.item_id}:{ch.producer_recipe}->{ch.consumer_recipe}"

    # The merge node label carries the consolidated channel rate ...
    line = next(ln for ln in dot.splitlines() if f'"{merge_id}" [' in ln)
    assert f"{ch.rate:.2f}/s" in line
    # ... and its belt tier matches Phase 3's own assignment for the channel.
    tier_name = assignments[ch].tier.name.lower()
    assert tier_name in line
