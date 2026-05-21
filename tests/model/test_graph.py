"""Tests for RecipeHypergraph queries and cycle detection."""

from __future__ import annotations

import pytest

from factorio_blue_graph.model.graph import RecipeHypergraph


@pytest.fixture(scope="module")
def graph() -> RecipeHypergraph:
    return RecipeHypergraph.load_default()


def test_producers_of_single_recipe(graph: RecipeHypergraph) -> None:
    prods = graph.producers_of("iron-gear-wheel")
    assert len(prods) == 1
    assert prods[0].item_id == "iron-gear-wheel"


def test_producers_of_raw_is_empty(graph: RecipeHypergraph) -> None:
    assert graph.producers_of("iron-ore") == []


def test_consumers_of_iron_plate(graph: RecipeHypergraph) -> None:
    consumer_ids = {r.item_id for r in graph.consumers_of("iron-plate")}
    assert {"iron-gear-wheel", "electronic-circuit"} <= consumer_ids


def test_is_raw(graph: RecipeHypergraph) -> None:
    assert graph.is_raw("iron-ore")
    assert not graph.is_raw("iron-plate")


def test_is_raw_unknown_raises(graph: RecipeHypergraph) -> None:
    with pytest.raises(KeyError):
        graph.is_raw("definitely-not-a-real-item")


def test_no_cycles(graph: RecipeHypergraph) -> None:
    assert graph.detect_cycles() == []
