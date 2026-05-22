"""Repair passes — one function per issue code.

Each pass takes a list of `Issue`s of the same code plus the mutable
`PlanState` and rewrites the IR in place. The loop selects exactly one
pass per iteration to keep behaviour deterministic.
"""

from __future__ import annotations

from factorio_blue_graph.layout.power_source import emit_power_source
from factorio_blue_graph.model.blueprint import (
    DIR_E,
    DIR_N,
    DIR_S,
    DIR_W,
    Chest,
    Inserter,
    Pole,
)
from factorio_blue_graph.repair.state import PlanState
from factorio_blue_graph.verify.report import Issue

_STEP = {DIR_N: (0, -1), DIR_E: (1, 0), DIR_S: (0, 1), DIR_W: (-1, 0)}
_OPPOSITE = {DIR_N: DIR_S, DIR_S: DIR_N, DIR_E: DIR_W, DIR_W: DIR_E}


# ---------------------------------------------------------------------------
# ORPHAN_INSERTER → just delete it
# ---------------------------------------------------------------------------


def drop_orphan_inserters(issues: list[Issue], state: PlanState) -> int:
    """Delete every inserter whose tile is named in the issue payload."""
    targets: set[tuple[int, int]] = set()
    for issue in issues:
        loc = issue.location
        if loc is not None:
            targets.add(loc)
    if not targets:
        return 0
    keep = tuple(i for i in state.inserters.inserters if (i.x, i.y) not in targets)
    removed = len(state.inserters.inserters) - len(keep)
    state.replace_inserters(keep)
    return removed


# ---------------------------------------------------------------------------
# WRONG_INSERTER_DIRECTION → flip the inserter
# ---------------------------------------------------------------------------


def flip_inserter_direction(issues: list[Issue], state: PlanState) -> int:
    """Flip every miss-oriented inserter's drop direction by 180°."""
    flipped: dict[tuple[int, int], int] = {}
    for issue in issues:
        loc = issue.location
        if loc is None:
            continue
        drop = int(issue.payload.get("drop_dir", DIR_N))
        flipped[loc] = _OPPOSITE[drop]
    if not flipped:
        return 0
    new_inserters = tuple(
        Inserter(x=i.x, y=i.y, direction=flipped[(i.x, i.y)], name=i.name)
        if (i.x, i.y) in flipped
        else i
        for i in state.inserters.inserters
    )
    state.replace_inserters(new_inserters)
    return len(flipped)


# ---------------------------------------------------------------------------
# MISSING_INSERTER → stamp one onto the named machine's face
# ---------------------------------------------------------------------------


def add_inserters_for_missing(issues: list[Issue], state: PlanState) -> int:
    """For each MISSING_INSERTER issue, drop an inserter on a free face tile.

    We pick the face nearest to the canvas edge for input inserters (so a
    raw chest can later be placed there), and the face nearest to free
    space for outputs. We don't promote to long-handed here — repair
    success is "any" inserter, throughput refinement is a separate pass.
    """
    if not issues:
        return 0
    used_inserter_tiles = {(i.x, i.y) for i in state.inserters.inserters}
    occupied = _occupied_tile_set(state)
    placed = 0
    for issue in issues:
        payload = issue.payload
        mid = payload.get("machine_id")
        if mid is None:
            continue
        mx, my = payload["top_left"]
        fw, fh = payload.get("footprint", (3, 3))
        role = payload.get("role", "input")
        block_id = _block_of_machine(state, mid)
        placed_block = state.placement.blocks.get(block_id) if block_id else None
        if placed_block is None:
            continue

        # Try each face: north, south, east, west — picking the first one
        # with a free adjacent tile for the inserter and a free belt tile
        # one further step out.
        for outward in (DIR_N, DIR_S, DIR_E, DIR_W):
            inward = _OPPOSITE[outward]
            # inserter tile = one step outward from the machine face
            face_tile = _face_tile_for_direction(mx, my, fw, fh, outward)
            ix, iy = face_tile
            belt_x, belt_y = ix + _STEP[outward][0], iy + _STEP[outward][1]
            if not (0 <= ix < state.placement.canvas[0] and 0 <= iy < state.placement.canvas[1]):
                continue
            if (ix, iy) in used_inserter_tiles:
                continue
            if (ix, iy) in occupied:
                continue
            # We need a place to drop/pickup. For role="input" the belt
            # tile (further out) is the pickup → must be free for a
            # subsequent belt or chest. For "output" the belt tile is
            # where we'd later route a belt; ditto.
            if (belt_x, belt_y) in occupied:
                continue
            drop_dir = inward if role == "input" else outward
            state.add_inserter(Inserter(x=ix, y=iy, direction=drop_dir, name="inserter"))
            used_inserter_tiles.add((ix, iy))
            occupied.add((ix, iy))
            placed += 1
            break
    return placed


def _face_tile_for_direction(mx: int, my: int, fw: int, fh: int, outward: int) -> tuple[int, int]:
    """Pick a tile one step outside the machine's face on `outward`."""
    if outward == DIR_N:
        return (mx + fw // 2, my - 1)
    if outward == DIR_S:
        return (mx + fw // 2, my + fh)
    if outward == DIR_W:
        return (mx - 1, my + fh // 2)
    return (mx + fw, my + fh // 2)


# ---------------------------------------------------------------------------
# STARVED_INPUT → emit a chest at the canvas edge nearest the consumer
# ---------------------------------------------------------------------------


def add_external_source_chests(issues: list[Issue], state: PlanState) -> int:
    """For each starved raw input emit a `wooden-chest` at the canvas edge
    nearest the consumer machine and an inserter feeding the machine. If
    the machine already has an input inserter pointed at an empty tile,
    we drop the chest directly there; otherwise we add a new inserter
    too.
    """
    placed = 0
    occupied = _occupied_tile_set(state)
    used_inserter_tiles = {(i.x, i.y) for i in state.inserters.inserters}
    W, H = state.placement.canvas
    for issue in issues:
        mid = issue.payload.get("machine_id")
        item_id = issue.payload.get("item_id")
        if not mid or not item_id:
            continue
        mtile = issue.payload.get("machine_top_left")
        if mtile is None:
            continue
        mx, my = mtile
        # Pick the outward direction nearest a canvas edge.
        outward = _nearest_edge(mx, my, W, H)
        inward = _OPPOSITE[outward]
        # Inserter tile: directly adjacent to the machine face.
        fw, fh = (3, 3)
        ins_tile = _face_tile_for_direction(mx, my, fw, fh, outward)
        chest_tile = (ins_tile[0] + _STEP[outward][0], ins_tile[1] + _STEP[outward][1])
        # Clamp the chest tile so it sits on the canvas edge if the
        # machine is already near it.
        chest_tile = _clamp_to_canvas(chest_tile, W, H)
        if chest_tile in occupied:
            continue
        # Place the chest.
        state.chests.append(
            Chest(
                x=chest_tile[0],
                y=chest_tile[1],
                name="wooden-chest",
                item_id=item_id,
            )
        )
        occupied.add(chest_tile)
        # If there's no inserter on `ins_tile` already, add one.
        if ins_tile not in used_inserter_tiles and ins_tile not in occupied:
            state.add_inserter(
                Inserter(x=ins_tile[0], y=ins_tile[1], direction=inward, name="inserter")
            )
            used_inserter_tiles.add(ins_tile)
            occupied.add(ins_tile)
        placed += 1
    return placed


def _nearest_edge(x: int, y: int, W: int, H: int) -> int:
    candidates = {
        DIR_N: y,
        DIR_S: H - 1 - y,
        DIR_W: x,
        DIR_E: W - 1 - x,
    }
    return min(candidates, key=lambda d: candidates[d])


def _clamp_to_canvas(tile: tuple[int, int], W: int, H: int) -> tuple[int, int]:
    return (max(0, min(tile[0], W - 1)), max(0, min(tile[1], H - 1)))


# ---------------------------------------------------------------------------
# UNPOWERED_MACHINE / UNPOWERED_INSERTER → add a pole
# ---------------------------------------------------------------------------


def add_pole_for_unpowered(issues: list[Issue], state: PlanState) -> int:
    """Drop a medium electric pole on a free tile within range of the
    flagged target. We pick the closest unoccupied tile to the target,
    not solving the full set-cover — sufficient for repairing a small
    number of stragglers.
    """
    occupied = _occupied_tile_set(state)
    occupied.update((p.x, p.y) for p in state.power.poles)
    placed = 0
    for issue in issues:
        loc = issue.location
        if loc is None:
            continue
        cand = _find_free_pole_tile(loc, occupied, state.placement.canvas)
        if cand is None:
            continue
        state.add_pole(Pole(x=cand[0], y=cand[1], name="medium-electric-pole"))
        occupied.add(cand)
        placed += 1
    return placed


def _find_free_pole_tile(
    target: tuple[int, int],
    occupied: set[tuple[int, int]],
    canvas: tuple[int, int],
) -> tuple[int, int] | None:
    W, H = canvas
    # Search a 7×7 box around the target for any free tile.
    for r in range(0, 4):
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                if abs(dx) != r and abs(dy) != r and r > 0:
                    continue
                tx, ty = target[0] + dx, target[1] + dy
                if 0 <= tx < W and 0 <= ty < H and (tx, ty) not in occupied:
                    return (tx, ty)
    return None


# ---------------------------------------------------------------------------
# NO_POWER_SOURCE → emit a steam-engine cluster sized to demand
# ---------------------------------------------------------------------------


def emit_steam_cluster(_issues: list[Issue], state: PlanState) -> int:
    new_power = emit_power_source(
        state.placement, state.block_graph, state.routing, state.inserters
    )
    state.power_source = new_power
    return 1 if new_power.steam_engines else 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _block_of_machine(state: PlanState, machine_id: str) -> str | None:
    try:
        block = state.block_graph.block_of(machine_id)
        return block.id
    except KeyError:
        return None


def _occupied_tile_set(state: PlanState) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for pb in state.placement.blocks.values():
        for _mid, mx, my in pb.machine_tiles:
            for dx in range(3):
                for dy in range(3):
                    out.add((mx + dx, my + dy))
    for path in state.routing.paths:
        out.update((s.x, s.y) for s in path.segments)
        out.update((u.x, u.y) for u in path.undergrounds)
    for sp in state.routing.splitters:
        out.add((sp.x, sp.y))
    for ins in state.inserters.inserters:
        out.add((ins.x, ins.y))
    for belt in state.inserters.boundary_belts:
        out.add((belt.x, belt.y))
    for chest in state.chests:
        out.add((chest.x, chest.y))
    return out


__all__ = [
    "add_external_source_chests",
    "add_inserters_for_missing",
    "add_pole_for_unpowered",
    "drop_orphan_inserters",
    "emit_steam_cluster",
    "flip_inserter_direction",
]
