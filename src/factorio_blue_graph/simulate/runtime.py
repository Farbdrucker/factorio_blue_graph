"""Mutable per-entity runtime state for the tick simulator.

Kept minimal: only the fields the tick loop and the bottleneck attributor
actually read. No fancy state machines — Factorio's belt and inserter
sub-tick state aren't needed to measure end-to-end throughput.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from factorio_blue_graph.layout.tier import BeltTier
from factorio_blue_graph.simulate.constants import (
    BELT_CAP_PER_TICK,
    CHEST_CAP,
    MACHINE_INPUT_CAP,
    MACHINE_OUTPUT_CAP,
)


@dataclass
class MachineRuntime:
    block_id: str
    recipe_id: str
    machine_id: str
    tile: tuple[int, int]
    craft_ticks: int
    inputs_per_craft: dict[str, int]
    output_item: str
    output_per_craft: int
    progress: int = 0
    input_slots: dict[str, int] = field(default_factory=dict)
    output_slots: dict[str, int] = field(default_factory=dict)
    crafting: bool = False
    total_crafts: int = 0
    starvation_ticks: int = 0
    blocked_output_ticks: int = 0

    def can_start(self) -> bool:
        for item, need in self.inputs_per_craft.items():
            if self.input_slots.get(item, 0) < need:
                return False
        return True

    def output_full(self) -> bool:
        return self.output_slots.get(self.output_item, 0) >= MACHINE_OUTPUT_CAP

    def take_input(self, item: str, count: int) -> int:
        if item not in self.inputs_per_craft:
            return 0
        held = self.input_slots.get(item, 0)
        # Factorio inserters stop feeding a machine once it buffers a few
        # crafts' worth of an ingredient. Mirroring that keeps one machine
        # from hoarding a shared belt's supply while siblings starve.
        cap = min(MACHINE_INPUT_CAP, 4 * self.inputs_per_craft[item])
        accepted = min(count, cap - held)
        if accepted > 0:
            self.input_slots[item] = held + accepted
        return accepted

    def take_output(self, item: str, count: int) -> int:
        if item != self.output_item:
            return 0
        held = self.output_slots.get(item, 0)
        give = min(count, held)
        if give > 0:
            self.output_slots[item] = held - give
        return give

    def step(self) -> None:
        if not self.crafting:
            if self.output_full():
                self.blocked_output_ticks += 1
                return
            if self.can_start():
                for item, need in self.inputs_per_craft.items():
                    self.input_slots[item] -= need
                self.crafting = True
                self.progress = 0
            else:
                self.starvation_ticks += 1
                return
        self.progress += 1
        if self.progress >= self.craft_ticks:
            held = self.output_slots.get(self.output_item, 0)
            self.output_slots[self.output_item] = held + self.output_per_craft
            self.total_crafts += 1
            self.crafting = False
            self.progress = 0


_INSERTER_STATE_IDLE = 0
_INSERTER_STATE_BUSY = 1


@dataclass
class InserterRuntime:
    """Cyclic inserter approximation.

    Models swing as a single ``BUSY`` countdown after a successful pickup;
    on transition back to ``IDLE`` the held items are deposited at the drop
    tile. This loses the directional half-swing distinction but preserves
    the steady-state items/sec rate that the bottleneck attributor cares
    about.
    """

    tile: tuple[int, int]
    name: str
    pickup_tile: tuple[int, int]
    drop_tile: tuple[int, int]
    swing_ticks: int
    items_per_swing: int
    state: int = _INSERTER_STATE_IDLE
    remaining_ticks: int = 0
    held_item: str | None = None
    held_count: int = 0
    active_ticks: int = 0
    blocked_pickup_ticks: int = 0
    blocked_drop_ticks: int = 0


@dataclass
class ChestRuntime:
    tile: tuple[int, int]
    name: str
    item_filter: str | None
    infinite_source: bool = False
    inventory: dict[str, int] = field(default_factory=dict)

    def take(self, item: str, count: int) -> int:
        if self.infinite_source and (self.item_filter is None or self.item_filter == item):
            return count
        held = self.inventory.get(item, 0)
        give = min(count, held)
        if give > 0:
            self.inventory[item] = held - give
        return give

    def give(self, item: str, count: int) -> int:
        if self.item_filter is not None and self.item_filter != item:
            return 0
        held = self.inventory.get(item, 0)
        room = CHEST_CAP - held
        accept = min(count, room)
        if accept > 0:
            self.inventory[item] = held + accept
        return accept

    def has(self, item: str) -> bool:
        if self.infinite_source and (self.item_filter is None or self.item_filter == item):
            return True
        return self.inventory.get(item, 0) > 0


@dataclass
class BeltPathRuntime:
    """Token-bucket model for one routed ``BeltPath``.

    The path is treated as a single FIFO conveyor with capacity-per-tick
    derived from its tier. ``delay_ticks`` is the head-to-tail propagation
    time so warmup naturally accounts for empty belts filling. ``saturated``
    counters drive the ``BELT_SATURATED`` bottleneck attribution.
    """

    path_id: str
    item_id: str
    source_block: str
    target_block: str
    tier: BeltTier
    lane_count: int
    delay_ticks: int
    head_in: deque[tuple[int, str]] = field(default_factory=deque)
    tail_out: deque[str] = field(default_factory=deque)
    cap_per_tick: float = 0.0
    bucket: float = 0.0
    inflow_attempts: int = 0
    inflow_rejected: int = 0
    delivered: int = 0

    def __post_init__(self) -> None:
        if self.cap_per_tick <= 0:
            self.cap_per_tick = BELT_CAP_PER_TICK[self.tier] * max(1, self.lane_count)

    def offer(self, current_tick: int, item: str) -> bool:
        self.inflow_attempts += 1
        if self.bucket >= 1.0:
            self.inflow_rejected += 1
            return False
        self.bucket += 1.0
        self.head_in.append((current_tick + self.delay_ticks, item))
        return True

    def step(self, current_tick: int) -> None:
        self.bucket = max(0.0, self.bucket - self.cap_per_tick)
        while self.head_in and self.head_in[0][0] <= current_tick:
            self.tail_out.append(self.head_in.popleft()[1])

    def pop(self) -> str | None:
        if not self.tail_out:
            return None
        item = self.tail_out.popleft()
        self.delivered += 1
        return item

    def peek(self) -> str | None:
        return self.tail_out[0] if self.tail_out else None

    def saturation_ratio(self) -> float:
        if self.inflow_attempts == 0:
            return 0.0
        return self.inflow_rejected / self.inflow_attempts


__all__ = [
    "BeltPathRuntime",
    "ChestRuntime",
    "InserterRuntime",
    "MachineRuntime",
]
