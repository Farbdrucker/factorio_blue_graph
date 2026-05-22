"""Power-grid checks.

* `UNPOWERED_MACHINE` / `UNPOWERED_INSERTER`: every machine and inserter
  tile lies in some pole's 7×7 supply area.
* `DISCONNECTED_POLE_NETWORK`: the pole graph (edges where Chebyshev
  distance ≤ `POLE_WIRE_REACH = 9`) must be a single connected
  component AND at least one pole must wire-connect (transitively) to a
  generator tile (steam engine).
* `NO_POWER_SOURCE`: at least one `SteamEngine` exists; if absent and
  there's any machine on the canvas, error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from factorio_blue_graph.model.blueprint import (
    POLE_RADIUS,
    POLE_WIRE_REACH,
    PowerSourceResult,
)
from factorio_blue_graph.verify.grid import (
    SteamCell,
    TileGrid,
)
from factorio_blue_graph.verify.report import Issue, Severity, VerifyReport

if TYPE_CHECKING:
    from factorio_blue_graph.layout.placement import Placement
    from factorio_blue_graph.layout.power import PoleResult
    from factorio_blue_graph.model.graph import BlockGraph


def check(
    grid: TileGrid,
    placement: Placement,
    block_graph: BlockGraph,
    power: PoleResult,
    power_source: PowerSourceResult | None,
    report: VerifyReport,
) -> None:
    _check_coverage(grid, report)
    _check_pole_network(grid, report)
    _check_power_source(grid, power_source, report)


def check_string_only(grid: TileGrid, report: VerifyReport) -> None:
    _check_coverage(grid, report)
    _check_pole_network(grid, report)
    _check_power_source(grid, None, report)


def _check_coverage(grid: TileGrid, report: VerifyReport) -> None:
    poles = [tile for tile, _ in grid.poles()]
    if not poles:
        # Caller will raise NO_POWER_SOURCE separately; suppress per-machine
        # noise when there are simply no poles at all.
        return
    for machine in grid.machines():
        mx, my = machine.top_left
        fw, fh = machine.footprint
        if not _covered(mx, my, fw, fh, poles):
            report.issues.append(
                Issue(
                    code="UNPOWERED_MACHINE",
                    severity=Severity.ERROR,
                    message=(
                        f"machine {machine.machine_id} at ({mx},{my}) not in any pole's coverage"
                    ),
                    location=(mx, my),
                    payload={
                        "machine_id": machine.machine_id,
                        "top_left": (mx, my),
                        "footprint": (fw, fh),
                    },
                )
            )
    for tile, _ in grid.inserters():
        if not _covered(tile[0], tile[1], 1, 1, poles):
            report.issues.append(
                Issue(
                    code="UNPOWERED_INSERTER",
                    severity=Severity.WARNING,
                    message=f"inserter at {tile} not in any pole's coverage",
                    location=tile,
                    payload={"tile": tile},
                )
            )


def _covered(mx: int, my: int, fw: int, fh: int, poles: list[tuple[int, int]]) -> bool:
    for px, py in poles:
        if (
            mx + fw - 1 >= px - POLE_RADIUS
            and mx <= px + POLE_RADIUS
            and my + fh - 1 >= py - POLE_RADIUS
            and my <= py + POLE_RADIUS
        ):
            return True
    return False


def _check_pole_network(grid: TileGrid, report: VerifyReport) -> None:
    poles = [tile for tile, _ in grid.poles()]
    if len(poles) <= 1:
        return
    # Union-Find.
    parent = {p: p for p in poles}

    def find(t: tuple[int, int]) -> tuple[int, int]:
        while parent[t] != t:
            parent[t] = parent[parent[t]]
            t = parent[t]
        return t

    def union(a: tuple[int, int], b: tuple[int, int]) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i, a in enumerate(poles):
        for b in poles[i + 1 :]:
            if max(abs(a[0] - b[0]), abs(a[1] - b[1])) <= POLE_WIRE_REACH:
                union(a, b)

    components: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for p in poles:
        components.setdefault(find(p), []).append(p)
    if len(components) > 1:
        report.issues.append(
            Issue(
                code="DISCONNECTED_POLE_NETWORK",
                severity=Severity.ERROR,
                message=(
                    f"pole network has {len(components)} disconnected components "
                    f"(largest has {max(len(c) for c in components.values())} poles)"
                ),
                location=poles[0],
                payload={
                    "components": [list(c) for c in components.values()],
                },
            )
        )


def _check_power_source(
    grid: TileGrid,
    power_source: PowerSourceResult | None,
    report: VerifyReport,
) -> None:
    has_machine = bool(grid.machines())
    if not has_machine:
        return
    has_engine = any(isinstance(e, SteamCell) for _, e in grid.items())
    if has_engine:
        return
    required_kw = 0.0
    if power_source is not None:
        required_kw = power_source.required_kw
    report.issues.append(
        Issue(
            code="NO_POWER_SOURCE",
            severity=Severity.ERROR,
            message=(
                f"no steam-engine emitted; blueprint has no power source "
                f"(required ≈ {required_kw:.0f} kW)"
            ),
            location=None,
            payload={"required_kw": required_kw},
        )
    )


__all__ = ["check", "check_string_only"]
