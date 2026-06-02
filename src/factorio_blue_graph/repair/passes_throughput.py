"""Throughput-repair passes.

Each pass takes the list of ``Bottleneck`` objects for one code and the
mutable ``PlanState``; returns the number of effective fixes applied. A
pass that can't help with a given state returns 0 so the loop's
oscillation guard can move on.
"""

from __future__ import annotations

from factorio_blue_graph.layout.placement import PlacementError, place_blocks
from factorio_blue_graph.layout.routing import route_belts
from factorio_blue_graph.model.blueprint import Inserter
from factorio_blue_graph.repair.state import PlanState
from factorio_blue_graph.simulate.report import Bottleneck

_INSERTER_LADDER = {
    "inserter": "fast-inserter",
    "long-handed-inserter": "fast-inserter",
    "fast-inserter": "stack-inserter",
}

# Per-state counter tracking how many times ``respace_and_replace`` has
# fired for a given ``PlanState`` so each retry widens the gap a little
# more. Capped at ``_MAX_RESPACE`` to keep the heal budget bounded.
_RESPACE_COUNTER: dict[int, int] = {}
_MAX_RESPACE = 3


def enable_belt_routing(_issues: list[Bottleneck], state: PlanState) -> int:
    """Promote currently-skipped Phase 6 belt routing.

    Only commits the new routing when ``unresolved == ()``; partial routings
    leave the IR in a confusing half-state where some inter-block flows are
    belts and others remain chest-fed.
    """
    if state.routing.paths:
        return 0  # already routed; let other passes handle saturation
    new_routing = route_belts(state.placement, state.block_graph)
    if new_routing.unresolved:
        return 0
    if not new_routing.paths:
        return 0
    state.routing = new_routing
    return len(new_routing.paths)


def upgrade_inserter(issues: list[Bottleneck], state: PlanState) -> int:
    """Walk inserter ladder at flagged locations."""
    locations = {b.location for b in issues if b.location is not None}
    if not locations:
        return 0
    new_inserters = []
    changed = 0
    for ins in state.inserters.inserters:
        if (ins.x, ins.y) in locations and ins.name in _INSERTER_LADDER:
            new_inserters.append(
                Inserter(
                    x=ins.x,
                    y=ins.y,
                    direction=ins.direction,
                    name=_INSERTER_LADDER[ins.name],
                )
            )
            changed += 1
        else:
            new_inserters.append(ins)
    if changed:
        state.replace_inserters(tuple(new_inserters))
    return changed


def bump_belt_tier(_issues: list[Bottleneck], state: PlanState) -> int:
    """Stub: tier-bumping requires per-edge re-routing.

    The structural router already picks the smallest tier ≥ flow; saturation
    bottlenecks in v1 indicate a deeper layout issue (e.g. cross-flow on a
    shared corridor) that the heavier ``respace_and_replace`` pass handles.
    Return 0 so the loop falls through.
    """
    return 0


def respace_and_replace(_issues: list[Bottleneck], state: PlanState) -> int:
    """Re-run Phase 5+6 with a wider ``min_gap`` between blocks.

    When ``OUTPUT_UNDERDELIVERY`` persists after ``enable_belt_routing``,
    the most common cause is that two blocks are packed so close that
    the A* router cannot find an unblocked corridor for one of their
    inter-block belts (``RoutingResult.unresolved`` is non-empty). Adding
    a 1-tile gap between blocks usually opens a corridor.

    The pass is bounded per-state: ``min_gap`` starts at 1 and increases
    by 1 each call, capped at ``_MAX_RESPACE``. Returns 0 (so the
    throughput loop moves on) when:

    * the cap is hit,
    * re-placement fails (canvas too small for the wider gap), or
    * re-routing still leaves edges unresolved.
    """
    key = id(state)
    used = _RESPACE_COUNTER.get(key, 0)
    if used >= _MAX_RESPACE:
        return 0
    new_gap = used + 1
    _RESPACE_COUNTER[key] = new_gap

    try:
        new_placement = place_blocks(
            state.block_graph,
            state.placement.canvas,
            min_gap=new_gap,
        )
    except PlacementError:
        return 0

    new_routing = route_belts(new_placement, state.block_graph)
    if new_routing.unresolved:
        return 0

    state.placement = new_placement
    state.routing = new_routing
    return 1


__all__ = [
    "bump_belt_tier",
    "enable_belt_routing",
    "respace_and_replace",
    "upgrade_inserter",
]
