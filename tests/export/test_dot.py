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
    # One edge from each target machine to the output sink.
    assert dot.count('-> "__output__"') == target_count
