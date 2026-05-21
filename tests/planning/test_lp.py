"""Tests for the Phase 2 machine-count MILP."""

from __future__ import annotations

import math

import pytest

from factorio_blue_graph.model.graph import RecipeHypergraph
from factorio_blue_graph.planning.demand import DemandPlan, expand_demand
from factorio_blue_graph.planning.lp import (
    ASSEMBLER_1,
    CHEMICAL_PLANT,
    STONE_FURNACE,
    MachinePlanError,
    infer_machine,
    solve_machine_counts,
)


@pytest.fixture(scope="module")
def graph() -> RecipeHypergraph:
    return RecipeHypergraph.load_default()


def _expected_count(crafts_per_sec: float, time: float, speed: float) -> int:
    return math.ceil(crafts_per_sec * time / speed)


def test_infer_machine_furnace_vs_assembler_vs_chem(graph: RecipeHypergraph) -> None:
    assert infer_machine(graph.recipes["iron-plate"]) is STONE_FURNACE
    assert infer_machine(graph.recipes["copper-plate"]) is STONE_FURNACE
    assert infer_machine(graph.recipes["iron-gear-wheel"]) is ASSEMBLER_1
    assert infer_machine(graph.recipes["electronic-circuit"]) is ASSEMBLER_1
    assert infer_machine(graph.recipes["sulfur"]) is CHEMICAL_PLANT
    assert infer_machine(graph.recipes["plastic-bar"]) is CHEMICAL_PLANT


def test_green_circuit_chain_counts(graph: RecipeHypergraph) -> None:
    demand = expand_demand("electronic-circuit", 1.0, graph)
    plan = solve_machine_counts(demand, graph)

    assert plan.machine_assignment["electronic-circuit"] is ASSEMBLER_1
    assert plan.machine_assignment["copper-cable"] is ASSEMBLER_1
    assert plan.machine_assignment["iron-plate"] is STONE_FURNACE
    assert plan.machine_assignment["copper-plate"] is STONE_FURNACE

    for recipe_id, crafts_per_sec in demand.recipe_crafts.items():
        recipe = graph.recipes[recipe_id]
        machine = plan.machine_assignment[recipe_id]
        expected = _expected_count(crafts_per_sec, recipe.time, machine.crafting_speed)
        assert plan.machine_counts[recipe_id] == expected, recipe_id

    assert plan.total_machines == sum(plan.machine_counts.values())


def test_machine_choice_override(graph: RecipeHypergraph) -> None:
    demand = expand_demand("electronic-circuit", 1.0, graph)
    plan = solve_machine_counts(demand, graph, machine_choice={"iron-plate": ASSEMBLER_1})

    assert plan.machine_assignment["iron-plate"] is ASSEMBLER_1
    recipe = graph.recipes["iron-plate"]
    crafts = demand.recipe_crafts["iron-plate"]
    assert plan.machine_counts["iron-plate"] == _expected_count(
        crafts, recipe.time, ASSEMBLER_1.crafting_speed
    )


def test_60_spm_science_pack_1_integration(graph: RecipeHypergraph) -> None:
    demand = expand_demand("science-pack-1", 1.0, graph)
    plan = solve_machine_counts(demand, graph)

    expected_total = 0
    for recipe_id, crafts_per_sec in demand.recipe_crafts.items():
        recipe = graph.recipes[recipe_id]
        machine = plan.machine_assignment[recipe_id]
        expected_total += _expected_count(crafts_per_sec, recipe.time, machine.crafting_speed)
    assert plan.total_machines == expected_total

    # Hand-computed sanity:
    #   1 science-pack-1/sec at time=5 -> 5/0.5 = 10 assemblers
    #   1 iron-gear-wheel/sec at time=0.5 -> 0.5/0.5 = 1 assembler
    #   2 iron-plate/sec for gears + 1 copper-plate/sec; both via furnace speed=1.0
    #     iron-plate crafts/sec = 2, time 3.5 -> ceil(7) = 7 furnaces
    #     copper-plate crafts/sec = 1, time 3.5 -> ceil(3.5) = 4 furnaces
    assert plan.machine_counts["science-pack-1"] == 10
    assert plan.machine_counts["iron-gear-wheel"] == 1
    assert plan.machine_counts["iron-plate"] == 7
    assert plan.machine_counts["copper-plate"] == 4


def test_empty_demand_returns_empty_plan(graph: RecipeHypergraph) -> None:
    demand = expand_demand("electronic-circuit", 1.0, graph, external_inputs={"electronic-circuit"})
    plan = solve_machine_counts(demand, graph)
    assert plan.total_machines == 0
    assert plan.machine_counts == {}
    assert plan.machine_assignment == {}
    assert plan.utilization == {}


def test_raw_only_demand(graph: RecipeHypergraph) -> None:
    demand = expand_demand("iron-ore", 1.0, graph)
    plan = solve_machine_counts(demand, graph)
    assert plan.total_machines == 0


def test_unknown_recipe_raises(graph: RecipeHypergraph) -> None:
    bogus = DemandPlan(
        target_item="iron-plate",
        target_rate_per_sec=1.0,
        item_rates={"iron-plate": 1.0},
        recipe_crafts={"definitely-not-a-recipe": 1.0},
        raw_inputs={},
    )
    with pytest.raises(MachinePlanError):
        solve_machine_counts(bogus, graph)


def test_utilization_in_unit_interval(graph: RecipeHypergraph) -> None:
    demand = expand_demand("electronic-circuit", 1.0, graph)
    plan = solve_machine_counts(demand, graph)
    for recipe_id, util in plan.utilization.items():
        assert 0.0 < util <= 1.0, (recipe_id, util)
