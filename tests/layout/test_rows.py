"""Row-based layout engine: geometry, connectivity, and edge cases.

Builds real plans (phases 1–4) for several targets and checks the
invariants the engine is supposed to guarantee by construction:
tile-exclusive occupancy, valid inserter geometry, continuous belts,
full pole coverage on one connected network.
"""

from __future__ import annotations

import pytest

from factorio_blue_graph.layout.clustering import cluster_into_blocks
from factorio_blue_graph.layout.rows import RowLayoutError, RowLayoutResult, build_row_layout
from factorio_blue_graph.model.blueprint import (
    DIR_E,
    DIR_N,
    DIR_S,
    DIR_W,
    POLE_RADIUS,
    POLE_WIRE_REACH,
)
from factorio_blue_graph.model.graph import FlowGraph, RecipeHypergraph
from factorio_blue_graph.planning.demand import expand_demand
from factorio_blue_graph.planning.lp import solve_machine_counts
from factorio_blue_graph.verify.runner import verify_plan

_STEP = {DIR_N: (0, -1), DIR_E: (1, 0), DIR_S: (0, 1), DIR_W: (-1, 0)}
_REACH = {"long-handed-inserter": 2}

GRAPH = RecipeHypergraph.load_default()


def _build(item: str, rate_per_sec: float):
    demand = expand_demand(item, rate_per_sec, GRAPH)
    machine_plan = solve_machine_counts(demand, GRAPH)
    flow_graph = FlowGraph.from_plan(demand, machine_plan, GRAPH)
    block_graph = cluster_into_blocks(flow_graph, single_recipe_blocks=True)
    return build_row_layout(flow_graph, block_graph, GRAPH, demand, item), block_graph, flow_graph


def _verify(item: str, rate_per_sec: float):
    row, block_graph, flow_graph = _build(item, rate_per_sec)
    report = verify_plan(
        row.placement,
        block_graph,
        flow_graph,
        row.routing,
        row.inserters,
        row.poles,
        power_source=None,
        external_ports=row.ports,
        chests=row.chests,
    )
    return row, report


@pytest.fixture(scope="module")
def ec_layout() -> RowLayoutResult:
    return _build("electronic-circuit", 1.0)[0]


def _machine_tiles(row: RowLayoutResult) -> dict[tuple[int, int], str]:
    tiles: dict[tuple[int, int], str] = {}
    for pb in row.placement.blocks.values():
        for mid, mx, my in pb.machine_tiles:
            fw, fh = (2, 2) if mid.split("#")[0] in ("iron-plate", "copper-plate") else (3, 3)
            for dx in range(fw):
                for dy in range(fh):
                    tiles[(mx + dx, my + dy)] = mid
    return tiles


def _belt_tiles(row: RowLayoutResult) -> set[tuple[int, int]]:
    tiles: set[tuple[int, int]] = set()
    for path in row.routing.paths:
        tiles.update((s.x, s.y) for s in path.segments)
        tiles.update((u.x, u.y) for u in path.undergrounds)
    return tiles


def test_no_tile_overlaps(ec_layout: RowLayoutResult) -> None:
    seen: set[tuple[int, int]] = set()

    def claim(tile: tuple[int, int], what: str) -> None:
        assert tile not in seen, f"{what} overlaps another entity at {tile}"
        seen.add(tile)

    for tile in _machine_tiles(ec_layout):
        claim(tile, "machine")
    for tile in _belt_tiles(ec_layout):
        claim(tile, "belt")
    for ins in ec_layout.inserters.inserters:
        claim((ins.x, ins.y), "inserter")
    for chest in ec_layout.chests:
        claim((chest.x, chest.y), "chest")
    for pole in ec_layout.poles.poles:
        claim((pole.x, pole.y), "pole")


def test_inserter_geometry(ec_layout: RowLayoutResult) -> None:
    """Every inserter bridges two real entities: one end on a machine or
    chest, the other on a belt, machine, or chest."""
    machines = _machine_tiles(ec_layout)
    belts = _belt_tiles(ec_layout)
    chests = {(c.x, c.y) for c in ec_layout.chests}

    def kind(tile: tuple[int, int]) -> str | None:
        if tile in machines:
            return "machine"
        if tile in belts:
            return "belt"
        if tile in chests:
            return "chest"
        return None

    for ins in ec_layout.inserters.inserters:
        reach = _REACH.get(ins.name, 1)
        dx, dy = _STEP[ins.direction]
        drop = (ins.x + dx * reach, ins.y + dy * reach)
        pickup = (ins.x - dx * reach, ins.y - dy * reach)
        assert kind(drop) is not None, f"inserter {(ins.x, ins.y)} drops onto nothing at {drop}"
        assert kind(pickup) is not None, (
            f"inserter {(ins.x, ins.y)} picks up from nothing at {pickup}"
        )


def test_belt_paths_continuous_and_verifier_clean() -> None:
    row, report = _verify("electronic-circuit", 1.0)
    codes = [i.code for i in report.issues if i.severity.name == "ERROR"]
    # NO_POWER_SOURCE is expected — the test skips phase 8b on purpose.
    codes = [c for c in codes if c != "NO_POWER_SOURCE"]
    assert codes == [], f"verifier errors: {codes}"


def test_fanout_item_reaches_all_consumers() -> None:
    """copper-cable feeds both electronic-circuit and advanced-circuit —
    one snaking path must pass every consumer's lane (verifier-clean)."""
    row, report = _verify("advanced-circuit", 0.5)
    cable = [p for p in row.routing.paths if p.item_id == "copper-cable"]
    assert len(cable) == 1
    codes = [
        i.code for i in report.issues if i.severity.name == "ERROR" and i.code != "NO_POWER_SOURCE"
    ]
    assert codes == []


def test_three_ingredient_recipe_uses_bottom_lane() -> None:
    """advanced-circuit has 3 solid ingredients; the 3rd rides lane C and
    is picked by north-facing long-handed inserters."""
    row, _, _ = _build("advanced-circuit", 0.5)
    north_long = [
        i
        for i in row.inserters.inserters
        if i.name == "long-handed-inserter" and i.direction == DIR_N
    ]
    assert north_long, "expected lane-C pickers for the 3rd ingredient"


def test_poles_cover_everything_and_connect(ec_layout: RowLayoutResult) -> None:
    poles = [(p.x, p.y) for p in ec_layout.poles.poles]
    assert poles

    def covered(tile: tuple[int, int]) -> bool:
        return any(
            abs(tile[0] - px) <= POLE_RADIUS and abs(tile[1] - py) <= POLE_RADIUS
            for px, py in poles
        )

    for tile, mid in _machine_tiles(ec_layout).items():
        assert covered(tile), f"machine {mid} tile {tile} unpowered"
    for ins in ec_layout.inserters.inserters:
        assert covered((ins.x, ins.y)), f"inserter at {(ins.x, ins.y)} unpowered"

    # Union-find over wire reach: one connected component.
    parent = {p: p for p in poles}

    def find(p):
        while parent[p] != p:
            parent[p] = parent[parent[p]]
            p = parent[p]
        return p

    for a in poles:
        for b in poles:
            if abs(a[0] - b[0]) <= POLE_WIRE_REACH and abs(a[1] - b[1]) <= POLE_WIRE_REACH:
                parent[find(a)] = find(b)
    assert len({find(p) for p in poles}) == 1, "pole network is disconnected"


def test_single_machine_stage() -> None:
    """A 1-machine plan still gets a pole column and valid geometry."""
    row, _, _ = _build("iron-gear-wheel", 0.05)
    assert row.poles.poles
    assert row.routing.status == "OK"


def test_flow_above_blue_belt_raises() -> None:
    with pytest.raises(RowLayoutError, match="single-lane"):
        _build("iron-gear-wheel", 50.0)


def test_ports_declare_inputs_and_output() -> None:
    row, _, _ = _build("electronic-circuit", 1.0)
    roles = {(p.item_id, p.role) for p in row.ports}
    assert ("iron-ore", "input") in roles
    assert ("copper-ore", "input") in roles
    assert ("electronic-circuit", "output") in roles
