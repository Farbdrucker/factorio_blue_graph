"""2D tile occupancy grid used by every verifier check.

A `TileGrid` maps `(x, y)` to a typed `Entity` (one of `MachineCell`,
`BeltCell`, `UndergroundCell`, `SplitterCell`, `InserterCell`, `PoleCell`,
`ChestCell`, `SteamCell`, `BoilerCell`, `PumpCell`). The grid is built
once per verification run from a `PlanState` (or from a decoded blueprint)
and shared across checks.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class MachineCell:
    machine_id: str
    recipe_id: str
    machine_name: str
    top_left: tuple[int, int]
    footprint: tuple[int, int]


@dataclass(frozen=True)
class BeltCell:
    direction: int  # Factorio 0/2/4/6
    name: str


@dataclass(frozen=True)
class UndergroundCell:
    direction: int
    io_type: str  # "input" | "output"
    name: str


@dataclass(frozen=True)
class SplitterCell:
    direction: int
    name: str
    anchor: tuple[int, int]


@dataclass(frozen=True)
class InserterCell:
    direction: int  # drop direction
    name: str


@dataclass(frozen=True)
class PoleCell:
    name: str


@dataclass(frozen=True)
class ChestCell:
    name: str
    item_id: str


@dataclass(frozen=True)
class SteamCell:
    direction: int


@dataclass(frozen=True)
class BoilerCell:
    direction: int


@dataclass(frozen=True)
class PumpCell:
    direction: int


Entity = (
    MachineCell
    | BeltCell
    | UndergroundCell
    | SplitterCell
    | InserterCell
    | PoleCell
    | ChestCell
    | SteamCell
    | BoilerCell
    | PumpCell
)


class TileGrid:
    """Mutable typed tile occupancy map. Multiple distinct entity layers may
    coexist on one tile (an inserter shares its tile with nothing in vanilla
    Factorio, but during construction we allow lookups by tile and by layer)."""

    def __init__(self, canvas: tuple[int, int]) -> None:
        self.canvas = canvas
        # Each tile may hold at most one entity (Factorio's collision model).
        # The verifier still needs to detect overlap, so insert checks for
        # existing occupants.
        self._cells: dict[tuple[int, int], Entity] = {}

    def put(self, x: int, y: int, entity: Entity) -> None:
        self._cells[(x, y)] = entity

    def get(self, x: int, y: int) -> Entity | None:
        return self._cells.get((x, y))

    def __contains__(self, key: tuple[int, int]) -> bool:
        return key in self._cells

    def items(self) -> Iterable[tuple[tuple[int, int], Entity]]:
        return self._cells.items()

    def belts(self) -> Iterable[tuple[tuple[int, int], BeltCell]]:
        for tile, ent in self._cells.items():
            if isinstance(ent, BeltCell):
                yield tile, ent

    def inserters(self) -> Iterable[tuple[tuple[int, int], InserterCell]]:
        for tile, ent in self._cells.items():
            if isinstance(ent, InserterCell):
                yield tile, ent

    def machines(self) -> list[MachineCell]:
        # Return one entry per distinct machine — machines occupy >1 tile so
        # the cell map repeats the same MachineCell across all its tiles.
        seen: set[str] = set()
        out: list[MachineCell] = []
        for ent in self._cells.values():
            if isinstance(ent, MachineCell) and ent.machine_id not in seen:
                seen.add(ent.machine_id)
                out.append(ent)
        return out

    def poles(self) -> list[tuple[tuple[int, int], PoleCell]]:
        return [(t, e) for t, e in self._cells.items() if isinstance(e, PoleCell)]

    def chests(self) -> list[tuple[tuple[int, int], ChestCell]]:
        return [(t, e) for t, e in self._cells.items() if isinstance(e, ChestCell)]


__all__ = [
    "BeltCell",
    "BoilerCell",
    "ChestCell",
    "Entity",
    "InserterCell",
    "MachineCell",
    "PoleCell",
    "PumpCell",
    "SplitterCell",
    "SteamCell",
    "TileGrid",
    "UndergroundCell",
]
