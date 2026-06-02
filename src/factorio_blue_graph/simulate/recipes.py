"""Recipe → simulation parameter helpers."""

from __future__ import annotations

import math

from factorio_blue_graph.model.recipe import Machine, Recipe
from factorio_blue_graph.simulate.constants import TICKS_PER_SECOND


def recipe_ticks(recipe: Recipe, machine: Machine) -> int:
    """Ticks required for one craft of ``recipe`` on ``machine``."""
    if recipe.time is None or recipe.time <= 0:
        raise ValueError(f"recipe {recipe.item_id!r} has no positive time")
    if machine.crafting_speed <= 0:
        raise ValueError(f"machine {machine.id!r} has non-positive crafting speed")
    seconds = recipe.time / machine.crafting_speed
    return max(1, math.ceil(seconds * TICKS_PER_SECOND))


def craft_inputs(recipe: Recipe) -> dict[str, int]:
    """Integerised ingredient counts per craft (ceil; conservative)."""
    out: dict[str, int] = {}
    for ing in recipe.ingredients:
        out[ing.item_id] = max(1, math.ceil(ing.amount))
    return out


def craft_yield(recipe: Recipe) -> int:
    return max(1, math.ceil(recipe.yield_ or 1.0))


__all__ = ["craft_inputs", "craft_yield", "recipe_ticks"]
