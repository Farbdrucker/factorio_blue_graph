"""Shared fixtures for the layout test suite."""

from __future__ import annotations

import pytest

from factorio_blue_graph.layout.clustering import cluster_into_blocks
from factorio_blue_graph.layout.placement import Placement, place_blocks
from factorio_blue_graph.layout.routing import RoutingResult, route_belts
from factorio_blue_graph.model.graph import BlockGraph, FlowGraph, RecipeHypergraph
from factorio_blue_graph.planning.demand import expand_demand
from factorio_blue_graph.planning.lp import solve_machine_counts


@pytest.fixture(scope="session")
def recipe_graph_session() -> RecipeHypergraph:
    return RecipeHypergraph.load_default()


@pytest.fixture(scope="session")
def gc_flow(recipe_graph_session: RecipeHypergraph) -> FlowGraph:
    demand = expand_demand("electronic-circuit", 8.0, recipe_graph_session)
    plan = solve_machine_counts(demand, recipe_graph_session)
    return FlowGraph.from_plan(demand, plan, recipe_graph_session)


@pytest.fixture(scope="session")
def gc_blocks_session(gc_flow: FlowGraph) -> BlockGraph:
    return cluster_into_blocks(gc_flow)


@pytest.fixture(scope="session")
def gc_placement(gc_blocks_session: BlockGraph) -> Placement:
    return place_blocks(gc_blocks_session, (80, 80), time_limit_s=20.0)


@pytest.fixture(scope="session")
def gc_routed(gc_blocks_session: BlockGraph, gc_placement: Placement) -> RoutingResult:
    return route_belts(gc_placement, gc_blocks_session)
