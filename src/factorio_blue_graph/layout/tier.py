"""Phase 3 belt-tier selection.

Picks the smallest vanilla 1.1 belt tier whose single-lane capacity covers
the given item flow. Flows above the blue-belt cap (45 items/sec) get
multiple parallel blue lanes — Factorio has no faster vanilla belt.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from factorio_blue_graph.model.graph import FlowGraph, ItemChannel


class BeltTier(StrEnum):
    YELLOW = "transport-belt"
    RED = "fast-transport-belt"
    BLUE = "express-transport-belt"


# items/sec per single lane, vanilla 1.1
TIER_CAPACITY: dict[BeltTier, float] = {
    BeltTier.YELLOW: 15.0,
    BeltTier.RED: 30.0,
    BeltTier.BLUE: 45.0,
}

_TIER_ORDER = (BeltTier.YELLOW, BeltTier.RED, BeltTier.BLUE)


@dataclass(frozen=True)
class BeltAssignment:
    tier: BeltTier
    lane_count: int
    flow: float


def pick_tier(items_per_sec: float) -> BeltAssignment:
    if items_per_sec < 0:
        raise ValueError(f"flow must be non-negative, got {items_per_sec}")

    for tier in _TIER_ORDER:
        if items_per_sec <= TIER_CAPACITY[tier]:
            return BeltAssignment(tier=tier, lane_count=1, flow=items_per_sec)

    blue_cap = TIER_CAPACITY[BeltTier.BLUE]
    lanes = math.ceil(items_per_sec / blue_cap)
    return BeltAssignment(tier=BeltTier.BLUE, lane_count=lanes, flow=items_per_sec)


def assign_tiers(flow_graph: FlowGraph) -> dict[ItemChannel, BeltAssignment]:
    return {channel: pick_tier(channel.rate) for channel in flow_graph.channels}


__all__ = [
    "BeltAssignment",
    "BeltTier",
    "TIER_CAPACITY",
    "assign_tiers",
    "pick_tier",
]
