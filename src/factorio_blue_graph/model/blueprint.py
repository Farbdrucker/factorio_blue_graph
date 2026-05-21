"""Blueprint entity dataclasses emitted by Phase 6 routing and consumed by
Phase 9 blueprint-string export.

Directions follow the vanilla 1.1 Factorio convention: 0=N, 2=E, 4=S, 6=W
(8-way enum, but belts only use the four cardinals).
"""

from __future__ import annotations

from dataclasses import dataclass

DIR_N = 0
DIR_E = 2
DIR_S = 4
DIR_W = 6

CARDINAL_DIRS = (DIR_N, DIR_E, DIR_S, DIR_W)


@dataclass(frozen=True)
class BeltSegment:
    """One surface transport-belt tile."""

    x: int
    y: int
    direction: int
    name: str


@dataclass(frozen=True)
class UndergroundBelt:
    """One half of an underground-belt pair.

    `io_type == "input"` is the entrance (belt goes down); `"output"` is
    the exit (belt comes back up). Both halves face `direction` (the flow
    direction). The two halves of a pair must share `name` and `direction`
    and be in line `direction` apart by at most the tier's max gap.
    """

    x: int
    y: int
    direction: int
    name: str
    io_type: str


@dataclass(frozen=True)
class Splitter:
    """A 2-tile splitter merging or splitting one belt lane.

    `(x, y)` is the splitter's anchor tile (Factorio splitters span an
    extra tile to the right of the facing direction). Phase 9 handles the
    second-tile bookkeeping during blueprint encoding.
    """

    x: int
    y: int
    direction: int
    name: str


__all__ = [
    "CARDINAL_DIRS",
    "DIR_E",
    "DIR_N",
    "DIR_S",
    "DIR_W",
    "BeltSegment",
    "Splitter",
    "UndergroundBelt",
]
