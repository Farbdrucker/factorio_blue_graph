"""Phase 7a: place inserters where belts touch machines.

For every routed `BeltPath` the source side gets inserters that pick items
off a machine and drop them onto the belt; the sink side gets inserters
that pick items off the belt and drop them onto a machine. Inserter count
scales with `BeltPath.rate` (capped by `INSERTER_THROUGHPUT`), promoting
from `inserter` → `fast-inserter` → `stack-inserter` if the demand exceeds
the available machine face. The `long-handed-inserter` is selected when
the belt port is two tiles away from the nearest machine face along the
inserter axis (a vanilla 1.1 long inserter spans 5 tiles vs the short
inserter's 3).

External raw inputs (``FlowGraph.external_in_edges``) that Phase 6 did
not route are stubbed at the canvas boundary: a single ``BeltSegment``
is dropped on the canvas edge nearest the consuming machine and an
input-inserter bridges that boundary tile to the machine. Phase 6.5/11
will replace this with a real boundary belt.

v1 limitations (documented also in `docs/PLAN.md`):
- Inserter count is capped by the machine face length (3 tiles per 3×3
  assembler). When the rate exceeds even a stack inserter at full face
  density we record an `unresolved` entry — the caller may want to widen
  the lane via Phase 6's `lane_count` or a higher tier.
- Boundary stubs are single-tile markers, not routed belts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from factorio_blue_graph.layout.placement import PlacedBlock, Placement
from factorio_blue_graph.layout.routing import BeltPath, RoutingResult
from factorio_blue_graph.model.blueprint import (
    INSERTER_THROUGHPUT,
    BeltSegment,
    Inserter,
)
from factorio_blue_graph.model.graph import BlockGraph, FlowGraph

# Direction enums in Factorio space (0=N, 2=E, 4=S, 6=W).
_DIR_N, _DIR_E, _DIR_S, _DIR_W = 0, 2, 4, 6
_OPPOSITE = {_DIR_N: _DIR_S, _DIR_S: _DIR_N, _DIR_E: _DIR_W, _DIR_W: _DIR_E}
# Per-direction unit step.
_STEP = {_DIR_N: (0, -1), _DIR_E: (1, 0), _DIR_S: (0, 1), _DIR_W: (-1, 0)}

# Promotion ladder used when the base inserter cannot keep up with the
# requested rate at full machine-face density.
_PROMOTION_LADDER = ("inserter", "fast-inserter", "stack-inserter")


class InserterError(Exception):
    """Raised on internal inconsistencies in the inserter placer."""


@dataclass(frozen=True)
class InserterResult:
    inserters: tuple[Inserter, ...]
    boundary_belts: tuple[BeltSegment, ...]
    unresolved: tuple[str, ...]


def place_inserters(
    placement: Placement,
    block_graph: BlockGraph,
    flow_graph: FlowGraph,
    routing_result: RoutingResult,
    *,
    emit_boundary_stubs: bool = True,
) -> InserterResult:
    """Emit inserters and boundary stubs for every belt endpoint.

    `emit_boundary_stubs` (default True for backwards compat): when True,
    every `FlowGraph.external_in_edges` entry gets a 1-tile floating belt
    at the canvas edge plus a sink inserter. When False, raw-input stubs
    are skipped — the caller is expected to handle them via
    `layout.io_chests.place_io_chests` instead, which is the path used
    by `fbg plan` to produce functional blueprints.
    """
    inserters: list[Inserter] = []
    boundary_belts: list[BeltSegment] = []
    unresolved: list[str] = []
    used: set[tuple[int, int]] = set()
    # Belt tiles are obstacles for inserters (an inserter cannot share a
    # tile with a belt segment or underground).
    belt_tiles = _belt_tile_set(routing_result)

    for path in routing_result.paths:
        # Source side: inserter drops onto the belt (drop_dir = outward).
        ok_src = _stamp_endpoint(
            placement=placement,
            block_id=path.source_block,
            port_x=path.segments[0].x,
            port_y=path.segments[0].y,
            rate=_path_rate(path, block_graph),
            role="source",
            inserters=inserters,
            used=used,
            belt_tiles=belt_tiles,
        )
        ok_snk = _stamp_endpoint(
            placement=placement,
            block_id=path.target_block,
            port_x=path.segments[-1].x,
            port_y=path.segments[-1].y,
            rate=_path_rate(path, block_graph),
            role="sink",
            inserters=inserters,
            used=used,
            belt_tiles=belt_tiles,
        )
        if not (ok_src and ok_snk):
            unresolved.append(f"{path.source_block}->{path.target_block}:{path.item_id}")

    if not emit_boundary_stubs:
        return InserterResult(
            inserters=tuple(inserters),
            boundary_belts=(),
            unresolved=tuple(unresolved),
        )

    for ext in flow_graph.external_in_edges:
        try:
            block = block_graph.block_of(ext.consumer_node)
        except KeyError:
            unresolved.append(f"external:{ext.item_id}->{ext.consumer_node}")
            continue
        placed = placement.blocks.get(block.id)
        if placed is None:
            unresolved.append(f"external:{ext.item_id}->{ext.consumer_node}")
            continue
        mtile = _machine_tile(placed, ext.consumer_node)
        if mtile is None:
            unresolved.append(f"external:{ext.item_id}->{ext.consumer_node}")
            continue
        belt_dir = _nearest_canvas_edge(placement.canvas, mtile)
        belt_xy = _boundary_tile(placement.canvas, mtile, belt_dir)
        boundary_belts.append(
            BeltSegment(
                x=belt_xy[0],
                y=belt_xy[1],
                direction=_OPPOSITE[belt_dir],  # belt flows inward (away from edge)
                name="transport-belt",
            )
        )
        belt_tiles_ext = belt_tiles | {belt_xy}
        ok = _stamp_endpoint(
            placement=placement,
            block_id=block.id,
            port_x=belt_xy[0],
            port_y=belt_xy[1],
            rate=ext.rate,
            role="sink",
            inserters=inserters,
            used=used,
            belt_tiles=belt_tiles_ext,
        )
        if not ok:
            unresolved.append(f"external:{ext.item_id}->{ext.consumer_node}")

    return InserterResult(
        inserters=tuple(inserters),
        boundary_belts=tuple(boundary_belts),
        unresolved=tuple(unresolved),
    )


# ---------------------------------------------------------------------------
# Endpoint stamping
# ---------------------------------------------------------------------------


def _stamp_endpoint(
    *,
    placement: Placement,
    block_id: str,
    port_x: int,
    port_y: int,
    rate: float,
    role: str,
    inserters: list[Inserter],
    used: set[tuple[int, int]],
    belt_tiles: set[tuple[int, int]],
) -> bool:
    """Emit inserters at one belt endpoint. Returns False if it cannot be placed."""
    placed = placement.blocks[block_id]
    outward = _outward_side(placed, port_x, port_y)
    if outward is None:
        # The port is not on a block face (e.g. an external boundary stub
        # whose tile sits on the canvas edge instead). Fall back to picking
        # the face nearest the port.
        outward = _nearest_face(placed, port_x, port_y)
    inward = _OPPOSITE[outward]
    ix, iy = port_x + _STEP[inward][0], port_y + _STEP[inward][1]
    nearest = _nearest_machine_to(placed, ix, iy)
    if nearest is None:
        return False
    _mid, mx, my, mw, mh = nearest
    # Distance from the inserter tile to the nearest machine tile along
    # the inward axis. 1 → short inserter (drop tile coincides with the
    # machine face); 2 → long-handed inserter.
    reach = _axial_distance(ix, iy, inward, mx, my, mw, mh)
    if reach not in (1, 2):
        return False
    base_name = "long-handed-inserter" if reach == 2 else "inserter"
    drop_dir = outward if role == "source" else inward
    # How many inserters do we need at this endpoint?
    name, count = _pick_inserter_kind(base_name, rate)
    face_tiles = _face_tiles(placed, outward, mx, my, mw, mh, port_x, port_y, inward)
    placed_count = 0
    for tx, ty in face_tiles:
        if placed_count >= count:
            break
        if (tx, ty) in used or (tx, ty) in belt_tiles:
            continue
        inserters.append(Inserter(x=tx, y=ty, direction=drop_dir, name=name))
        used.add((tx, ty))
        placed_count += 1
    return placed_count != 0


def _pick_inserter_kind(base_name: str, rate: float) -> tuple[str, int]:
    """Choose (name, count) such that `count * throughput(name) >= rate`.

    For long-handed inserters we don't promote (they're a geometry need,
    not a throughput choice) — we just stamp the right count. For short
    inserters we walk the promotion ladder until `count` fits within the
    3-tile machine face. If even a stack inserter at full density falls
    short, we return (stack, 3) and let the caller record an unresolved.
    """
    if base_name == "long-handed-inserter":
        n = max(1, math.ceil(rate / INSERTER_THROUGHPUT[base_name]))
        return base_name, min(n, 3)
    for name in _PROMOTION_LADDER:
        n = max(1, math.ceil(rate / INSERTER_THROUGHPUT[name]))
        if n <= 3:
            return name, n
    return "stack-inserter", 3


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def _outward_side(placed: PlacedBlock, x: int, y: int) -> int | None:
    """Return the Factorio cardinal direction normal to the block face on
    which `(x, y)` sits, or None if the tile is not on a block face."""
    on_north = y == placed.y
    on_south = y == placed.y + placed.height - 1
    on_west = x == placed.x
    on_east = x == placed.x + placed.width - 1
    inside_x = placed.x <= x < placed.x + placed.width
    inside_y = placed.y <= y < placed.y + placed.height
    if on_north and inside_x:
        return _DIR_N
    if on_south and inside_x:
        return _DIR_S
    if on_west and inside_y:
        return _DIR_W
    if on_east and inside_y:
        return _DIR_E
    return None


def _nearest_face(placed: PlacedBlock, x: int, y: int) -> int:
    """Closest block face for an off-face tile (used for boundary stubs)."""
    candidates = {
        _DIR_N: abs(y - placed.y),
        _DIR_S: abs(y - (placed.y + placed.height - 1)),
        _DIR_W: abs(x - placed.x),
        _DIR_E: abs(x - (placed.x + placed.width - 1)),
    }
    return min(candidates, key=lambda d: candidates[d])


def _nearest_machine_to(
    placed: PlacedBlock, x: int, y: int
) -> tuple[str, int, int, int, int] | None:
    """Return `(machine_id, mx, my, mw, mh)` of the machine closest to `(x, y)`.

    Footprint is read from the block's members; we look it up via
    placement-side info kept on `placed.machine_tiles` (which only stores
    `(id, mx, my)`). Footprint defaults to `(3, 3)` — the vanilla 1.1
    assembler/chem-plant size — when no per-machine footprint lookup is
    available at this layer.
    """
    if not placed.machine_tiles:
        return None
    best: tuple[str, int, int, int, int] | None = None
    best_d = math.inf
    for mid, mx, my in placed.machine_tiles:
        mw, mh = 3, 3
        # Chebyshev distance from (x,y) to nearest tile of the machine box.
        dx = max(mx - x, 0, x - (mx + mw - 1))
        dy = max(my - y, 0, y - (my + mh - 1))
        d = dx + dy
        if d < best_d:
            best_d = d
            best = (mid, mx, my, mw, mh)
    return best


def _axial_distance(ix: int, iy: int, inward: int, mx: int, my: int, mw: int, mh: int) -> int:
    """Tiles separating the inserter at `(ix, iy)` and the machine face
    along the inward step direction. `1` = the machine tile is immediately
    one step inward (short inserter); `2` = one tile of margin in between
    (long-handed). Other values mean the geometry doesn't fit a 1×1
    inserter and the caller should bail out.
    """
    dx, dy = _STEP[inward]
    # Walk inward; we accept up to 3 tiles before declaring "not adjacent".
    for k in range(1, 4):
        tx, ty = ix + dx * k, iy + dy * k
        if mx <= tx < mx + mw and my <= ty < my + mh:
            return k
    return -1


def _face_tiles(
    placed: PlacedBlock,
    outward: int,
    mx: int,
    my: int,
    mw: int,
    mh: int,
    port_x: int,
    port_y: int,
    inward: int,
) -> list[tuple[int, int]]:
    """Inserter-candidate tiles along the machine's outward-facing face.

    The inserter tile sits one step inward from the belt port. We start
    with that exact tile and fan outward along the machine face so a
    multi-inserter endpoint stays adjacent to its own port.
    """
    dx, dy = _STEP[inward]
    base_x = port_x + dx
    base_y = port_y + dy
    if outward in (_DIR_N, _DIR_S):
        # Machine face runs along x; vary x across [mx, mx+mw)
        xs = sorted(range(mx, mx + mw), key=lambda v: abs(v - base_x))
        return [(v, base_y) for v in xs]
    # outward in (E, W): face runs along y
    ys = sorted(range(my, my + mh), key=lambda v: abs(v - base_y))
    return [(base_x, v) for v in ys]


def _belt_tile_set(routing_result: RoutingResult) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for path in routing_result.paths:
        out.update((s.x, s.y) for s in path.segments)
        out.update((u.x, u.y) for u in path.undergrounds)
    for sp in routing_result.splitters:
        out.add((sp.x, sp.y))
    return out


def _path_rate(path: BeltPath, block_graph: BlockGraph) -> float:
    """Look up the BlockEdge rate matching this BeltPath."""
    for edge in block_graph.edges:
        if (
            edge.source_block == path.source_block
            and edge.target_block == path.target_block
            and edge.item_id == path.item_id
        ):
            return edge.rate
    return 0.0


def _machine_tile(placed: PlacedBlock, machine_id: str) -> tuple[int, int] | None:
    for mid, mx, my in placed.machine_tiles:
        if mid == machine_id:
            return (mx, my)
    return None


def _nearest_canvas_edge(canvas: tuple[int, int], tile: tuple[int, int]) -> int:
    """Return the Factorio cardinal direction toward the nearest canvas edge."""
    W, H = canvas
    x, y = tile
    candidates = {
        _DIR_N: y,
        _DIR_S: H - 1 - y,
        _DIR_W: x,
        _DIR_E: W - 1 - x,
    }
    return min(candidates, key=lambda d: candidates[d])


def _boundary_tile(canvas: tuple[int, int], tile: tuple[int, int], outward: int) -> tuple[int, int]:
    """Tile on the canvas edge in direction `outward` aligned with `tile`."""
    W, H = canvas
    x, y = tile
    if outward == _DIR_N:
        return (x, 0)
    if outward == _DIR_S:
        return (x, H - 1)
    if outward == _DIR_W:
        return (0, y)
    return (W - 1, y)


__all__ = [
    "InserterError",
    "InserterResult",
    "place_inserters",
]
