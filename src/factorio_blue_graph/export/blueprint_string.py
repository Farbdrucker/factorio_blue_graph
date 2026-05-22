"""Phase 9: Factorio 1.1 blueprint string encoder.

Build the Factorio JSON from all pipeline outputs, compress with zlib,
base64-encode, and prefix with "0" to produce the importable blueprint
string.

Encoding round-trip:
    "0" + base64(zlib.compress(json.dumps(blueprint_dict).encode()))
Inverse (decode) is exposed for testing and debugging.
"""

from __future__ import annotations

import base64
import json
import zlib

from factorio_blue_graph.layout.inserter import InserterResult
from factorio_blue_graph.layout.placement import Placement
from factorio_blue_graph.layout.power import PoleResult
from factorio_blue_graph.layout.routing import RoutingResult
from factorio_blue_graph.model.blueprint import (
    BeltSegment,
    Boiler,
    Chest,
    OffshorePump,
    PowerSourceResult,
    Splitter,
    SteamEngine,
)
from factorio_blue_graph.model.graph import BlockGraph

# Factorio 1.1.43 version word (major=1, minor=1, patch=43, build=0).
_VERSION = 281479274496000

_DIR_N, _DIR_E, _DIR_S, _DIR_W = 0, 2, 4, 6


def encode(
    placement: Placement,
    block_graph: BlockGraph,
    routing_result: RoutingResult,
    inserter_result: InserterResult,
    pole_result: PoleResult,
    label: str = "FBG Blueprint",
    power_source: PowerSourceResult | None = None,
    chests: tuple[Chest, ...] = (),
) -> str:
    """Encode pipeline outputs as an importable Factorio 1.1 blueprint string."""
    entities: list[dict] = []

    # Tiles occupied by splitters — belt segments there must be suppressed
    # because the trunk path records a belt segment at every tile it crosses,
    # including tiles that later become splitter anchors for Steiner branches.
    sp_tiles: set[tuple[int, int]] = set()
    for sp in routing_result.splitters:
        sp_tiles.update(_splitter_tile_pair(sp))

    # Single seen-set shared across all surface belt emitters so boundary belts
    # and routed belts can't collide.
    belt_seen: set[tuple[int, int]] = set()

    _emit_machines(entities, placement, block_graph)
    _emit_belt_paths(entities, routing_result, sp_tiles, belt_seen)
    _emit_splitters(entities, routing_result)
    _emit_inserters(entities, inserter_result)
    _emit_boundary_belts(entities, inserter_result, sp_tiles, belt_seen)
    _emit_poles(entities, pole_result)
    _emit_chests(entities, chests)
    if power_source is not None:
        _emit_power_source(entities, power_source)

    for i, ent in enumerate(entities, start=1):
        ent["entity_number"] = i

    blueprint = {
        "blueprint": {
            "item": "blueprint",
            "label": label,
            "entities": entities,
            "icons": _build_icons(block_graph),
            "version": _VERSION,
        }
    }
    raw = json.dumps(blueprint, separators=(",", ":")).encode("utf-8")
    return "0" + base64.b64encode(zlib.compress(raw, level=9)).decode("ascii")


def decode(blueprint_string: str) -> dict:
    """Decode a Factorio blueprint string to its JSON dict. Inverse of `encode`."""
    if not blueprint_string.startswith("0"):
        raise ValueError("not a Factorio blueprint string (missing leading '0')")
    return json.loads(zlib.decompress(base64.b64decode(blueprint_string[1:])))


# ---------------------------------------------------------------------------
# Entity emitters
# ---------------------------------------------------------------------------


def _emit_machines(
    entities: list[dict],
    placement: Placement,
    block_graph: BlockGraph,
) -> None:
    for pb in placement.blocks.values():
        block = block_graph.blocks.get(pb.block_id)
        if block is None:
            continue
        member_by_id = {m.id: m for m in block.members}
        for machine_id, mx, my in pb.machine_tiles:
            node = member_by_id.get(machine_id)
            if node is None:
                continue
            fw, fh = node.machine.footprint
            entities.append(
                {
                    "name": node.machine.id,
                    "position": {"x": mx + fw / 2, "y": my + fh / 2},
                    "direction": 0,
                    "recipe": node.recipe_id,
                }
            )


def _emit_belt_paths(
    entities: list[dict],
    routing_result: RoutingResult,
    sp_tiles: set[tuple[int, int]],
    belt_seen: set[tuple[int, int]],
) -> None:
    for path in routing_result.paths:
        for seg in path.segments:
            tile = (seg.x, seg.y)
            if tile in sp_tiles or tile in belt_seen:
                continue
            belt_seen.add(tile)
            entities.append(_belt_dict(seg))
        for ug in path.undergrounds:
            entities.append(_underground_dict(ug))


def _emit_splitters(entities: list[dict], routing_result: RoutingResult) -> None:
    for sp in routing_result.splitters:
        pos_x, pos_y = _splitter_center(sp)
        entities.append(
            {
                "name": sp.name,
                "position": {"x": pos_x, "y": pos_y},
                "direction": sp.direction,
            }
        )


def _emit_inserters(entities: list[dict], inserter_result: InserterResult) -> None:
    for ins in inserter_result.inserters:
        entities.append(
            {
                "name": ins.name,
                "position": {"x": ins.x + 0.5, "y": ins.y + 0.5},
                "direction": ins.direction,
            }
        )


def _emit_boundary_belts(
    entities: list[dict],
    inserter_result: InserterResult,
    sp_tiles: set[tuple[int, int]],
    belt_seen: set[tuple[int, int]],
) -> None:
    for seg in inserter_result.boundary_belts:
        tile = (seg.x, seg.y)
        if tile in sp_tiles or tile in belt_seen:
            continue
        belt_seen.add(tile)
        entities.append(_belt_dict(seg))


def _emit_poles(entities: list[dict], pole_result: PoleResult) -> None:
    for pole in pole_result.poles:
        entities.append(
            {
                "name": pole.name,
                "position": {"x": pole.x + 0.5, "y": pole.y + 0.5},
            }
        )


def _emit_chests(entities: list[dict], chests: tuple[Chest, ...]) -> None:
    """Emit each chest with a request-filter slot showing the item it should
    hold. Vanilla wooden-chests don't support request-filters at runtime,
    but blueprints accept the `request_filters` array and display the item
    icon on the chest preview — a clear hint to the player about what to
    drop in.
    """
    for chest in chests:
        ent: dict = {
            "name": chest.name,
            "position": {"x": chest.x + 0.5, "y": chest.y + 0.5},
        }
        if chest.item_id:
            # `request_filters` slot is honored by logistic chests; for a
            # wooden chest the icon still renders in the preview, marking
            # what should go inside.
            ent["request_filters"] = [{"index": 1, "name": chest.item_id, "count": 0}]
        entities.append(ent)


def _emit_power_source(entities: list[dict], ps: PowerSourceResult) -> None:
    """Emit steam-engine + boiler + offshore-pump entities.

    Positions are entity centers per Factorio conventions:
      - steam-engine is 3×5 → center is (x+1.5, y+2.5) for north-south, etc.
      - boiler is 3×2 → center is (x+1.5, y+1.0)
      - offshore-pump is 1×2 → center is (x+0.5, y+1.0)
    """
    for se in ps.steam_engines:
        cx, cy = _steam_engine_center(se)
        entities.append(
            {
                "name": "steam-engine",
                "position": {"x": cx, "y": cy},
                "direction": se.direction,
            }
        )
    for bo in ps.boilers:
        cx, cy = _boiler_center(bo)
        entities.append(
            {
                "name": "boiler",
                "position": {"x": cx, "y": cy},
                "direction": bo.direction,
            }
        )
    for pump in ps.offshore_pumps:
        cx, cy = _pump_center(pump)
        entities.append(
            {
                "name": "offshore-pump",
                "position": {"x": cx, "y": cy},
                "direction": pump.direction,
            }
        )


def _steam_engine_center(se: SteamEngine) -> tuple[float, float]:
    if se.direction in (_DIR_E, _DIR_W):
        return (se.x + 5 / 2, se.y + 3 / 2)
    return (se.x + 3 / 2, se.y + 5 / 2)


def _boiler_center(bo: Boiler) -> tuple[float, float]:
    if bo.direction in (_DIR_E, _DIR_W):
        return (bo.x + 3 / 2, bo.y + 2 / 2)
    return (bo.x + 2 / 2, bo.y + 3 / 2)


def _pump_center(pump: OffshorePump) -> tuple[float, float]:
    if pump.direction in (_DIR_E, _DIR_W):
        return (pump.x + 1.0, pump.y + 0.5)
    return (pump.x + 0.5, pump.y + 1.0)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _belt_dict(seg: BeltSegment) -> dict:
    return {
        "name": seg.name,
        "position": {"x": seg.x + 0.5, "y": seg.y + 0.5},
        "direction": seg.direction,
    }


def _underground_dict(ug: object) -> dict:
    return {
        "name": ug.name,  # type: ignore[attr-defined]
        "position": {"x": ug.x + 0.5, "y": ug.y + 0.5},  # type: ignore[attr-defined]
        "direction": ug.direction,  # type: ignore[attr-defined]
        "type": ug.io_type,  # type: ignore[attr-defined]
    }


def _splitter_center(sp: Splitter) -> tuple[float, float]:
    """Geometric center of the 2-tile splitter footprint.

    The anchor tile `(sp.x, sp.y)` is the "first" tile; the second tile is
    one step to the right of the facing direction (see `model/blueprint.py`).

    Facing N (dir 0): second tile at (+1, 0) → tiles {sx, sx+1} × {sy}.
    Facing E (dir 2): second tile at (0, +1) → tiles {sx} × {sy, sy+1}.
    Facing S (dir 4): second tile at (-1, 0) → tiles {sx-1, sx} × {sy}.
    Facing W (dir 6): second tile at (0, -1) → tiles {sx} × {sy-1, sy}.
    """
    sx, sy, d = sp.x, sp.y, sp.direction
    if d == _DIR_N:
        return (sx + 1.0, sy + 0.5)
    if d == _DIR_E:
        return (sx + 0.5, sy + 1.0)
    if d == _DIR_S:
        return (float(sx), sy + 0.5)
    # _DIR_W
    return (sx + 0.5, float(sy))


def _splitter_tile_pair(sp: Splitter) -> tuple[tuple[int, int], tuple[int, int]]:
    """Both grid tiles occupied by the splitter entity."""
    sx, sy, d = sp.x, sp.y, sp.direction
    if d == _DIR_N:
        return (sx, sy), (sx + 1, sy)
    if d == _DIR_E:
        return (sx, sy), (sx, sy + 1)
    if d == _DIR_S:
        return (sx, sy), (sx - 1, sy)
    # _DIR_W
    return (sx, sy), (sx, sy - 1)


def _build_icons(block_graph: BlockGraph) -> list[dict]:
    seen: set[str] = set()
    icons: list[dict] = []
    for block in block_graph.blocks.values():
        rid = block.primary_recipe
        if not rid or rid in seen:
            continue
        seen.add(rid)
        icons.append({"signal": {"type": "item", "name": rid}, "index": len(icons) + 1})
        if len(icons) >= 4:
            break
    if not icons:
        icons = [{"signal": {"type": "item", "name": "transport-belt"}, "index": 1}]
    return icons


__all__ = ["decode", "encode"]
