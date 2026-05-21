"""Phase 2: integer machine counts via PuLP MILP.

Takes the balanced `DemandPlan` produced by Phase 1 and assigns each
recipe an integer machine count `m_r ∈ ℤ≥0` minimising `Σ m_r` subject
to per-recipe throughput requirements:

    m_r · tput_r  ≥  crafts_per_sec_r · yield_r       (items/sec)

where `tput_r = (yield_/time) · crafting_speed` is the items/sec one
machine of recipe r delivers. Item mass-balance is already established
by Phase 1's reverse-topo expansion (every intermediate's `crafts_per_sec`
is exactly what downstream consumers demand at the target rate), so
constraining on Phase 1's per-recipe rate rather than on item-level
producer/consumer flows avoids over-provisioning: an integer-rounded
downstream m_r has slack, and item-level constraints would force
upstream to feed that peak slack instead of the actual draw.

Machine class is inferred from a recipe's ingredients (no machine table
in v1). The rule is intentionally crude: e.g. `landfill` (only stone) is
misclassified as smelting. Callers can override per recipe via the
`machine_choice` argument.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pulp

from factorio_blue_graph.model.graph import RecipeHypergraph
from factorio_blue_graph.model.recipe import Machine, Recipe
from factorio_blue_graph.planning.demand import DemandPlan

STONE_FURNACE = Machine(id="stone-furnace", crafting_speed=1.0, footprint=(2, 2))
CHEMICAL_PLANT = Machine(id="chemical-plant", crafting_speed=1.0, footprint=(3, 3))
ASSEMBLER_1 = Machine(id="assembling-machine-1", crafting_speed=0.5, footprint=(3, 3))

SMELT_INPUTS = frozenset({"iron-ore", "copper-ore", "stone"})
FLUID_INPUTS = frozenset(
    {"petroleum-gas", "light-oil", "heavy-oil", "sulfuric-acid", "lubricant", "water"}
)

_UTIL_EPS = 1e-6


class MachinePlanError(Exception):
    """Raised when machine-count planning cannot complete."""


@dataclass(frozen=True)
class MachinePlan:
    machine_counts: dict[str, int] = field(default_factory=dict)
    machine_assignment: dict[str, Machine] = field(default_factory=dict)
    utilization: dict[str, float] = field(default_factory=dict)
    total_machines: int = 0


def infer_machine(recipe: Recipe) -> Machine:
    ingredients = [ing.item_id for ing in recipe.ingredients]
    if ingredients and all(i in SMELT_INPUTS for i in ingredients):
        return STONE_FURNACE
    if any(i in FLUID_INPUTS for i in ingredients):
        return CHEMICAL_PLANT
    return ASSEMBLER_1


def _throughput(recipe: Recipe, machine: Machine) -> float:
    if recipe.time is None or recipe.yield_ is None or recipe.time <= 0 or recipe.yield_ <= 0:
        raise MachinePlanError(
            f"recipe {recipe.item_id!r} has non-positive time/yield; cannot plan machines"
        )
    return (recipe.yield_ / recipe.time) * machine.crafting_speed


def solve_machine_counts(
    demand: DemandPlan,
    graph: RecipeHypergraph,
    machine_choice: dict[str, Machine] | None = None,
) -> MachinePlan:
    if not demand.recipe_crafts:
        return MachinePlan()

    overrides = machine_choice or {}
    assignment: dict[str, Machine] = {}
    throughput: dict[str, float] = {}
    recipes: dict[str, Recipe] = {}

    for recipe_id in demand.recipe_crafts:
        recipe = graph.recipes.get(recipe_id)
        if recipe is None:
            raise MachinePlanError(f"unknown recipe in demand plan: {recipe_id!r}")
        machine = overrides.get(recipe_id) or infer_machine(recipe)
        assignment[recipe_id] = machine
        throughput[recipe_id] = _throughput(recipe, machine)
        recipes[recipe_id] = recipe

    prob = pulp.LpProblem("factorio_machine_counts", pulp.LpMinimize)
    m = {
        r: pulp.LpVariable(f"m_{r.replace('-', '_')}", lowBound=0, cat=pulp.LpInteger)
        for r in recipes
    }
    prob += pulp.lpSum(m.values())

    for recipe_id, recipe in recipes.items():
        required_items_per_sec = demand.recipe_crafts[recipe_id] * recipe.yield_
        prob += (
            throughput[recipe_id] * m[recipe_id] >= required_items_per_sec,
            f"throughput_{recipe_id}",
        )

    solver = pulp.PULP_CBC_CMD(msg=False)
    status = prob.solve(solver)
    if pulp.LpStatus[status] != "Optimal":
        raise MachinePlanError(f"MILP did not solve to optimality: {pulp.LpStatus[status]}")

    machine_counts: dict[str, int] = {}
    utilization: dict[str, float] = {}
    for recipe_id, var in m.items():
        value = var.value()
        if value is None:
            raise MachinePlanError(f"no value assigned for {recipe_id!r}")
        count = int(round(value))
        machine_counts[recipe_id] = count
        if count == 0:
            utilization[recipe_id] = 0.0
            continue
        recipe = recipes[recipe_id]
        machine = assignment[recipe_id]
        machine_seconds_needed = (
            demand.recipe_crafts[recipe_id] * recipe.time / machine.crafting_speed
        )
        util = machine_seconds_needed / count
        if util > 1.0 + _UTIL_EPS:
            raise MachinePlanError(f"utilization > 1 for {recipe_id!r}: {util:.4f} (count={count})")
        utilization[recipe_id] = min(util, 1.0)

    return MachinePlan(
        machine_counts=machine_counts,
        machine_assignment=assignment,
        utilization=utilization,
        total_machines=sum(machine_counts.values()),
    )


__all__ = [
    "ASSEMBLER_1",
    "CHEMICAL_PLANT",
    "STONE_FURNACE",
    "MachinePlan",
    "MachinePlanError",
    "infer_machine",
    "solve_machine_counts",
]
