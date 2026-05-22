"""Belt-graph checks.

* `DISCONNECTED_BELT`: walking each `BeltPath` from its first segment in
  the segment's flow direction must reach the last segment, hopping over
  matched underground pairs.
* `BELT_DIRECTION_CONFLICT`: two adjacent surface belts on the trunk
  must not face each other head-to-head.
* `ORPHAN_UNDERGROUND`: every `io_type="input"` underground must have a
  matching `"output"` underground in its facing direction within the
  tier's max gap (capped at 10 tiles for express belts).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from factorio_blue_graph.model.blueprint import DIR_E, DIR_N, DIR_S, DIR_W
from factorio_blue_graph.verify.grid import (
    BeltCell,
    InserterCell,
    SplitterCell,
    TileGrid,
    UndergroundCell,
)
from factorio_blue_graph.verify.report import Issue, Severity, VerifyReport

if TYPE_CHECKING:
    from factorio_blue_graph.layout.routing import RoutingResult

# Direction → unit step. Mirrors model/blueprint.py constants.
_STEP = {DIR_N: (0, -1), DIR_E: (1, 0), DIR_S: (0, 1), DIR_W: (-1, 0)}
_OPPOSITE = {DIR_N: DIR_S, DIR_S: DIR_N, DIR_E: DIR_W, DIR_W: DIR_E}
# Underground belt max jump per tier name. Used by the string-only check
# where we don't know the planner's BeltTier enum.
_UG_MAX = {
    "underground-belt": 6,
    "fast-underground-belt": 8,
    "express-underground-belt": 10,
}


def check(grid: TileGrid, routing: RoutingResult, report: VerifyReport) -> None:
    """Run belt checks with full planner IR available."""
    for path_idx, path in enumerate(routing.paths):
        if not path.segments and not path.undergrounds:
            continue
        _check_path_continuity(grid, path, path_idx, report)
    _check_underground_pairs(grid, report)
    _check_belt_direction_conflicts(grid, report)


def check_string_only(grid: TileGrid, report: VerifyReport) -> None:
    """Subset that runs from a decoded blueprint with no `BeltPath` info."""
    _check_underground_pairs(grid, report)
    _check_belt_direction_conflicts(grid, report)


# ---------------------------------------------------------------------------
# Per-path continuity walk
# ---------------------------------------------------------------------------


def _check_path_continuity(grid: TileGrid, path, path_idx: int, report: VerifyReport) -> None:
    """Walk `path` from its first segment by following arrows until we hit
    the last segment (or a splitter on the trunk, which counts as reaching
    the trunk endpoint). Emit `DISCONNECTED_BELT` when the walk breaks.
    """
    first = path.segments[0] if path.segments else path.undergrounds[0]
    last_target = (path.segments[-1].x, path.segments[-1].y) if path.segments else None

    x, y = first.x, first.y
    visited: set[tuple[int, int]] = {(x, y)}
    direction = first.direction
    max_steps = grid.canvas[0] * grid.canvas[1]  # absolute upper bound
    for _ in range(max_steps):
        # Have we reached the path's last segment?
        if (x, y) == last_target:
            return
        cell = grid.get(x, y)
        if isinstance(cell, BeltCell):
            direction = cell.direction
            nx, ny = x + _STEP[direction][0], y + _STEP[direction][1]
        elif isinstance(cell, UndergroundCell):
            if cell.io_type == "input":
                # Jump to the matching output along `direction`.
                jump = _find_underground_exit(grid, x, y, cell.direction, cell.name)
                if jump is None:
                    report.issues.append(
                        Issue(
                            code="ORPHAN_UNDERGROUND",
                            severity=Severity.ERROR,
                            message=(
                                f"belt {path_idx} ({path.item_id}): underground input at "
                                f"({x},{y}) has no matching output"
                            ),
                            location=(x, y),
                            payload={"path_idx": path_idx, "item_id": path.item_id},
                        )
                    )
                    return
                direction = cell.direction
                nx, ny = jump
            else:
                # We're on an output tile; the next step is one tile forward.
                direction = cell.direction
                nx, ny = x + _STEP[direction][0], y + _STEP[direction][1]
        elif isinstance(cell, SplitterCell):
            # Trunk reached via splitter; reaching the splitter for a Steiner
            # branch counts as connecting to the trunk.
            return
        else:
            report.issues.append(
                Issue(
                    code="DISCONNECTED_BELT",
                    severity=Severity.ERROR,
                    message=(
                        f"belt {path_idx} ({path.source_block} -> {path.target_block}, "
                        f"{path.item_id}): broken at ({x},{y}); expected belt continuation"
                    ),
                    location=(x, y),
                    payload={
                        "path_idx": path_idx,
                        "item_id": path.item_id,
                        "source_block": path.source_block,
                        "target_block": path.target_block,
                        "broken_at": (x, y),
                    },
                )
            )
            return
        if (nx, ny) in visited:
            # Hit a loop somewhere — flag as disconnected (paths shouldn't loop).
            report.issues.append(
                Issue(
                    code="DISCONNECTED_BELT",
                    severity=Severity.ERROR,
                    message=(f"belt {path_idx} ({path.item_id}): loops back to ({nx},{ny})"),
                    location=(nx, ny),
                    payload={"path_idx": path_idx, "item_id": path.item_id},
                )
            )
            return
        visited.add((nx, ny))
        x, y = nx, ny

    # Ran out of steps without hitting the target.
    report.issues.append(
        Issue(
            code="DISCONNECTED_BELT",
            severity=Severity.ERROR,
            message=f"belt {path_idx} ({path.item_id}): walk exceeded canvas budget",
            location=(x, y),
            payload={"path_idx": path_idx, "item_id": path.item_id},
        )
    )


def _find_underground_exit(
    grid: TileGrid,
    x: int,
    y: int,
    direction: int,
    name: str,
) -> tuple[int, int] | None:
    """Find the matching output tile for an underground input at `(x, y)`."""
    max_gap = _UG_MAX.get(name, 10)
    dx, dy = _STEP[direction]
    for k in range(2, max_gap + 1):
        tx, ty = x + dx * k, y + dy * k
        cell = grid.get(tx, ty)
        if (
            isinstance(cell, UndergroundCell)
            and cell.io_type == "output"
            and cell.direction == direction
            and cell.name == name
        ):
            return (tx, ty)
    return None


# ---------------------------------------------------------------------------
# Underground pair check (independent of path walks; catches orphans even
# in pathological cases where the path walker never reaches that tile).
# ---------------------------------------------------------------------------


def _check_underground_pairs(grid: TileGrid, report: VerifyReport) -> None:
    inputs = [
        (tile, cell)
        for tile, cell in grid.items()
        if isinstance(cell, UndergroundCell) and cell.io_type == "input"
    ]
    outputs = [
        (tile, cell)
        for tile, cell in grid.items()
        if isinstance(cell, UndergroundCell) and cell.io_type == "output"
    ]
    matched_outputs: set[tuple[int, int]] = set()
    for tile, cell in inputs:
        exit_tile = _find_underground_exit(grid, tile[0], tile[1], cell.direction, cell.name)
        if exit_tile is None:
            report.issues.append(
                Issue(
                    code="ORPHAN_UNDERGROUND",
                    severity=Severity.ERROR,
                    message=f"underground input at {tile} has no matching output",
                    location=tile,
                    payload={"io_type": "input", "name": cell.name},
                )
            )
        else:
            matched_outputs.add(exit_tile)
    for tile, cell in outputs:
        if tile not in matched_outputs:
            report.issues.append(
                Issue(
                    code="ORPHAN_UNDERGROUND",
                    severity=Severity.ERROR,
                    message=f"underground output at {tile} has no matching input",
                    location=tile,
                    payload={"io_type": "output", "name": cell.name},
                )
            )


# ---------------------------------------------------------------------------
# Adjacent-belt direction conflict
# ---------------------------------------------------------------------------


def _check_belt_direction_conflicts(grid: TileGrid, report: VerifyReport) -> None:
    """Head-to-head adjacent belts (a faces east, b sits east of a facing west)
    deadlock both tiles. Flag every such pair once.
    """
    seen: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    for tile, cell in grid.belts():
        dx, dy = _STEP[cell.direction]
        nx, ny = tile[0] + dx, tile[1] + dy
        nbr = grid.get(nx, ny)
        if isinstance(nbr, BeltCell) and nbr.direction == _OPPOSITE[cell.direction]:
            key = tuple(sorted([tile, (nx, ny)]))
            if key in seen:
                continue
            seen.add(key)
            report.issues.append(
                Issue(
                    code="BELT_DIRECTION_CONFLICT",
                    severity=Severity.ERROR,
                    message=f"belts at {tile} and {(nx, ny)} face each other head-to-head",
                    location=tile,
                    payload={"a": tile, "b": (nx, ny)},
                )
            )
        # Bonus check: an inserter shouldn't sit on a belt's outgoing
        # neighbour facing back at us (handled in inserters.py).
        _ = InserterCell


__all__ = ["check", "check_string_only"]
