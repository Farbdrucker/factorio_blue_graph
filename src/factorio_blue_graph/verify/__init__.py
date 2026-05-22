"""Structural verifier for planner output.

Runs after Phase 7 (and standalone via the `fbg verify` CLI command) to
catch blueprints that decode cleanly but are functionally broken — belts
not reaching their endpoints, machines without inserters, unpowered
buildings, missing power source, raw inputs with no supply chest.

Each check emits one or more `Issue`s into a `VerifyReport`. The repair
loop in `factorio_blue_graph.repair` consumes the report and mutates the
IR in place; on success the report is empty and the planner proceeds to
export.
"""

from factorio_blue_graph.verify.report import Issue, Severity, VerifyReport
from factorio_blue_graph.verify.runner import (
    verify_blueprint_string,
    verify_plan,
)

__all__ = [
    "Issue",
    "Severity",
    "VerifyReport",
    "verify_blueprint_string",
    "verify_plan",
]
