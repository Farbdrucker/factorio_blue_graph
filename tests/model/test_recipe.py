"""Tests for the Pydantic recipe models and packaged data."""

from __future__ import annotations

from factorio_blue_graph.model.graph import RecipeHypergraph
from factorio_blue_graph.model.recipe import Item


def test_packaged_recipes_parse() -> None:
    graph = RecipeHypergraph.load_default()
    assert len(graph) == 214


def test_iron_gear_wheel() -> None:
    graph = RecipeHypergraph.load_default()
    item = graph.items["iron-gear-wheel"]
    assert isinstance(item, Item)
    assert item.recipe.time == 0.5
    assert item.recipe.yield_ == 1
    assert len(item.recipe.ingredients) == 1
    ing = item.recipe.ingredients[0]
    assert ing.item_id == "iron-plate"
    assert ing.amount == 2


def test_electronic_circuit_ingredients() -> None:
    graph = RecipeHypergraph.load_default()
    recipe = graph.items["electronic-circuit"].recipe
    assert recipe.time == 0.5
    ings = {i.item_id: i.amount for i in recipe.ingredients}
    assert ings == {"copper-cable": 3, "iron-plate": 1}


def test_raw_item_has_none_time() -> None:
    graph = RecipeHypergraph.load_default()
    assert graph.items["iron-ore"].recipe.is_raw
    assert graph.items["iron-ore"].recipe.time is None
