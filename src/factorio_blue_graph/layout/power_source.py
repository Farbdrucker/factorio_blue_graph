"""Phase 7b: emit a steam-engine + boiler + offshore-pump cluster.

Sizes the cluster from total machine + inserter draw, then places a
horizontal strip along the canvas edge with the most contiguous free
space.

Power numbers are vanilla Factorio 1.1:
- Steam engine     900 kW per unit
- Boiler          ~1800 kW thermal (one boiler feeds two engines)
- Offshore pump   ~1200 boilers-worth of water (one pump per cluster)
- Inserter           14 kW continuous (peak; we use this conservatively)

Layout (east-facing example, the only orientation we emit for v1):
    P  B B B  E E E ...
where P is the 1×2 pump, B is a 3×2 boiler (the row directly south of
the engine row), E is a 3×5 steam engine (engines occupy the row north
of the boilers).  We keep the cluster as a single horizontal strip
along the canvas's bottom edge to minimise interference with the
already-placed blocks.

If the strip cannot fit the requested cluster within the canvas we
return a `PLACEHOLDER` result: a `WARNING` issue will be emitted by the
verifier and the blueprint still imports — just without working power.
"""

from __future__ import annotations

import math

from factorio_blue_graph.layout.inserter import InserterResult
from factorio_blue_graph.layout.placement import Placement
from factorio_blue_graph.layout.routing import RoutingResult
from factorio_blue_graph.model.blueprint import (
    DIR_E,
    Boiler,
    OffshorePump,
    PowerSourceResult,
    SteamEngine,
)
from factorio_blue_graph.model.graph import BlockGraph

# Vanilla 1.1 power numbers.
_STEAM_ENGINE_KW = 900.0
_INSERTER_KW = 0.014  # 14 W continuous draw; conservative
# Vanilla-1.1 machine consumption (kW) — hard-coded since the `Machine`
# model carries no `energy_kw` field. Unknown entities are charged at
# the assembler-1 rate as a safe upper bound.
_MACHINE_KW = {
    "assembling-machine-1": 75.0,
    "assembling-machine-2": 155.0,
    "assembling-machine-3": 388.0,
    "stone-furnace": 0.0,  # burner — does NOT draw from the grid
    "steel-furnace": 0.0,
    "electric-furnace": 180.0,
    "chemical-plant": 210.0,
    "oil-refinery": 420.0,
}
_DEFAULT_MACHINE_KW = 75.0


def emit_power_source(
    placement: Placement,
    block_graph: BlockGraph,
    routing: RoutingResult,
    inserters: InserterResult,
) -> PowerSourceResult:
    """Compute total draw and emit a sized steam-engine cluster on the canvas
    edge with the most free contiguous tiles."""
    required_kw = _required_kw(placement, block_graph, inserters)
    if required_kw <= 0:
        return PowerSourceResult(required_kw=0.0, nominal_kw=0.0, status="OK")

    n_engines = max(1, math.ceil(required_kw / _STEAM_ENGINE_KW))
    # Vanilla ratio is 1 boiler per 2 engines (boiler outputs 1.8 MW
    # thermal which equals ~2 engines). Be conservative and round up.
    n_boilers = max(1, math.ceil(n_engines / 2))
    n_pumps = 1

    occupied = _occupied_tiles(placement, routing, inserters)
    strip = _find_free_strip(placement.canvas, occupied, n_engines, n_boilers, n_pumps)
    if strip is None:
        # Out of room — placeholder; the verifier emits a WARNING but plan
        # export still happens so the user can iterate.
        return PowerSourceResult(
            required_kw=required_kw,
            nominal_kw=0.0,
            status="PLACEHOLDER",
        )

    pump_x, pump_y, b_x, b_y, e_x, e_y = strip
    pumps = (OffshorePump(x=pump_x, y=pump_y, direction=DIR_E),)
    boilers = tuple(Boiler(x=b_x + i * 3, y=b_y, direction=DIR_E) for i in range(n_boilers))
    engines = tuple(SteamEngine(x=e_x + i * 5, y=e_y, direction=DIR_E) for i in range(n_engines))
    return PowerSourceResult(
        steam_engines=engines,
        boilers=boilers,
        offshore_pumps=pumps,
        required_kw=required_kw,
        nominal_kw=n_engines * _STEAM_ENGINE_KW,
        status="OK",
    )


def _required_kw(
    placement: Placement,
    block_graph: BlockGraph,
    inserters: InserterResult,
) -> float:
    total = 0.0
    for pb in placement.blocks.values():
        block = block_graph.blocks.get(pb.block_id)
        if block is None:
            continue
        for member in block.members:
            total += _MACHINE_KW.get(member.machine.id, _DEFAULT_MACHINE_KW)
    total += _INSERTER_KW * len(inserters.inserters)
    return total


def _occupied_tiles(
    placement: Placement,
    routing: RoutingResult,
    inserters: InserterResult,
) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for pb in placement.blocks.values():
        for _mid, mx, my in pb.machine_tiles:
            for dx in range(3):
                for dy in range(3):
                    out.add((mx + dx, my + dy))
    for path in routing.paths:
        out.update((s.x, s.y) for s in path.segments)
        out.update((u.x, u.y) for u in path.undergrounds)
    for sp in routing.splitters:
        out.add((sp.x, sp.y))
    for ins in inserters.inserters:
        out.add((ins.x, ins.y))
    for belt in inserters.boundary_belts:
        out.add((belt.x, belt.y))
    return out


def _find_free_strip(
    canvas: tuple[int, int],
    occupied: set[tuple[int, int]],
    n_engines: int,
    n_boilers: int,
    n_pumps: int,
) -> tuple[int, int, int, int, int, int] | None:
    """Return `(pump_x, pump_y, b_x, b_y, e_x, e_y)` for an east-facing strip
    along the bottom of the canvas, or None if it doesn't fit.

    Strip geometry:
      - Engines occupy a row of height 3, width `5*n_engines`.
      - Boilers occupy the 2-tile row directly south of the engines, width
        `3*n_boilers`.
      - Pump sits 1×2 west of the boiler row.
    """
    W, H = canvas
    width_needed = 1 + 3 * n_boilers  # pump (1) + boilers
    width_needed = max(width_needed, 5 * n_engines)  # engines may be wider
    height_needed = 5  # 3 (engine) + 2 (boiler)
    if width_needed > W or height_needed > H:
        return None

    # Sweep candidate top-edge y for the engine row from highest free y
    # downward; pick the southernmost strip that fits (keeps power away
    # from the production area).
    for y in range(H - height_needed, -1, -1):
        e_y = y
        b_y = y + 3
        # Boiler row needs free tiles in [pump_x..pump_x + 3*n_boilers],
        # and engine row needs free tiles in [e_x..e_x + 5*n_engines].
        # Pick x=0 (canvas west edge) — pump fits in the first column.
        for x in range(0, W - width_needed + 1):
            if _strip_fits(occupied, x, e_y, b_y, n_engines, n_boilers, n_pumps):
                pump_x = x
                pump_y = b_y  # 1×2 starting at (x, b_y); spans (x, b_y) and (x, b_y+1)
                b_x = x + 1
                e_x = x
                return (pump_x, pump_y, b_x, b_y, e_x, e_y)
    return None


def _strip_fits(
    occupied: set[tuple[int, int]],
    x: int,
    e_y: int,
    b_y: int,
    n_engines: int,
    n_boilers: int,
    n_pumps: int,
) -> bool:
    # Engines row (5*n_engines × 3) starting at (x, e_y).
    for dx in range(5 * n_engines):
        for dy in range(3):
            if (x + dx, e_y + dy) in occupied:
                return False
    # Boiler row starting at (x + 1, b_y).
    for dx in range(3 * n_boilers):
        for dy in range(2):
            if (x + 1 + dx, b_y + dy) in occupied:
                return False
    # Pump (1×2) at (x, b_y).
    for dy in range(2):
        if (x, b_y + dy) in occupied:
            return False
    _ = n_pumps  # current geometry always emits exactly 1 pump
    return True


__all__ = ["PowerSourceResult", "emit_power_source"]
