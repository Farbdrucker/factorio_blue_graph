"""Phase 8: ε-constraint sweep across the (throughput, footprint) front.

Fix the throughput at decreasing fractions of the user target, run the
Phases 1-7 pipeline at each ε, and record the resulting bounding-box
area. The result is a list of ``ParetoPoint``s ordered by ascending
throughput (so the front reads left-to-right as bigger throughput costs
more tiles). Phase 5 already minimises a bbox-perimeter + flow-distance
objective, so no extra optimisation knob is added here — the sweep just
re-uses the existing per-phase entry points.

Pipeline failures (placement infeasible on the canvas, MILP infeasible)
are caught narrowly and the offending point is skipped; partially-routed
results are kept with ``status = "PARTIAL"`` since footprint is still
meaningful there.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from factorio_blue_graph.layout.clustering import cluster_into_blocks
from factorio_blue_graph.layout.inserter import InserterResult, place_inserters
from factorio_blue_graph.layout.placement import Placement, PlacementError, place_blocks
from factorio_blue_graph.layout.power import PoleResult, cover_with_poles
from factorio_blue_graph.layout.routing import RoutingError, RoutingResult, route_belts
from factorio_blue_graph.model.graph import BlockGraph, FlowGraph, RecipeHypergraph
from factorio_blue_graph.planning.demand import DemandError, DemandPlan, expand_demand
from factorio_blue_graph.planning.lp import MachinePlan, MachinePlanError, solve_machine_counts


@dataclass(frozen=True)
class ParetoPoint:
    index: int
    target_rate_per_sec: float
    footprint: int
    bbox: tuple[int, int]
    demand: DemandPlan
    machine_plan: MachinePlan
    flow_graph: FlowGraph
    block_graph: BlockGraph
    placement: Placement
    routing: RoutingResult
    inserters: InserterResult
    power: PoleResult
    status: str  # "OK" | "PARTIAL"


def default_epsilons(k_points: int) -> list[float]:
    """Evenly-spaced descending fractions in (0, 1] with `k_points` entries.

    With ``k_points = 5`` this yields ``[1.0, 0.8, 0.6, 0.4, 0.2]``.
    The lowest fraction is ``1/k_points`` so we never sweep a zero rate.
    """
    if k_points <= 0:
        raise ValueError(f"k_points must be positive, got {k_points}")
    return [1.0 - i / k_points for i in range(k_points)]


def pareto_sweep(
    target_item: str,
    target_rate_per_sec: float,
    *,
    graph: RecipeHypergraph | None = None,
    canvas: tuple[int, int] = (60, 60),
    k_points: int = 5,
    epsilons: Sequence[float] | None = None,
    placement_time_limit_s: float = 10.0,
    random_state: int = 42,
) -> list[ParetoPoint]:
    """Sweep `target_rate_per_sec` by the ε-constraint method.

    Returns surviving points sorted by ascending throughput. Points that
    fail mid-pipeline (placement infeasible on the canvas, MILP/demand
    infeasible) are dropped; partially-routed points are kept with
    ``status = "PARTIAL"``.
    """
    if target_rate_per_sec <= 0:
        raise ValueError(f"target_rate_per_sec must be positive, got {target_rate_per_sec}")

    if graph is None:
        graph = RecipeHypergraph.load_default()

    eps_schedule = list(epsilons) if epsilons is not None else default_epsilons(k_points)
    # Descending so the heaviest run happens first — quicker failure if
    # the canvas is too small to ever fit the target rate.
    eps_schedule = sorted({e for e in eps_schedule if e > 0}, reverse=True)

    points: list[ParetoPoint] = []
    for idx, eps in enumerate(eps_schedule):
        rate = eps * target_rate_per_sec
        point = _run_pipeline(
            index=idx,
            target_item=target_item,
            rate_per_sec=rate,
            graph=graph,
            canvas=canvas,
            placement_time_limit_s=placement_time_limit_s,
            random_state=random_state,
        )
        if point is not None:
            points.append(point)

    points.sort(key=lambda p: p.target_rate_per_sec)
    # Reassign monotone indices after sorting so callers can pick by index
    # without skipping numbers from dropped points.
    return [_with_index(p, i) for i, p in enumerate(points)]


def _with_index(point: ParetoPoint, index: int) -> ParetoPoint:
    if point.index == index:
        return point
    return ParetoPoint(
        index=index,
        target_rate_per_sec=point.target_rate_per_sec,
        footprint=point.footprint,
        bbox=point.bbox,
        demand=point.demand,
        machine_plan=point.machine_plan,
        flow_graph=point.flow_graph,
        block_graph=point.block_graph,
        placement=point.placement,
        routing=point.routing,
        inserters=point.inserters,
        power=point.power,
        status=point.status,
    )


def _run_pipeline(
    *,
    index: int,
    target_item: str,
    rate_per_sec: float,
    graph: RecipeHypergraph,
    canvas: tuple[int, int],
    placement_time_limit_s: float,
    random_state: int,
) -> ParetoPoint | None:
    try:
        demand = expand_demand(target_item, rate_per_sec, graph)
        machine_plan = solve_machine_counts(demand, graph)
    except (DemandError, MachinePlanError):
        return None

    flow_graph = FlowGraph.from_plan(demand, machine_plan, graph)
    block_graph = cluster_into_blocks(flow_graph, random_state=random_state)

    try:
        placement = place_blocks(
            block_graph,
            canvas,
            time_limit_s=placement_time_limit_s,
            random_state=random_state,
        )
    except PlacementError:
        return None

    try:
        routing = route_belts(placement, block_graph)
    except RoutingError:
        return None

    inserters = place_inserters(placement, block_graph, flow_graph, routing)
    occupied = _occupied_tiles(placement, routing, inserters)
    power = cover_with_poles(placement, block_graph, occupied)

    return ParetoPoint(
        index=index,
        target_rate_per_sec=demand.target_rate_per_sec,
        footprint=placement.bbox[0] * placement.bbox[1],
        bbox=placement.bbox,
        demand=demand,
        machine_plan=machine_plan,
        flow_graph=flow_graph,
        block_graph=block_graph,
        placement=placement,
        routing=routing,
        inserters=inserters,
        power=power,
        status="PARTIAL" if routing.status == "PARTIAL" else "OK",
    )


def _occupied_tiles(
    placement: Placement,
    routing: RoutingResult,
    inserters: InserterResult,
) -> frozenset[tuple[int, int]]:
    """Tiles the pole-cover may not stand on.

    Includes every machine footprint tile, every belt / underground /
    splitter tile from routing, and every inserter / boundary-belt tile
    from Phase 7a. Mirrors the contract documented on
    `cover_with_poles`.
    """
    tiles: set[tuple[int, int]] = set()

    for pb in placement.blocks.values():
        # Use a 3×3 footprint per machine — matches `power._MACHINE_FOOTPRINT`.
        for _mid, mx, my in pb.machine_tiles:
            for dx in range(3):
                for dy in range(3):
                    tiles.add((mx + dx, my + dy))

    for path in routing.paths:
        for seg in path.segments:
            tiles.add((seg.x, seg.y))
        for ug in path.undergrounds:
            tiles.add((ug.x, ug.y))
    for sp in routing.splitters:
        # Splitter spans the anchor tile plus one neighbour to the right
        # of its facing direction. Conservatively block both halves.
        tiles.add((sp.x, sp.y))
        # Facing 0=N / 2=E / 4=S / 6=W (Factorio encoding); right-of-face
        # offset is +x for N, -y for E, -x for S, +y for W.
        if sp.direction == 0:
            tiles.add((sp.x + 1, sp.y))
        elif sp.direction == 2:
            tiles.add((sp.x, sp.y + 1))
        elif sp.direction == 4:
            tiles.add((sp.x - 1, sp.y))
        elif sp.direction == 6:
            tiles.add((sp.x, sp.y - 1))

    for ins in inserters.inserters:
        tiles.add((ins.x, ins.y))
    for belt in inserters.boundary_belts:
        tiles.add((belt.x, belt.y))

    return frozenset(tiles)


__all__ = ["ParetoPoint", "default_epsilons", "pareto_sweep"]
