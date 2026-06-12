"""Unit tests for runtime entity classes."""

from __future__ import annotations

from factorio_blue_graph.layout.tier import BeltTier
from factorio_blue_graph.simulate.constants import MACHINE_INPUT_CAP
from factorio_blue_graph.simulate.runtime import (
    BeltPathRuntime,
    ChestRuntime,
    MachineRuntime,
)


def _gear_machine() -> MachineRuntime:
    return MachineRuntime(
        block_id="b0",
        recipe_id="iron-gear-wheel",
        machine_id="m0",
        tile=(0, 0),
        craft_ticks=30,  # 0.5s at 60 UPS
        inputs_per_craft={"iron-plate": 2},
        output_item="iron-gear-wheel",
        output_per_craft=1,
    )


def test_machine_runs_when_fed_and_drained():
    m = _gear_machine()
    # Feed for 600 ticks; each craft needs 30 ticks + 2 iron-plate.
    for _ in range(600):
        m.take_input("iron-plate", 2)
        m.step()
        # Drain output every tick.
        m.take_output("iron-gear-wheel", 100)
    # 600/30 = 20 crafts max; allow small startup slop.
    assert m.total_crafts >= 18, f"expected ≥18 crafts, got {m.total_crafts}"


def test_machine_starves_without_inputs():
    m = _gear_machine()
    for _ in range(300):
        m.step()
    assert m.total_crafts == 0
    assert m.starvation_ticks == 300


def test_machine_blocks_when_output_full():
    m = _gear_machine()
    # Pre-fill the output slot.
    m.output_slots["iron-gear-wheel"] = 1000
    for _ in range(50):
        m.take_input("iron-plate", 2)
        m.step()
    assert m.total_crafts == 0
    assert m.blocked_output_ticks > 0


def test_chest_infinite_source_gives_indefinitely():
    c = ChestRuntime(
        tile=(0, 0),
        name="wooden-chest",
        item_filter="iron-ore",
        infinite_source=True,
    )
    for _ in range(100):
        assert c.take("iron-ore", 5) == 5


def test_chest_finite_holds_received_items():
    c = ChestRuntime(tile=(0, 0), name="wooden-chest", item_filter="iron-plate")
    assert c.give("iron-plate", 10) == 10
    assert c.has("iron-plate")
    assert c.take("iron-plate", 7) == 7
    assert c.inventory["iron-plate"] == 3


def test_chest_filter_rejects_wrong_item():
    c = ChestRuntime(tile=(0, 0), name="wooden-chest", item_filter="iron-plate")
    assert c.give("copper-plate", 5) == 0


def test_belt_path_capacity_caps_inflow():
    b = BeltPathRuntime(
        path_id="p0",
        item_id="iron-plate",
        source_block="A",
        target_block="B",
        tier=BeltTier.YELLOW,
        lane_count=1,
        delay_ticks=1,
    )
    # Yellow belt = 15/s = 0.25/tick. Offer 100 items in tick 0; only a few
    # should slip through the bucket before saturation.
    accepted = 0
    for _ in range(100):
        if b.offer(0, "iron-plate"):
            accepted += 1
    assert 0 < accepted <= 5  # bucket starts at 0, fills to 1 per accepted offer


def test_machine_cap_clamps_input():
    # A machine buffers at most 4 crafts' worth of an ingredient (Factorio
    # inserters stop overfilling), never more than MACHINE_INPUT_CAP.
    m = _gear_machine()
    accepted = m.take_input("iron-plate", MACHINE_INPUT_CAP * 2)
    assert accepted == min(MACHINE_INPUT_CAP, 4 * m.inputs_per_craft["iron-plate"])
