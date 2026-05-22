"""Repair loop driving the planner IR toward a passing `VerifyReport`.

`plan_with_repair(state)` runs the verifier, applies the cheapest
applicable repair pass for whichever issue codes are present, and loops
up to `budget` iterations. The oscillation guard catches passes that
ping-pong without progress and exits with a WARNING.
"""

from factorio_blue_graph.repair.loop import plan_with_repair
from factorio_blue_graph.repair.state import PlanState

__all__ = ["PlanState", "plan_with_repair"]
