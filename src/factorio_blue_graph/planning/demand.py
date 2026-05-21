"""Phase 1: reverse demand propagation through the recipe hypergraph.

Given a target item and a desired output rate (items/sec), walk backwards
through producing recipes in reverse topological order to compute the
crafts/sec required per recipe and the resulting raw-input demand. Recipe
timing is intentionally not used here — Phase 2's MILP converts crafts/sec
into machine counts using `recipe.time` and machine `crafting_speed`.

The dataset is a DAG (verified via `RecipeHypergraph.detect_cycles`), so a
single topological pass is sufficient and avoids the double-counting risk
of a naive BFS that re-processes items reached via multiple paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from factorio_blue_graph.model.graph import RecipeHypergraph


class DemandError(Exception):
    """Raised when demand propagation cannot complete."""


@dataclass(frozen=True)
class DemandPlan:
    target_item: str
    target_rate_per_sec: float
    item_rates: dict[str, float] = field(default_factory=dict)
    recipe_crafts: dict[str, float] = field(default_factory=dict)
    raw_inputs: dict[str, float] = field(default_factory=dict)


def _ancestors_in_reverse_topo(
    graph: RecipeHypergraph,
    target: str,
    treat_as_leaf: set[str],
) -> list[str]:
    """Return items reachable from `target` via producing-recipe edges,
    ordered so each item appears before any of its ingredients."""
    order: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(item_id: str) -> None:
        if item_id in visited:
            return
        if item_id in visiting:
            raise DemandError(f"cycle detected at {item_id!r}")
        visiting.add(item_id)
        if item_id not in treat_as_leaf and not graph.is_raw(item_id):
            recipe = graph.recipes[item_id]
            for ing in recipe.ingredients:
                if ing.item_id not in graph:
                    raise DemandError(
                        f"recipe {item_id!r} references unknown ingredient {ing.item_id!r}"
                    )
                visit(ing.item_id)
        visiting.remove(item_id)
        visited.add(item_id)
        order.append(item_id)

    visit(target)
    order.reverse()
    return order


def expand_demand(
    target_item: str,
    rate_per_sec: float,
    graph: RecipeHypergraph,
    recipe_overrides: dict[str, str] | None = None,
    external_inputs: set[str] | None = None,
) -> DemandPlan:
    if target_item not in graph:
        raise DemandError(f"unknown item: {target_item}")
    if rate_per_sec < 0:
        raise DemandError(f"rate must be non-negative, got {rate_per_sec}")

    overrides = recipe_overrides or {}
    pinned = external_inputs or set()
    for item_id, recipe_id in overrides.items():
        if item_id != recipe_id:
            raise DemandError(
                f"recipe override {item_id}->{recipe_id} not supported: "
                "current dataset has a single recipe per item"
            )

    order = _ancestors_in_reverse_topo(graph, target_item, treat_as_leaf=pinned)

    item_rates: dict[str, float] = {target_item: rate_per_sec}
    recipe_crafts: dict[str, float] = {}
    raw_inputs: dict[str, float] = {}

    for item_id in order:
        rate = item_rates.get(item_id, 0.0)
        if item_id in pinned or graph.is_raw(item_id):
            raw_inputs[item_id] = rate
            continue
        recipe = graph.recipes[item_id]
        if recipe.yield_ is None or recipe.yield_ <= 0:
            raise DemandError(f"recipe {item_id!r} has non-positive yield")
        crafts_per_sec = rate / recipe.yield_
        recipe_crafts[item_id] = crafts_per_sec
        for ing in recipe.ingredients:
            item_rates[ing.item_id] = item_rates.get(ing.item_id, 0.0) + crafts_per_sec * ing.amount

    return DemandPlan(
        target_item=target_item,
        target_rate_per_sec=rate_per_sec,
        item_rates=item_rates,
        recipe_crafts=recipe_crafts,
        raw_inputs=raw_inputs,
    )


__all__ = ["DemandPlan", "DemandError", "expand_demand"]
