"""Inserter orientation and orphan checks."""

from __future__ import annotations

from factorio_blue_graph.model.blueprint import DIR_E, DIR_N
from factorio_blue_graph.verify import inserters
from factorio_blue_graph.verify.grid import (
    BeltCell,
    ChestCell,
    InserterCell,
    MachineCell,
    TileGrid,
)
from factorio_blue_graph.verify.report import VerifyReport


def _machine(grid: TileGrid, x: int, y: int, fw: int = 3, fh: int = 3) -> None:
    cell = MachineCell(
        machine_id=f"m_{x}_{y}",
        recipe_id="r",
        machine_name="assembling-machine-1",
        top_left=(x, y),
        footprint=(fw, fh),
    )
    for dx in range(fw):
        for dy in range(fh):
            grid.put(x + dx, y + dy, cell)


def test_orphan_inserter_detected():
    grid = TileGrid((20, 20))
    grid.put(5, 5, InserterCell(direction=DIR_N, name="inserter"))
    report = VerifyReport()
    inserters.check_string_only(grid, report)
    codes = [i.code for i in report.issues]
    assert "ORPHAN_INSERTER" in codes


def test_inserter_with_belt_pickup_and_machine_drop_ok():
    grid = TileGrid((20, 20))
    _machine(grid, 5, 4, 3, 3)  # machine north of inserter
    grid.put(6, 7, BeltCell(direction=DIR_E, name="transport-belt"))  # pickup south
    grid.put(6, 8, BeltCell(direction=DIR_E, name="transport-belt"))  # add belt
    # Inserter at (6, 7) was a belt; put inserter at (6, 8)? Better build clean.
    grid = TileGrid((20, 20))
    _machine(grid, 5, 4, 3, 3)
    grid.put(6, 8, BeltCell(direction=DIR_E, name="transport-belt"))
    grid.put(6, 7, InserterCell(direction=DIR_N, name="inserter"))
    report = VerifyReport()
    inserters.check_string_only(grid, report)
    assert not [
        i for i in report.issues if i.code in ("ORPHAN_INSERTER", "WRONG_INSERTER_DIRECTION")
    ]


def test_inserter_missing_pickup_side():
    grid = TileGrid((20, 20))
    _machine(grid, 5, 4, 3, 3)
    # No belt south — pickup side empty.
    grid.put(6, 7, InserterCell(direction=DIR_N, name="inserter"))
    report = VerifyReport()
    inserters.check_string_only(grid, report)
    codes = [i.code for i in report.issues]
    # Either WRONG_INSERTER_DIRECTION (drop ok, pickup empty) or ORPHAN.
    assert "WRONG_INSERTER_DIRECTION" in codes


def test_chest_qualifies_as_drop_target():
    grid = TileGrid((20, 20))
    grid.put(7, 5, ChestCell(name="wooden-chest", item_id="iron-ore"))  # drop tile
    grid.put(5, 5, BeltCell(direction=DIR_E, name="transport-belt"))  # pickup tile
    grid.put(6, 5, InserterCell(direction=DIR_E, name="inserter"))
    report = VerifyReport()
    inserters.check_string_only(grid, report)
    # No errors: pickup belt, drop chest.
    assert not [i for i in report.issues if i.severity.name == "ERROR"]


def test_long_handed_inserter_uses_reach_2():
    grid = TileGrid((20, 20))
    _machine(grid, 5, 4, 3, 3)
    grid.put(6, 9, BeltCell(direction=DIR_E, name="transport-belt"))  # 2 tiles south of insert.
    grid.put(6, 7, InserterCell(direction=DIR_N, name="long-handed-inserter"))
    report = VerifyReport()
    inserters.check_string_only(grid, report)
    # Drop tile = (6, 5) → machine ✓; pickup tile = (6, 9) → belt ✓.
    assert not [i for i in report.issues if i.severity.name == "ERROR"]
