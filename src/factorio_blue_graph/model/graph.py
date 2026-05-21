"""Recipe hypergraph: items as nodes, recipes as edges from ingredient to output."""

from __future__ import annotations

import json
from importlib.resources import files

import networkx as nx

from factorio_blue_graph.model.recipe import Item, Recipe


class RecipeHypergraph:
    """Directed graph of items linked by their producing recipes.

    Edges run from each ingredient item to the produced item, annotated
    with the recipe metadata. With the v1 dataset every item has at most
    one producing recipe, so the structure is effectively a DAG of items.
    """

    def __init__(self, items: list[Item]) -> None:
        self.items: dict[str, Item] = {it.id: it for it in items}
        self.recipes: dict[str, Recipe] = {it.id: it.recipe for it in items if not it.recipe.is_raw}
        self.graph = nx.DiGraph()
        for item in items:
            self.graph.add_node(item.id, item=item)
        for recipe in self.recipes.values():
            for ing in recipe.ingredients:
                self.graph.add_edge(ing.item_id, recipe.item_id, amount=ing.amount)

    @classmethod
    def load_default(cls) -> RecipeHypergraph:
        path = files("factorio_blue_graph.data").joinpath("recipes.json")
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        return cls([Item.from_json_entry(entry) for entry in raw])

    def producers_of(self, item_id: str) -> list[Recipe]:
        recipe = self.recipes.get(item_id)
        return [recipe] if recipe is not None else []

    def consumers_of(self, item_id: str) -> list[Recipe]:
        return [
            self.recipes[consumer]
            for consumer in self.graph.successors(item_id)
            if consumer in self.recipes
        ]

    def is_raw(self, item_id: str) -> bool:
        item = self.items.get(item_id)
        if item is None:
            raise KeyError(f"unknown item: {item_id}")
        return item.recipe.is_raw

    def detect_cycles(self) -> list[list[str]]:
        return [cycle for cycle in nx.simple_cycles(self.graph)]

    def __contains__(self, item_id: str) -> bool:
        return item_id in self.items

    def __len__(self) -> int:
        return len(self.items)
