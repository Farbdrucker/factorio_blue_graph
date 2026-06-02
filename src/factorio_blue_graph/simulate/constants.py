"""Tick-simulator constants.

Vanilla 1.1 timing numbers used to drive the discrete-tick throughput
simulator. Cross-checked against ``model/blueprint.py::INSERTER_THROUGHPUT``
which is the items/sec rate exposed to the planner; the swing-tick numbers
here are the closest integer cycle that reproduces those rates at 60 UPS.
"""

from __future__ import annotations

from factorio_blue_graph.layout.tier import TIER_CAPACITY, BeltTier

TICKS_PER_SECOND = 60

# Ticks per full pickup→drop→return cycle. ``items / sec ≈ items_per_swing *
# 60 / SWING_TICKS``. The values below match the planner-side
# ``INSERTER_THROUGHPUT`` constants closely enough for the sim's purpose.
SWING_TICKS: dict[str, int] = {
    "inserter": 72,  # 0.83 items/sec
    "long-handed-inserter": 50,  # 1.2 items/sec
    "fast-inserter": 26,  # 2.31 items/sec
    "stack-inserter": 26,  # 2.31 items/sec (no stack bonus modelled)
}

ITEMS_PER_SWING: dict[str, int] = {
    "inserter": 1,
    "long-handed-inserter": 1,
    "fast-inserter": 1,
    "stack-inserter": 1,
}

# Capacity in items per tick per lane, derived from TIER_CAPACITY.
BELT_CAP_PER_TICK: dict[BeltTier, float] = {
    tier: TIER_CAPACITY[tier] / TICKS_PER_SECOND for tier in TIER_CAPACITY
}

# Default measurement window. 30s warmup is enough to fill long pipelines;
# 60s measurement window makes items/min readout direct.
WARMUP_TICKS = 1800
MEASURE_TICKS = 3600

# Acceptance threshold for ``SimReport.ok``.
ACHIEVEMENT_THRESHOLD = 0.95

# Machine input/output slot caps (items). Generous so transient stalls don't
# dominate the steady-state measurement; lower than infinity so saturated
# downstream paths back-pressure as in real Factorio.
MACHINE_INPUT_CAP = 100
MACHINE_OUTPUT_CAP = 100

# Chest cap. Output sink chests treat this as unlimited (planner takes them
# off the belt fast enough that the bucket never fills in steady state).
CHEST_CAP = 1_000_000


__all__ = [
    "ACHIEVEMENT_THRESHOLD",
    "BELT_CAP_PER_TICK",
    "CHEST_CAP",
    "ITEMS_PER_SWING",
    "MACHINE_INPUT_CAP",
    "MACHINE_OUTPUT_CAP",
    "MEASURE_TICKS",
    "SWING_TICKS",
    "TICKS_PER_SECOND",
    "WARMUP_TICKS",
]
