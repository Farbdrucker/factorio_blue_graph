"""Belt continuity and underground-pair checks."""

from __future__ import annotations

from factorio_blue_graph.model.blueprint import DIR_E
from factorio_blue_graph.verify import belts
from factorio_blue_graph.verify.grid import BeltCell, TileGrid, UndergroundCell
from factorio_blue_graph.verify.report import VerifyReport


def _put_belt(grid: TileGrid, x: int, y: int, direction: int = DIR_E) -> None:
    grid.put(x, y, BeltCell(direction=direction, name="transport-belt"))


def test_orphan_underground_input_detected():
    grid = TileGrid((20, 5))
    grid.put(
        2,
        2,
        UndergroundCell(direction=DIR_E, io_type="input", name="underground-belt"),
    )
    report = VerifyReport()
    belts.check_string_only(grid, report)
    codes = [i.code for i in report.issues]
    assert "ORPHAN_UNDERGROUND" in codes


def test_underground_input_matched_with_output_is_ok():
    grid = TileGrid((20, 5))
    grid.put(
        2,
        2,
        UndergroundCell(direction=DIR_E, io_type="input", name="underground-belt"),
    )
    grid.put(
        5,
        2,
        UndergroundCell(direction=DIR_E, io_type="output", name="underground-belt"),
    )
    report = VerifyReport()
    belts.check_string_only(grid, report)
    assert not [i for i in report.issues if i.code == "ORPHAN_UNDERGROUND"]


def test_head_to_head_belts_detected():
    grid = TileGrid((10, 5))
    _put_belt(grid, 2, 2, DIR_E)
    _put_belt(grid, 3, 2, 6)  # DIR_W
    report = VerifyReport()
    belts.check_string_only(grid, report)
    codes = [i.code for i in report.issues]
    assert "BELT_DIRECTION_CONFLICT" in codes


def test_aligned_belts_are_not_conflicts():
    grid = TileGrid((10, 5))
    _put_belt(grid, 2, 2, DIR_E)
    _put_belt(grid, 3, 2, DIR_E)
    _put_belt(grid, 4, 2, DIR_E)
    report = VerifyReport()
    belts.check_string_only(grid, report)
    assert not [i for i in report.issues if i.code == "BELT_DIRECTION_CONFLICT"]
