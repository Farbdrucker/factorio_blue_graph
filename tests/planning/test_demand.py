"""Tests for reverse-BFS demand propagation."""

from __future__ import annotations

import math

import pytest

from factorio_blue_graph.model.graph import RecipeHypergraph
from factorio_blue_graph.planning.demand import DemandError, expand_demand


@pytest.fixture(scope="module")
def graph() -> RecipeHypergraph:
    return RecipeHypergraph.load_default()


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


def test_iron_gear_simple_chain(graph: RecipeHypergraph) -> None:
    plan = expand_demand("iron-gear-wheel", 1.0, graph)
    assert _close(plan.recipe_crafts["iron-gear-wheel"], 1.0)
    assert _close(plan.recipe_crafts["iron-plate"], 2.0)
    assert _close(plan.raw_inputs["iron-ore"], 2.0)
    assert "copper-ore" not in plan.raw_inputs


def test_green_circuit_fan_in(graph: RecipeHypergraph) -> None:
    plan = expand_demand("electronic-circuit", 1.0, graph)

    # 1 green-circuit/sec needs 3 copper-cable/sec and 1 iron-plate/sec directly.
    assert _close(plan.recipe_crafts["electronic-circuit"], 1.0)
    # copper-cable recipe yields 2 per craft → 1.5 crafts/sec for 3 cables/sec.
    assert _close(plan.recipe_crafts["copper-cable"], 1.5)
    # iron-plate demand = 1 (direct) only — gears aren't on this chain.
    assert _close(plan.recipe_crafts["iron-plate"], 1.0)
    # copper-plate: one per cable craft.
    assert _close(plan.recipe_crafts["copper-plate"], 1.5)
    assert _close(plan.raw_inputs["copper-ore"], 1.5)
    assert _close(plan.raw_inputs["iron-ore"], 1.0)


def test_advanced_circuit_aggregates(graph: RecipeHypergraph) -> None:
    # advanced-circuit consumes copper-cable (4) AND electronic-circuit (2),
    # and electronic-circuit itself consumes copper-cable (3). Per
    # advanced-circuit craft: copper-cable = 4 + 2*3 = 10 units.
    plan = expand_demand("advanced-circuit", 1.0, graph)
    cable_demand_per_sec = plan.item_rates["copper-cable"]
    assert _close(cable_demand_per_sec, 10.0)
    # 10 cables/sec at yield=2 → 5 crafts/sec.
    assert _close(plan.recipe_crafts["copper-cable"], 5.0)


def test_external_input_short_circuits(graph: RecipeHypergraph) -> None:
    # Pinning copper-plate as external should stop expansion there:
    # no copper-plate recipe usage, no copper-ore.
    plan = expand_demand(
        "electronic-circuit",
        1.0,
        graph,
        external_inputs={"copper-plate"},
    )
    assert "copper-plate" not in plan.recipe_crafts
    assert "copper-ore" not in plan.raw_inputs
    assert _close(plan.raw_inputs["copper-plate"], 1.5)


def test_unknown_item_raises(graph: RecipeHypergraph) -> None:
    with pytest.raises(DemandError):
        expand_demand("definitely-not-a-real-item", 1.0, graph)


def test_negative_rate_raises(graph: RecipeHypergraph) -> None:
    with pytest.raises(DemandError):
        expand_demand("iron-plate", -1.0, graph)
