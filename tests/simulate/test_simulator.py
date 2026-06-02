"""Integration tests: build a small PlanState and drive the simulator."""

from __future__ import annotations

from factorio_blue_graph.layout.inserter import InserterResult
from factorio_blue_graph.layout.placement import Placement
from factorio_blue_graph.layout.power import PoleResult
from factorio_blue_graph.layout.routing import RoutingResult
from factorio_blue_graph.model.blueprint import (
    DIR_E,
    PowerSourceResult,
)
from factorio_blue_graph.model.graph import BlockGraph, FlowGraph
from factorio_blue_graph.repair.state import PlanState
from factorio_blue_graph.simulate import run_simulation


def _empty_state(canvas: tuple[int, int] = (20, 20)) -> PlanState:
    return PlanState(
        placement=Placement(
            blocks={},
            canvas=canvas,
            bbox=(0, 0),
            flow_weighted_distance=0.0,
            status="OPTIMAL",
            solver="cp-sat",
        ),
        block_graph=BlockGraph(),
        flow_graph=FlowGraph(),
        routing=RoutingResult(
            paths=(),
            splitters=(),
            ripup_count=0,
            unresolved=(),
            status="OK",
            canvas=canvas,
        ),
        inserters=InserterResult(inserters=(), boundary_belts=(), unresolved=()),
        power=PoleResult(poles=(), uncovered_machines=()),
        power_source=PowerSourceResult(),
    )


def test_empty_state_returns_zero_throughput():
    state = _empty_state()
    report = run_simulation(
        state,
        target_item="iron-plate",
        target_rate_per_sec=1.0,
        warmup_ticks=60,
        measure_ticks=60,
    )
    assert report.achieved_rate_per_sec == 0
    assert not report.ok
    codes = {b.code for b in report.bottlenecks}
    assert "OUTPUT_UNDERDELIVERY" in codes


def test_zero_target_rate_returns_ok():
    state = _empty_state()
    report = run_simulation(
        state,
        target_item="iron-plate",
        target_rate_per_sec=0.0,
        warmup_ticks=10,
        measure_ticks=10,
    )
    assert report.ok
    assert report.achievement_ratio == 1.0


def test_iron_smelter_with_raw_ore_chest_produces_iron_plate():
    """End-to-end happy path on a hand-built topology.

    One stone furnace fed by an infinite iron-ore chest via inserter, with
    an output chest collecting iron-plate via a second inserter. After a
    short warmup the iron-plate sink should accumulate.
    """
    from factorio_blue_graph.layout.placement import PlacedBlock
    from factorio_blue_graph.model.blueprint import Chest, Inserter
    from factorio_blue_graph.model.graph import Block, MachineNode
    from factorio_blue_graph.planning.lp import STONE_FURNACE

    canvas = (20, 20)
    smelter_id = "iron-plate#0"
    node = MachineNode(
        id=smelter_id,
        recipe_id="iron-plate",
        machine=STONE_FURNACE,
        crafts_per_sec=1.0,
    )
    block = Block(
        id="block_iron",
        members=(node,),
        recipes=("iron-plate",),
        footprint=(STONE_FURNACE.footprint[0] + 4, STONE_FURNACE.footprint[1] + 4),
        primary_recipe="iron-plate",
    )

    bg = BlockGraph()
    bg.add_block(block)

    # Place machine at (5,5) — 2x2 footprint -> occupies (5..6, 5..6).
    placed = PlacedBlock(
        block_id="block_iron",
        x=4,
        y=4,
        rotated=False,
        width=block.footprint[0],
        height=block.footprint[1],
        machine_tiles=((smelter_id, 5, 5),),
    )
    placement = Placement(
        blocks={"block_iron": placed},
        canvas=canvas,
        bbox=block.footprint,
        flow_weighted_distance=0.0,
        status="OPTIMAL",
        solver="cp-sat",
    )

    # Inserter east of machine drops INTO machine (direction = W from inserter's perspective,
    # i.e. drop tile = machine tile). For our model: drop tile is _step(ins, direction).
    # Place ore chest at (8,5), inserter at (7,5) facing W=6 (drop to 6,5 which is machine).
    ore_chest = Chest(x=8, y=5, name="wooden-chest", item_id="iron-ore")
    feed_inserter = Inserter(x=7, y=5, direction=6, name="inserter")

    # Output: inserter at (3,5) facing W=6 takes from machine (5,5)? No, need pickup from machine.
    # Use a south-side inserter: inserter (5,7) facing S=4 drops to (5,8) = output chest.
    # Pickup tile = (5,6) which is the machine.
    out_chest = Chest(x=5, y=8, name="wooden-chest", item_id="iron-plate")
    out_inserter = Inserter(x=5, y=7, direction=4, name="inserter")

    state = _empty_state(canvas)
    state.placement = placement
    state.block_graph = bg
    state.chests = [ore_chest, out_chest]
    state.inserters = InserterResult(
        inserters=(feed_inserter, out_inserter), boundary_belts=(), unresolved=()
    )

    report = run_simulation(
        state,
        target_item="iron-plate",
        target_rate_per_sec=0.5,  # very modest target
        warmup_ticks=600,
        measure_ticks=600,
    )
    # Stone furnace at speed 1.0, iron-plate recipe time 3.2s → 1 craft per 3.2s ≈ 0.31/s.
    # Target 0.5/s is above what one furnace can deliver, so OUTPUT_UNDERDELIVERY is expected
    # but the sim should at least be producing something.
    assert report.total_crafts_by_recipe.get("iron-plate", 0) > 0
    _ = DIR_E  # silence unused-import lint when this assert structure changes
