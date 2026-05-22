"""Inserter-orientation and orphan checks.

* `WRONG_INSERTER_DIRECTION`: an inserter's drop tile must hit a machine,
  chest, or generator footprint (a sink); its pickup tile must hit a
  belt, machine, or chest (a source). Either side empty is a structural
  bug.
* `ORPHAN_INSERTER`: an inserter whose both ends fail the above is more
  severe — the repair pass deletes it outright.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from factorio_blue_graph.model.blueprint import DIR_E, DIR_N, DIR_S, DIR_W
from factorio_blue_graph.verify.grid import (
    BeltCell,
    BoilerCell,
    ChestCell,
    MachineCell,
    SplitterCell,
    SteamCell,
    TileGrid,
    UndergroundCell,
)
from factorio_blue_graph.verify.report import Issue, Severity, VerifyReport

if TYPE_CHECKING:
    from factorio_blue_graph.layout.inserter import InserterResult

_STEP = {DIR_N: (0, -1), DIR_E: (1, 0), DIR_S: (0, 1), DIR_W: (-1, 0)}
_OPPOSITE = {DIR_N: DIR_S, DIR_S: DIR_N, DIR_E: DIR_W, DIR_W: DIR_E}
# Long-handed inserter reaches 2 tiles either side; short inserters reach 1.
_REACH = {
    "inserter": 1,
    "fast-inserter": 1,
    "stack-inserter": 1,
    "long-handed-inserter": 2,
}

# Entity types valid on the drop side (sink): machines, chests, generators.
_SINK_TYPES = (MachineCell, ChestCell, BoilerCell, SteamCell)
# Entity types valid on the pickup side (source).
_SOURCE_TYPES = (MachineCell, ChestCell, BeltCell, UndergroundCell, SplitterCell)


def check(grid: TileGrid, inserter_result: InserterResult, report: VerifyReport) -> None:
    _scan(grid, report)


def check_string_only(grid: TileGrid, report: VerifyReport) -> None:
    _scan(grid, report)


def _scan(grid: TileGrid, report: VerifyReport) -> None:
    for tile, cell in grid.inserters():
        ix, iy = tile
        drop = cell.direction
        pickup = _OPPOSITE[drop]
        reach = _REACH.get(cell.name, 1)
        drop_tile = (ix + _STEP[drop][0] * reach, iy + _STEP[drop][1] * reach)
        pickup_tile = (ix + _STEP[pickup][0] * reach, iy + _STEP[pickup][1] * reach)
        drop_ent = grid.get(*drop_tile)
        pickup_ent = grid.get(*pickup_tile)

        drop_ok = isinstance(drop_ent, _SINK_TYPES)
        pickup_ok = isinstance(pickup_ent, _SOURCE_TYPES)

        if not drop_ok and not pickup_ok:
            report.issues.append(
                Issue(
                    code="ORPHAN_INSERTER",
                    severity=Severity.ERROR,
                    message=(
                        f"inserter at {tile} has no entity on either end "
                        f"(drop {drop_tile}, pickup {pickup_tile})"
                    ),
                    location=tile,
                    payload={
                        "drop_tile": drop_tile,
                        "pickup_tile": pickup_tile,
                        "drop_dir": drop,
                    },
                )
            )
            continue
        if not drop_ok:
            report.issues.append(
                Issue(
                    code="WRONG_INSERTER_DIRECTION",
                    severity=Severity.ERROR,
                    message=(
                        f"inserter at {tile}: drop tile {drop_tile} has "
                        f"{_describe(drop_ent)}, expected machine/chest"
                    ),
                    location=tile,
                    payload={
                        "side": "drop",
                        "drop_tile": drop_tile,
                        "pickup_tile": pickup_tile,
                        "drop_dir": drop,
                    },
                )
            )
            continue
        if not pickup_ok:
            report.issues.append(
                Issue(
                    code="WRONG_INSERTER_DIRECTION",
                    severity=Severity.ERROR,
                    message=(
                        f"inserter at {tile}: pickup tile {pickup_tile} has "
                        f"{_describe(pickup_ent)}, expected belt/machine/chest"
                    ),
                    location=tile,
                    payload={
                        "side": "pickup",
                        "drop_tile": drop_tile,
                        "pickup_tile": pickup_tile,
                        "drop_dir": drop,
                    },
                )
            )


def _describe(ent) -> str:
    if ent is None:
        return "empty"
    return ent.__class__.__name__.lower().replace("cell", "")


__all__ = ["check", "check_string_only"]
