"""Verifier orchestration.

`verify_plan(...)` consumes the live planner IR (placement, block_graph,
flow_graph, routing, inserters, power, power_source, external_ports,
chests) and runs every check. `verify_blueprint_string(bp)` decodes a
blueprint string back into a partial IR and runs the subset of checks
that don't require `flow_graph` or `block_graph`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from factorio_blue_graph.export.blueprint_string import decode
from factorio_blue_graph.model.blueprint import (
    DIR_E,
    DIR_N,
    DIR_S,
    DIR_W,
    Boiler,
    Chest,
    OffshorePump,
    PowerSourceResult,
    Splitter,
    SteamEngine,
)
from factorio_blue_graph.verify.grid import (
    BeltCell,
    BoilerCell,
    ChestCell,
    InserterCell,
    MachineCell,
    PoleCell,
    PumpCell,
    SplitterCell,
    SteamCell,
    TileGrid,
    UndergroundCell,
)
from factorio_blue_graph.verify.report import VerifyReport

if TYPE_CHECKING:
    from factorio_blue_graph.layout.inserter import InserterResult
    from factorio_blue_graph.layout.placement import Placement
    from factorio_blue_graph.layout.power import PoleResult
    from factorio_blue_graph.layout.routing import RoutingResult
    from factorio_blue_graph.model.blueprint import IOPort
    from factorio_blue_graph.model.graph import BlockGraph, FlowGraph


def verify_plan(
    placement: Placement,
    block_graph: BlockGraph,
    flow_graph: FlowGraph,
    routing: RoutingResult,
    inserters: InserterResult,
    power: PoleResult,
    power_source: PowerSourceResult | None = None,
    external_ports: tuple[IOPort, ...] = (),
    chests: tuple[Chest, ...] = (),
) -> VerifyReport:
    """Run the full check battery against the live planner IR."""
    grid = _build_grid_from_ir(
        placement,
        block_graph,
        routing,
        inserters,
        power,
        power_source,
        chests,
    )
    report = VerifyReport()

    # Lazy imports keep the verify package import-light.
    from factorio_blue_graph.verify import (
        belts,
        machines,
        sources,
    )
    from factorio_blue_graph.verify import (
        inserters as ins_checks,
    )
    from factorio_blue_graph.verify import (
        power as pwr,
    )

    belts.check(grid, routing, report)
    ins_checks.check(grid, inserters, report)
    machines.check(grid, placement, block_graph, report)
    pwr.check(grid, placement, block_graph, power, power_source, report)
    sources.check(grid, placement, block_graph, flow_graph, external_ports, chests, report)
    return report


def verify_blueprint_string(bp: str) -> VerifyReport:
    """Decode `bp`, reconstruct a partial IR, and run string-derivable checks.

    Checks needing the recipe-level `flow_graph` or `block_graph` (notably
    `MISSING_INSERTER` and `STARVED_INPUT`) are skipped — they would
    require a re-derivation of the planner's intent that the blueprint
    alone can't supply.
    """
    blueprint = decode(bp)
    entities = blueprint.get("blueprint", {}).get("entities", [])
    grid, canvas = _build_grid_from_entities(entities)
    report = VerifyReport()

    from factorio_blue_graph.verify import belts
    from factorio_blue_graph.verify import inserters as ins_checks
    from factorio_blue_graph.verify import power as pwr

    belts.check_string_only(grid, report)
    ins_checks.check_string_only(grid, report)
    pwr.check_string_only(grid, report)
    return report


# ---------------------------------------------------------------------------
# IR → TileGrid
# ---------------------------------------------------------------------------


def _build_grid_from_ir(
    placement: Placement,
    block_graph: BlockGraph,
    routing: RoutingResult,
    inserters: InserterResult,
    power: PoleResult,
    power_source: PowerSourceResult | None,
    chests: tuple[Chest, ...],
) -> TileGrid:
    grid = TileGrid(placement.canvas)

    for pb in placement.blocks.values():
        block = block_graph.blocks.get(pb.block_id)
        member_by_id = {m.id: m for m in block.members} if block else {}
        for mid, mx, my in pb.machine_tiles:
            node = member_by_id.get(mid)
            if node is None:
                continue
            fw, fh = node.machine.footprint
            cell = MachineCell(
                machine_id=mid,
                recipe_id=node.recipe_id,
                machine_name=node.machine.id,
                top_left=(mx, my),
                footprint=(fw, fh),
            )
            for dx in range(fw):
                for dy in range(fh):
                    grid.put(mx + dx, my + dy, cell)

    for path in routing.paths:
        for seg in path.segments:
            grid.put(seg.x, seg.y, BeltCell(direction=seg.direction, name=seg.name))
        for ug in path.undergrounds:
            grid.put(
                ug.x,
                ug.y,
                UndergroundCell(direction=ug.direction, io_type=ug.io_type, name=ug.name),
            )
    for sp in routing.splitters:
        for tile in _splitter_tiles(sp):
            grid.put(
                tile[0],
                tile[1],
                SplitterCell(direction=sp.direction, name=sp.name, anchor=(sp.x, sp.y)),
            )

    for seg in inserters.boundary_belts:
        # Don't clobber an already-placed routed belt on the same tile.
        if (seg.x, seg.y) not in grid:
            grid.put(seg.x, seg.y, BeltCell(direction=seg.direction, name=seg.name))

    for ins in inserters.inserters:
        grid.put(ins.x, ins.y, InserterCell(direction=ins.direction, name=ins.name))

    for pole in power.poles:
        grid.put(pole.x, pole.y, PoleCell(name=pole.name))

    for chest in chests:
        grid.put(chest.x, chest.y, ChestCell(name=chest.name, item_id=chest.item_id))

    if power_source is not None:
        for se in power_source.steam_engines:
            for dx, dy in _engine_tiles(se):
                grid.put(dx, dy, SteamCell(direction=se.direction))
        for bo in power_source.boilers:
            for dx, dy in _boiler_tiles(bo):
                grid.put(dx, dy, BoilerCell(direction=bo.direction))
        for pump in power_source.offshore_pumps:
            for dx, dy in _pump_tiles(pump):
                grid.put(dx, dy, PumpCell(direction=pump.direction))

    return grid


# ---------------------------------------------------------------------------
# Decoded entities → TileGrid
# ---------------------------------------------------------------------------


def _build_grid_from_entities(
    entities: list[dict],
) -> tuple[TileGrid, tuple[int, int]]:
    """Reconstruct an approximate `TileGrid` from a decoded blueprint dict.

    Machine ids are synthesised as `<name>#<idx>` since the blueprint
    string doesn't preserve our internal ids. Footprints default to 3×3
    (the vanilla 1.1 assembler/chem-plant) for unknown machine names.
    """
    # First pass: scan canvas extents.
    xs: list[int] = []
    ys: list[int] = []
    for ent in entities:
        pos = ent.get("position", {})
        xs.append(int(pos.get("x", 0)))
        ys.append(int(pos.get("y", 0)))
    if not xs:
        return TileGrid((1, 1)), (1, 1)
    W = max(xs) + 4
    H = max(ys) + 4
    grid = TileGrid((W, H))

    machine_idx = 0
    for ent in entities:
        name = ent.get("name", "")
        pos = ent.get("position", {"x": 0, "y": 0})
        px = pos.get("x", 0)
        py = pos.get("y", 0)
        direction = int(ent.get("direction", 0))

        if name.endswith("transport-belt") or name in (
            "transport-belt",
            "fast-transport-belt",
            "express-transport-belt",
        ):
            tx, ty = int(px - 0.5), int(py - 0.5)
            grid.put(tx, ty, BeltCell(direction=direction, name=name))
            continue
        if name.endswith("underground-belt"):
            tx, ty = int(px - 0.5), int(py - 0.5)
            io_type = ent.get("type", "input")
            grid.put(tx, ty, UndergroundCell(direction=direction, io_type=io_type, name=name))
            continue
        if name.endswith("splitter"):
            anchor = _splitter_anchor_from_center(px, py, direction)
            for tile in _splitter_tiles_from_anchor(anchor, direction):
                grid.put(
                    tile[0],
                    tile[1],
                    SplitterCell(direction=direction, name=name, anchor=anchor),
                )
            continue
        if "inserter" in name:
            tx, ty = int(px - 0.5), int(py - 0.5)
            grid.put(tx, ty, InserterCell(direction=direction, name=name))
            continue
        if name.endswith("electric-pole") or name == "medium-electric-pole":
            tx, ty = int(px - 0.5), int(py - 0.5)
            grid.put(tx, ty, PoleCell(name=name))
            continue
        if name in ("wooden-chest", "iron-chest", "steel-chest"):
            tx, ty = int(px - 0.5), int(py - 0.5)
            grid.put(tx, ty, ChestCell(name=name, item_id=""))
            continue
        if name == "steam-engine":
            # 3×5 footprint; position is the center.
            for tx, ty in _engine_tiles_from_center(px, py, direction):
                grid.put(tx, ty, SteamCell(direction=direction))
            continue
        if name == "boiler":
            for tx, ty in _boiler_tiles_from_center(px, py, direction):
                grid.put(tx, ty, BoilerCell(direction=direction))
            continue
        if name == "offshore-pump":
            for tx, ty in _pump_tiles_from_center(px, py, direction):
                grid.put(tx, ty, PumpCell(direction=direction))
            continue
        if name == "accumulator":
            # Stand-in power-source placeholder; ignore for now.
            continue
        # Generic machine fallback (assembler, furnace, chem-plant) — 3×3.
        recipe = ent.get("recipe", "")
        fw, fh = 3, 3
        if name == "stone-furnace":
            fw, fh = 2, 2
        tx = int(px - fw / 2)
        ty = int(py - fh / 2)
        mid = f"{name}#{machine_idx}"
        machine_idx += 1
        cell = MachineCell(
            machine_id=mid,
            recipe_id=recipe,
            machine_name=name,
            top_left=(tx, ty),
            footprint=(fw, fh),
        )
        for dx in range(fw):
            for dy in range(fh):
                grid.put(tx + dx, ty + dy, cell)

    return grid, (W, H)


# ---------------------------------------------------------------------------
# Footprint geometry helpers (mirror export/blueprint_string.py)
# ---------------------------------------------------------------------------


def _splitter_tiles(sp: Splitter) -> tuple[tuple[int, int], tuple[int, int]]:
    sx, sy, d = sp.x, sp.y, sp.direction
    if d == DIR_N:
        return (sx, sy), (sx + 1, sy)
    if d == DIR_E:
        return (sx, sy), (sx, sy + 1)
    if d == DIR_S:
        return (sx, sy), (sx - 1, sy)
    return (sx, sy), (sx, sy - 1)  # DIR_W


def _splitter_tiles_from_anchor(
    anchor: tuple[int, int], direction: int
) -> tuple[tuple[int, int], tuple[int, int]]:
    sx, sy = anchor
    if direction == DIR_N:
        return (sx, sy), (sx + 1, sy)
    if direction == DIR_E:
        return (sx, sy), (sx, sy + 1)
    if direction == DIR_S:
        return (sx, sy), (sx - 1, sy)
    return (sx, sy), (sx, sy - 1)


def _splitter_anchor_from_center(cx: float, cy: float, direction: int) -> tuple[int, int]:
    """Inverse of `export.blueprint_string._splitter_center`."""
    if direction == DIR_N:
        return (int(cx - 1.0), int(cy - 0.5))
    if direction == DIR_E:
        return (int(cx - 0.5), int(cy - 1.0))
    if direction == DIR_S:
        return (int(cx), int(cy - 0.5))
    return (int(cx - 0.5), int(cy))  # DIR_W


def _engine_tiles(se: SteamEngine) -> list[tuple[int, int]]:
    # 3 wide × 5 long. Anchor (x, y) is top-left; long axis follows the
    # facing direction. For simplicity we treat direction=2 (east) as
    # extending +x and direction=4 (south) as +y.
    if se.direction in (DIR_E, DIR_W):
        # 5 wide × 3 tall
        return [(se.x + dx, se.y + dy) for dx in range(5) for dy in range(3)]
    return [(se.x + dx, se.y + dy) for dx in range(3) for dy in range(5)]


def _engine_tiles_from_center(cx: float, cy: float, direction: int) -> list[tuple[int, int]]:
    if direction in (DIR_E, DIR_W):
        tx = int(cx - 5 / 2)
        ty = int(cy - 3 / 2)
        return [(tx + dx, ty + dy) for dx in range(5) for dy in range(3)]
    tx = int(cx - 3 / 2)
    ty = int(cy - 5 / 2)
    return [(tx + dx, ty + dy) for dx in range(3) for dy in range(5)]


def _boiler_tiles(bo: Boiler) -> list[tuple[int, int]]:
    if bo.direction in (DIR_E, DIR_W):
        return [(bo.x + dx, bo.y + dy) for dx in range(3) for dy in range(2)]
    return [(bo.x + dx, bo.y + dy) for dx in range(2) for dy in range(3)]


def _boiler_tiles_from_center(cx: float, cy: float, direction: int) -> list[tuple[int, int]]:
    if direction in (DIR_E, DIR_W):
        tx = int(cx - 3 / 2)
        ty = int(cy - 2 / 2)
        return [(tx + dx, ty + dy) for dx in range(3) for dy in range(2)]
    tx = int(cx - 2 / 2)
    ty = int(cy - 3 / 2)
    return [(tx + dx, ty + dy) for dx in range(2) for dy in range(3)]


def _pump_tiles(pump: OffshorePump) -> list[tuple[int, int]]:
    if pump.direction in (DIR_E, DIR_W):
        return [(pump.x + 0, pump.y + 0), (pump.x + 1, pump.y + 0)]
    return [(pump.x + 0, pump.y + 0), (pump.x + 0, pump.y + 1)]


def _pump_tiles_from_center(cx: float, cy: float, direction: int) -> list[tuple[int, int]]:
    if direction in (DIR_E, DIR_W):
        tx = int(cx - 1.0)
        ty = int(cy - 0.5)
        return [(tx, ty), (tx + 1, ty)]
    tx = int(cx - 0.5)
    ty = int(cy - 1.0)
    return [(tx, ty), (tx, ty + 1)]


__all__ = ["verify_plan", "verify_blueprint_string"]
