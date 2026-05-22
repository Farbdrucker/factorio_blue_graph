"""Power-grid checks: coverage, network connectivity, source presence."""

from __future__ import annotations

from factorio_blue_graph.verify import power
from factorio_blue_graph.verify.grid import (
    MachineCell,
    PoleCell,
    SteamCell,
    TileGrid,
)
from factorio_blue_graph.verify.report import VerifyReport


def _machine(grid: TileGrid, x: int, y: int) -> None:
    cell = MachineCell(
        machine_id=f"m_{x}_{y}",
        recipe_id="r",
        machine_name="assembling-machine-1",
        top_left=(x, y),
        footprint=(3, 3),
    )
    for dx in range(3):
        for dy in range(3):
            grid.put(x + dx, y + dy, cell)


def test_no_power_source_when_machines_exist():
    grid = TileGrid((20, 20))
    _machine(grid, 5, 5)
    grid.put(7, 7, PoleCell(name="medium-electric-pole"))
    report = VerifyReport()
    power.check_string_only(grid, report)
    codes = [i.code for i in report.issues]
    assert "NO_POWER_SOURCE" in codes


def test_steam_engine_suppresses_no_power_source():
    grid = TileGrid((30, 30))
    _machine(grid, 5, 5)
    grid.put(7, 7, PoleCell(name="medium-electric-pole"))
    for dx in range(5):
        for dy in range(3):
            grid.put(15 + dx, 15 + dy, SteamCell(direction=2))
    report = VerifyReport()
    power.check_string_only(grid, report)
    codes = [i.code for i in report.issues]
    assert "NO_POWER_SOURCE" not in codes


def test_disconnected_pole_network_detected():
    grid = TileGrid((50, 50))
    _machine(grid, 5, 5)
    # Two pole clusters > POLE_WIRE_REACH = 9 apart.
    grid.put(7, 7, PoleCell(name="medium-electric-pole"))
    grid.put(8, 7, PoleCell(name="medium-electric-pole"))
    grid.put(30, 30, PoleCell(name="medium-electric-pole"))
    for dx in range(5):
        for dy in range(3):
            grid.put(40 + dx, 40 + dy, SteamCell(direction=2))
    report = VerifyReport()
    power.check_string_only(grid, report)
    codes = [i.code for i in report.issues]
    assert "DISCONNECTED_POLE_NETWORK" in codes


def test_unpowered_machine_when_no_pole_nearby():
    grid = TileGrid((40, 40))
    _machine(grid, 5, 5)  # at (5..7, 5..7)
    grid.put(30, 30, PoleCell(name="medium-electric-pole"))  # too far
    for dx in range(5):
        for dy in range(3):
            grid.put(20 + dx, 20 + dy, SteamCell(direction=2))
    report = VerifyReport()
    power.check_string_only(grid, report)
    codes = [i.code for i in report.issues]
    assert "UNPOWERED_MACHINE" in codes


def test_covered_machine_not_flagged():
    grid = TileGrid((40, 40))
    _machine(grid, 5, 5)  # at (5..7, 5..7); pole at (6,6) covers via 7×7
    grid.put(6, 6, PoleCell(name="medium-electric-pole"))
    for dx in range(5):
        for dy in range(3):
            grid.put(20 + dx, 20 + dy, SteamCell(direction=2))
    report = VerifyReport()
    power.check_string_only(grid, report)
    codes = [i.code for i in report.issues]
    assert "UNPOWERED_MACHINE" not in codes
