"""Blueprint entity dataclasses emitted by Phases 6-7 and consumed by
Phase 9 blueprint-string export.

Directions follow the vanilla 1.1 Factorio convention: 0=N, 2=E, 4=S, 6=W
(8-way enum, but belts and inserters only use the four cardinals).
"""

from __future__ import annotations

from dataclasses import dataclass

DIR_N = 0
DIR_E = 2
DIR_S = 4
DIR_W = 6

CARDINAL_DIRS = (DIR_N, DIR_E, DIR_S, DIR_W)

# Power-pole supply area half-extent. A medium electric pole covers a 7×7
# square centred on its tile, so any other tile (mx, my) is powered iff
# |mx - px| <= POLE_RADIUS and |my - py| <= POLE_RADIUS.
POLE_RADIUS = 3

# Vanilla 1.1 inserter throughput (items/sec). Used by Phase 7 to size the
# number of inserters needed at a belt endpoint. Stack inserter assumes a
# stack size bonus of 12 (the late-game default once stack inserter capacity
# upgrade 1 is researched); the v1 planner is conservative and uses the
# unboosted base rate of 2.31 items/sec until a stack-size knob is added.
INSERTER_THROUGHPUT: dict[str, float] = {
    "inserter": 0.83,
    "long-handed-inserter": 1.2,
    "fast-inserter": 2.31,
    "stack-inserter": 2.31,
}


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


@dataclass(frozen=True)
class Inserter:
    """A 1×1 inserter moving items between a belt tile and a machine tile.

    `direction` is the **drop** direction (the convention Factorio uses
    for inserter entities). A source-side inserter drops onto the belt
    (direction = outward from the machine); a sink-side inserter drops
    onto the machine (direction = inward toward the machine).
    """

    x: int
    y: int
    direction: int
    name: str


@dataclass(frozen=True)
class Pole:
    """A 1×1 electric pole supplying every tile in a 7×7 square around it."""

    x: int
    y: int
    name: str


__all__ = [
    "CARDINAL_DIRS",
    "DIR_E",
    "DIR_N",
    "DIR_S",
    "DIR_W",
    "INSERTER_THROUGHPUT",
    "POLE_RADIUS",
    "BeltSegment",
    "Inserter",
    "Pole",
    "Splitter",
    "UndergroundBelt",
]
