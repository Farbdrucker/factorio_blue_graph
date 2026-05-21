"""Tests for the Phase 3 belt-tier picker."""

from __future__ import annotations

import pytest

from factorio_blue_graph.layout.tier import (
    TIER_CAPACITY,
    BeltAssignment,
    BeltTier,
    pick_tier,
)


def test_yellow_below_capacity() -> None:
    a = pick_tier(10.0)
    assert a == BeltAssignment(tier=BeltTier.YELLOW, lane_count=1, flow=10.0)


def test_yellow_at_capacity_boundary_inclusive() -> None:
    a = pick_tier(15.0)
    assert a.tier is BeltTier.YELLOW
    assert a.lane_count == 1


def test_just_above_yellow_promotes_to_red() -> None:
    a = pick_tier(15.0001)
    assert a.tier is BeltTier.RED
    assert a.lane_count == 1


def test_red_at_capacity_boundary_inclusive() -> None:
    a = pick_tier(30.0)
    assert a.tier is BeltTier.RED
    assert a.lane_count == 1


def test_blue_at_capacity_boundary_inclusive() -> None:
    a = pick_tier(45.0)
    assert a.tier is BeltTier.BLUE
    assert a.lane_count == 1


def test_split_just_above_blue() -> None:
    a = pick_tier(46.0)
    assert a.tier is BeltTier.BLUE
    assert a.lane_count == 2


def test_split_two_full_lanes() -> None:
    a = pick_tier(90.0)
    assert a.tier is BeltTier.BLUE
    assert a.lane_count == 2


def test_split_three_lanes() -> None:
    a = pick_tier(135.0)
    assert a.tier is BeltTier.BLUE
    assert a.lane_count == 3


def test_zero_flow_is_degenerate_single_yellow() -> None:
    a = pick_tier(0.0)
    assert a.tier is BeltTier.YELLOW
    assert a.lane_count == 1


def test_negative_raises() -> None:
    with pytest.raises(ValueError):
        pick_tier(-1.0)


def test_tier_capacity_table_values() -> None:
    assert TIER_CAPACITY[BeltTier.YELLOW] == 15.0
    assert TIER_CAPACITY[BeltTier.RED] == 30.0
    assert TIER_CAPACITY[BeltTier.BLUE] == 45.0
