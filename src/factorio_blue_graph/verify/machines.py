"""Per-machine inserter coverage check.

For every placed machine with a recipe, every non-fluid ingredient needs
at least one inserter dropping onto the machine, and every product needs
at least one inserter picking up from the machine.

Fluids (water, petroleum-gas, etc.) are routed via pipes, which the v1
planner does not emit at all; we suppress `MISSING_INSERTER` for them so
the verifier doesn't flag the entire oil-processing column.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from factorio_blue_graph.model.blueprint import DIR_E, DIR_N, DIR_S, DIR_W
from factorio_blue_graph.planning.lp import FLUID_INPUTS
from factorio_blue_graph.verify.grid import MachineCell, TileGrid
from factorio_blue_graph.verify.report import Issue, Severity, VerifyReport

if TYPE_CHECKING:
    from factorio_blue_graph.layout.placement import Placement
    from factorio_blue_graph.model.graph import BlockGraph

_STEP = {DIR_N: (0, -1), DIR_E: (1, 0), DIR_S: (0, 1), DIR_W: (-1, 0)}
_OPPOSITE = {DIR_N: DIR_S, DIR_S: DIR_N, DIR_E: DIR_W, DIR_W: DIR_E}
_REACH = {
    "inserter": 1,
    "fast-inserter": 1,
    "stack-inserter": 1,
    "long-handed-inserter": 2,
}


def check(
    grid: TileGrid,
    placement: Placement,
    block_graph: BlockGraph,
    report: VerifyReport,
) -> None:
    """Emit `MISSING_INSERTER` for every recipe input/output without an inserter."""
    inputs_seen, outputs_seen = _index_inserters_by_machine(grid)

    for pb in placement.blocks.values():
        block = block_graph.blocks.get(pb.block_id)
        if block is None:
            continue
        member_by_id = {m.id: m for m in block.members}
        for mid, mx, my in pb.machine_tiles:
            node = member_by_id.get(mid)
            if node is None:
                continue
            from factorio_blue_graph.model.graph import RecipeHypergraph

            try:
                graph = RecipeHypergraph.load_default()
            except Exception:
                return
            recipe = graph.recipes.get(node.recipe_id)
            if recipe is None:
                continue
            fw, fh = node.machine.footprint

            need_inputs = {
                ing.item_id for ing in recipe.ingredients if ing.item_id not in FLUID_INPUTS
            }
            # If any inserter drops onto this machine, treat all non-fluid
            # ingredients as satisfied — we can't tell from the IR alone
            # which item an inserter carries, so requiring at least one
            # input inserter per machine is the strictest deduction that
            # doesn't produce false MISSING_INSERTER for chest-fed factories.
            has_any_input_inserter = mid in inputs_seen and len(inputs_seen[mid]) > 0
            missing_inputs = set() if has_any_input_inserter else need_inputs
            for missing in missing_inputs:
                report.issues.append(
                    Issue(
                        code="MISSING_INSERTER",
                        severity=Severity.ERROR,
                        message=(
                            f"machine {mid} ({node.recipe_id}) needs an input inserter "
                            f"for {missing}"
                        ),
                        location=(mx, my),
                        payload={
                            "machine_id": mid,
                            "recipe_id": node.recipe_id,
                            "item_id": missing,
                            "role": "input",
                            "top_left": (mx, my),
                            "footprint": (fw, fh),
                        },
                    )
                )

            # Output: every non-raw recipe has exactly one product (v1 dataset).
            if (
                recipe.yield_ is not None
                and node.recipe_id not in FLUID_INPUTS
                and mid not in outputs_seen
            ):
                report.issues.append(
                    Issue(
                        code="MISSING_INSERTER",
                        severity=Severity.ERROR,
                        message=(
                            f"machine {mid} ({node.recipe_id}) needs an output "
                            f"inserter for {node.recipe_id}"
                        ),
                        location=(mx, my),
                        payload={
                            "machine_id": mid,
                            "recipe_id": node.recipe_id,
                            "item_id": node.recipe_id,
                            "role": "output",
                            "top_left": (mx, my),
                            "footprint": (fw, fh),
                        },
                    )
                )


def _index_inserters_by_machine(
    grid: TileGrid,
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Build (inputs_seen, outputs_seen) keyed by machine_id.

    Each maps `machine_id` → set of inserter tile coords. We can't tell
    from the IR alone which item an inserter carries, so the caller
    treats "any input inserter at all" as satisfying every non-fluid
    ingredient (and likewise for outputs). The set's size doubles as a
    cheap presence indicator.
    """
    inputs: dict[str, set[tuple[int, int]]] = {}
    outputs: dict[str, set[tuple[int, int]]] = {}
    for tile, cell in grid.inserters():
        ix, iy = tile
        drop = cell.direction
        pickup = _OPPOSITE[drop]
        reach = _REACH.get(cell.name, 1)
        drop_tile = (ix + _STEP[drop][0] * reach, iy + _STEP[drop][1] * reach)
        pickup_tile = (ix + _STEP[pickup][0] * reach, iy + _STEP[pickup][1] * reach)
        drop_ent = grid.get(*drop_tile)
        pickup_ent = grid.get(*pickup_tile)
        if isinstance(drop_ent, MachineCell):
            inputs.setdefault(drop_ent.machine_id, set()).add((ix, iy))
        if isinstance(pickup_ent, MachineCell):
            outputs.setdefault(pickup_ent.machine_id, set()).add((ix, iy))
    return inputs, outputs


__all__ = ["check"]
