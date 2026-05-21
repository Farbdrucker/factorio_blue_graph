"""Recipe hypergraph and machine-flow graph.

`RecipeHypergraph` is the abstract item/recipe DAG. `FlowGraph` instantiates
one node per concrete machine (driven by Phase 2's `MachinePlan`) and wires
inter-machine item flows as a `MultiDiGraph` so Phase 4 can cluster on it
and Phase 6 can drive belt routing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from typing import TYPE_CHECKING

import networkx as nx

from factorio_blue_graph.model.recipe import Item, Machine, Recipe

if TYPE_CHECKING:
    from factorio_blue_graph.planning.demand import DemandPlan
    from factorio_blue_graph.planning.lp import MachinePlan


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


@dataclass(frozen=True)
class MachineNode:
    """A single concrete machine instance running one recipe."""

    id: str
    recipe_id: str
    machine: Machine
    crafts_per_sec: float


@dataclass(frozen=True)
class ItemChannel:
    """Aggregate item flow between one producer recipe and one consumer recipe.

    `rate` is in items/sec and is what Phase 3's belt-tier picker consumes.
    Multiple `ItemChannel`s may exist for the same item if it is produced
    or consumed by several recipes.
    """

    item_id: str
    producer_recipe: str
    consumer_recipe: str
    rate: float


@dataclass(frozen=True)
class ExternalInput:
    """A consumer machine drawing a raw input from the canvas boundary."""

    consumer_node: str
    item_id: str
    rate: float


class FlowGraph:
    """Per-machine bipartite flow graph derived from a `MachinePlan`.

    Nodes are individual machine instances (one per machine in
    `MachinePlan.machine_counts`). For each item produced by some active
    recipe and consumed by another, a fully-bipartite set of edges is laid
    down between producer instances and consumer instances; each carries an
    equal share of the corresponding `ItemChannel.rate`. This keeps the
    Louvain clustering in Phase 4 well-defined while letting Phase 3 reason
    about belt tiers at the aggregated channel level.
    """

    def __init__(self) -> None:
        self.nodes: dict[str, MachineNode] = {}
        self.channels: list[ItemChannel] = []
        self.external_in_edges: list[ExternalInput] = []
        self.graph: nx.MultiDiGraph = nx.MultiDiGraph()

    @classmethod
    def from_plan(
        cls,
        demand: DemandPlan,
        machine_plan: MachinePlan,
        recipe_graph: RecipeHypergraph,
    ) -> FlowGraph:
        fg = cls()

        for recipe_id, count in machine_plan.machine_counts.items():
            if count <= 0:
                continue
            machine = machine_plan.machine_assignment[recipe_id]
            per_machine = demand.recipe_crafts[recipe_id] / count
            for i in range(count):
                node_id = f"{recipe_id}#{i}"
                node = MachineNode(
                    id=node_id,
                    recipe_id=recipe_id,
                    machine=machine,
                    crafts_per_sec=per_machine,
                )
                fg.nodes[node_id] = node
                fg.graph.add_node(node_id, machine_node=node)

        active = {r for r, c in machine_plan.machine_counts.items() if c > 0}

        for item_id, total_rate in demand.item_rates.items():
            if total_rate <= 0:
                continue

            producer_recipes = [
                r for r in recipe_graph.producers_of(item_id) if r.item_id in active
            ]
            consumer_recipes = [
                r for r in recipe_graph.consumers_of(item_id) if r.item_id in active
            ]

            if not consumer_recipes:
                continue

            cons_totals: dict[str, float] = {}
            for cons in consumer_recipes:
                crafts = demand.recipe_crafts[cons.item_id]
                amount = next(ing.amount for ing in cons.ingredients if ing.item_id == item_id)
                cons_totals[cons.item_id] = crafts * amount

            if not producer_recipes:
                for cons_id, c_rate in cons_totals.items():
                    cons_count = machine_plan.machine_counts[cons_id]
                    per_machine = c_rate / cons_count
                    for j in range(cons_count):
                        fg.external_in_edges.append(
                            ExternalInput(
                                consumer_node=f"{cons_id}#{j}",
                                item_id=item_id,
                                rate=per_machine,
                            )
                        )
                continue

            prod_totals: dict[str, float] = {}
            for prod in producer_recipes:
                crafts = demand.recipe_crafts[prod.item_id]
                prod_totals[prod.item_id] = crafts * prod.yield_

            for prod_id, p_rate in prod_totals.items():
                prod_count = machine_plan.machine_counts[prod_id]
                for cons_id, c_rate in cons_totals.items():
                    cons_count = machine_plan.machine_counts[cons_id]
                    channel_rate = p_rate * c_rate / total_rate
                    fg.channels.append(
                        ItemChannel(
                            item_id=item_id,
                            producer_recipe=prod_id,
                            consumer_recipe=cons_id,
                            rate=channel_rate,
                        )
                    )
                    per_edge = channel_rate / (prod_count * cons_count)
                    for i in range(prod_count):
                        src = f"{prod_id}#{i}"
                        for j in range(cons_count):
                            dst = f"{cons_id}#{j}"
                            fg.graph.add_edge(
                                src,
                                dst,
                                key=f"{item_id}@{i}-{j}",
                                item=item_id,
                                rate=per_edge,
                                weight=per_edge,
                            )

        return fg

    def nodes_for_recipe(self, recipe_id: str) -> list[MachineNode]:
        return [n for n in self.nodes.values() if n.recipe_id == recipe_id]

    def in_edges(self, node_id: str) -> list[tuple[str, str, float]]:
        return [
            (src, data["item"], data["rate"])
            for src, _, data in self.graph.in_edges(node_id, data=True)
        ]

    def out_edges(self, node_id: str) -> list[tuple[str, str, float]]:
        return [
            (dst, data["item"], data["rate"])
            for _, dst, data in self.graph.out_edges(node_id, data=True)
        ]

    @property
    def total_machines(self) -> int:
        return len(self.nodes)
