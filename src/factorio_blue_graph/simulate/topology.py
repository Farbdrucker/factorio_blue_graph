"""Lower ``PlanState`` to a runtime spatial topology the simulator drives.

The build is the only piece that has to understand the planner IR; the
``Simulator`` itself only needs the resulting ``Topology`` neighbour maps.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from factorio_blue_graph.model.blueprint import DIR_E, DIR_N, DIR_S, DIR_W
from factorio_blue_graph.model.graph import RecipeHypergraph
from factorio_blue_graph.planning.lp import FLUID_INPUTS
from factorio_blue_graph.repair.state import PlanState
from factorio_blue_graph.simulate.constants import ITEMS_PER_SWING, SWING_TICKS
from factorio_blue_graph.simulate.recipes import (
    craft_inputs,
    craft_yield,
    recipe_ticks,
)
from factorio_blue_graph.simulate.runtime import (
    BeltPathRuntime,
    ChestRuntime,
    InserterRuntime,
    MachineRuntime,
)

_STEP = {DIR_N: (0, -1), DIR_E: (1, 0), DIR_S: (0, 1), DIR_W: (-1, 0)}


@dataclass
class Topology:
    width: int = 0
    height: int = 0
    machines: dict[str, MachineRuntime] = field(default_factory=dict)
    machine_by_tile: dict[tuple[int, int], MachineRuntime] = field(default_factory=dict)
    chests: dict[tuple[int, int], ChestRuntime] = field(default_factory=dict)
    inserters: list[InserterRuntime] = field(default_factory=list)
    belt_paths: list[BeltPathRuntime] = field(default_factory=list)
    # Endpoint maps for belt_paths: (block_id, item_id) → list of paths whose
    # source/target is that block.  Used to glue machines to belts via inserter
    # pickup/drop endpoints at the block boundary.
    belts_from_block: dict[tuple[str, str], list[BeltPathRuntime]] = field(default_factory=dict)
    belts_to_block: dict[tuple[str, str], list[BeltPathRuntime]] = field(default_factory=dict)
    machines_in_block: dict[str, list[MachineRuntime]] = field(default_factory=dict)
    # Every surface/underground tile of a routed path → its runtime, so the
    # simulator can resolve inserter pickup/drop tiles that land on belts.
    belt_by_tile: dict[tuple[int, int], BeltPathRuntime] = field(default_factory=dict)


def build_topology(
    state: PlanState,
    recipe_graph: RecipeHypergraph,
    target_item: str | None = None,
) -> Topology:
    w, h = state.placement.canvas
    topo = Topology(width=w, height=h)

    _build_chests(state, topo, recipe_graph, target_item)
    _build_machines(state, recipe_graph, topo)
    _build_belt_paths(state, topo)
    _build_inserters(state, topo)
    return topo


def _build_chests(
    state: PlanState,
    topo: Topology,
    recipe_graph: RecipeHypergraph,
    target_item: str | None,
) -> None:
    declared_inputs = {p.item_id for p in state.external_ports if p.role == "input"}
    for chest in state.chests:
        # A chest counts as an infinite source if it holds a raw resource OR
        # has been declared as an external input port. Output chests for the
        # target item stay as finite sinks so we can measure delivery.
        is_target = chest.item_id == target_item
        is_raw = chest.item_id in recipe_graph and recipe_graph.is_raw(chest.item_id)
        is_external = chest.item_id in declared_inputs
        infinite = (is_raw or is_external) and not is_target
        topo.chests[(chest.x, chest.y)] = ChestRuntime(
            tile=(chest.x, chest.y),
            name=chest.name,
            item_filter=chest.item_id,
            infinite_source=infinite,
        )


def _build_machines(
    state: PlanState,
    recipe_graph: RecipeHypergraph,
    topo: Topology,
) -> None:
    for block_id in sorted(state.placement.blocks):
        placed = state.placement.blocks[block_id]
        block = state.block_graph.blocks.get(block_id)
        if block is None:
            continue
        member_by_id = {m.id: m for m in block.members}
        topo.machines_in_block.setdefault(block_id, [])
        for mid, mx, my in placed.machine_tiles:
            node = member_by_id.get(mid)
            if node is None:
                continue
            recipe = recipe_graph.recipes.get(node.recipe_id)
            if recipe is None:
                continue
            inputs = {k: v for k, v in craft_inputs(recipe).items() if k not in FLUID_INPUTS}
            machine = MachineRuntime(
                block_id=block_id,
                recipe_id=node.recipe_id,
                machine_id=node.id,
                tile=(mx, my),
                craft_ticks=recipe_ticks(recipe, node.machine),
                inputs_per_craft=inputs,
                output_item=node.recipe_id,
                output_per_craft=craft_yield(recipe),
            )
            topo.machines[node.id] = machine
            topo.machines_in_block[block_id].append(machine)
            fw, fh = node.machine.footprint
            for dx in range(fw):
                for dy in range(fh):
                    topo.machine_by_tile[(mx + dx, my + dy)] = machine


def _build_belt_paths(state: PlanState, topo: Topology) -> None:
    for idx, path in enumerate(state.routing.paths):
        seg_count = len(path.segments) + len(path.undergrounds)
        delay = max(1, seg_count // 4)  # ~4 tiles per tick (mid-belt average)
        rt = BeltPathRuntime(
            path_id=f"path_{idx}",
            item_id=path.item_id,
            source_block=path.source_block,
            target_block=path.target_block,
            tier=path.tier,
            lane_count=path.lane_count,
            delay_ticks=delay,
        )
        topo.belt_paths.append(rt)
        topo.belts_from_block.setdefault((path.source_block, path.item_id), []).append(rt)
        topo.belts_to_block.setdefault((path.target_block, path.item_id), []).append(rt)
        for seg in path.segments:
            topo.belt_by_tile[(seg.x, seg.y)] = rt
        for ug in path.undergrounds:
            topo.belt_by_tile[(ug.x, ug.y)] = rt


_INSERTER_REACH = {"long-handed-inserter": 2}


def _build_inserters(state: PlanState, topo: Topology) -> None:
    for ins in state.inserters.inserters:
        reach = _INSERTER_REACH.get(ins.name, 1)
        drop_tile = _step(ins.x, ins.y, ins.direction, reach)
        pickup_tile = _step(ins.x, ins.y, _opposite(ins.direction), reach)
        topo.inserters.append(
            InserterRuntime(
                tile=(ins.x, ins.y),
                name=ins.name,
                pickup_tile=pickup_tile,
                drop_tile=drop_tile,
                swing_ticks=SWING_TICKS.get(ins.name, SWING_TICKS["inserter"]),
                items_per_swing=ITEMS_PER_SWING.get(ins.name, 1),
            )
        )


def _step(x: int, y: int, direction: int, reach: int = 1) -> tuple[int, int]:
    dx, dy = _STEP.get(direction, (0, 0))
    return (x + dx * reach, y + dy * reach)


def _opposite(direction: int) -> int:
    return {DIR_N: DIR_S, DIR_S: DIR_N, DIR_E: DIR_W, DIR_W: DIR_E}.get(direction, direction)


def find_all_sinks(topo: Topology, target_item: str) -> list[ChestRuntime]:
    return [c for c in topo.chests.values() if c.item_filter == target_item]


__all__ = ["Topology", "build_topology", "find_all_sinks"]
