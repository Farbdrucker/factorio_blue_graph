"""Deterministic row-based layout engine (``--layout rows``).

Replaces phases 5–8 (placement, routing, inserters, I/O chests, pole
cover) with a correct-by-construction layout: one horizontal row of
machines per recipe stage, belt lanes directly above/below each row,
and inserters in fixed, known-valid geometry. Connectivity is
guaranteed by construction, so the structural verifier and the tick
simulator pass without repair.

Vertical structure (top → bottom):

    feeder band per raw item        chests → inserters → belt head
    stage band per recipe           topological order, producers first
    sink band                       belt → inserters → target chests

One stage band, for machines of footprint ``(w, h)``:

    y+0      lane B   2nd solid ingredient (long-handed pickers, reach 2)
    y+1      lane A   1st solid ingredient (short pickers, reach 1)
    y+2      input inserter row   (direction S: pick lane, drop machine)
    y+3 …    machine rows; medium poles in the gap columns
    y+3+h    output inserter row  (direction S: pick machine, drop output
             lane) + long-handed lane-C pickers (direction N)
    y+4+h    output lane (flows toward the east corridor)
    y+5+h    lane C   optional 3rd solid ingredient
    y+6+h    separator

Items travel between bands on one continuous ``BeltPath`` per item:
east along the source row into a dedicated corridor column, south to
the first consumer's input lane, west across the row; further
consumers snake down through alternating west/east corridor columns
(the simulator models a path as one shared FIFO, so any picker along
it can withdraw — no splitters needed). Corridor columns are spaced 3
tiles apart; a horizontal run crossing a foreign column dives under it
with an underground pair whose entry/exit sit exactly 2 tiles apart
(the minimum the verifier matches, valid for every tier).

v1 limits (raise ``RowLayoutError``): more than 3 solid ingredients
per recipe, flows above one blue belt (45 items/sec), and inserter
demand beyond one machine face. Fluids and furnace fuel stay
unmodeled, matching the rest of the planner.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from factorio_blue_graph.layout.inserter import InserterResult
from factorio_blue_graph.layout.placement import PlacedBlock, Placement
from factorio_blue_graph.layout.power import PoleResult
from factorio_blue_graph.layout.routing import BeltPath, RoutingResult
from factorio_blue_graph.layout.tier import TIER_CAPACITY, BeltTier, pick_tier
from factorio_blue_graph.model.blueprint import (
    DIR_E,
    DIR_N,
    DIR_S,
    DIR_W,
    INSERTER_THROUGHPUT,
    BeltSegment,
    Chest,
    Inserter,
    IOPort,
    Pole,
    UndergroundBelt,
)
from factorio_blue_graph.model.graph import (
    BlockGraph,
    FlowGraph,
    MachineNode,
    RecipeHypergraph,
)
from factorio_blue_graph.planning.demand import DemandPlan
from factorio_blue_graph.planning.lp import FLUID_INPUTS

_UG_NAME = {
    BeltTier.YELLOW: "underground-belt",
    BeltTier.RED: "fast-underground-belt",
    BeltTier.BLUE: "express-underground-belt",
}

# Tiles between adjacent corridor columns. 3 leaves a 2-tile gap so each
# foreign column can be hopped with an independent underground pair.
_COL_PITCH = 3
# Gap column between adjacent machines in a row (hosts poles).
_MACHINE_GAP = 1
# Free rows below the sink band so `emit_power_source` finds a strip.
_POWER_MARGIN = 12
# Medium pole covers Chebyshev distance 3 → poles every 6 tiles cover a row.
_POLE_COVER_PITCH = 6
# Inserters are sized for rate × headroom: a transfer point running at ~100%
# of nominal throughput loses a few percent to swing quantization, and those
# losses compound multiplicatively down the production chain.
_INSERTER_HEADROOM = 1.3


class RowLayoutError(Exception):
    """Raised when a plan cannot be expressed in the v1 row layout."""


@dataclass(frozen=True)
class RowLayoutResult:
    placement: Placement
    routing: RoutingResult
    inserters: InserterResult
    chests: tuple[Chest, ...]
    ports: tuple[IOPort, ...]
    poles: PoleResult
    canvas: tuple[int, int]


# ---------------------------------------------------------------------------
# Intermediate planning structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _LanePlan:
    """How one solid ingredient reaches each machine of a stage."""

    item_id: str
    slot: str  # "A" (top, reach 1), "B" (top, reach 2), "C" (bottom, reach 2)
    inserter: str
    count: int  # inserters per machine


@dataclass
class _Stage:
    recipe_id: str
    machines: list[MachineNode]
    footprint: tuple[int, int]
    lane_plans: list[_LanePlan]
    out_plan: tuple[str, int]  # (inserter name, count per machine)
    band_y: int = 0
    band_h: int = 0

    @property
    def width(self) -> int:
        return len(self.machines) * (self.footprint[0] + _MACHINE_GAP)

    @property
    def lane_items(self) -> list[str]:
        return [p.item_id for p in self.lane_plans]

    @property
    def has_lane_b(self) -> bool:
        return any(p.slot == "B" for p in self.lane_plans)

    @property
    def has_lane_c(self) -> bool:
        return any(p.slot == "C" for p in self.lane_plans)

    @property
    def input_inserter_y(self) -> int:
        return self.band_y + 1 + (1 if self.has_lane_b else 0)

    @property
    def machine_y(self) -> int:
        return self.input_inserter_y + 1

    @property
    def output_inserter_y(self) -> int:
        return self.machine_y + self.footprint[1]

    @property
    def output_lane_y(self) -> int:
        return self.output_inserter_y + 1

    def lane_y(self, item_id: str) -> int:
        """Absolute y of the lane row carrying `item_id` into this stage."""
        slot = next(p.slot for p in self.lane_plans if p.item_id == item_id)
        if slot == "A":  # directly above the input inserter row
            return self.input_inserter_y - 1
        if slot == "B":  # top row of the band
            return self.band_y
        return self.output_lane_y + 1  # "C" — below the output lane


@dataclass
class _Feeder:
    item_id: str
    rate: float
    band_y: int = 0

    @property
    def chest_y(self) -> int:
        return self.band_y

    @property
    def inserter_y(self) -> int:
        return self.band_y + 1

    @property
    def lane_y(self) -> int:
        return self.band_y + 2

    @property
    def separator_y(self) -> int:
        return self.band_y + 3


@dataclass
class _Channel:
    """One item flow: a source row feeding one or more consumer lane rows."""

    item_id: str
    rate: float
    source_y: int  # row where producers drop the item
    source_block: str
    target_block: str
    consumer_ys: list[int]  # lane rows to visit, top→bottom
    east_col: int = 0
    west_col: int | None = None


class _Canvas:
    """Occupancy bookkeeping; every emitted entity claims its tiles."""

    def __init__(self) -> None:
        self.occupied: dict[tuple[int, int], str] = {}

    def claim(self, x: int, y: int, what: str) -> None:
        prev = self.occupied.get((x, y))
        if prev is not None:
            raise RowLayoutError(f"tile ({x},{y}) double-booked: {prev} vs {what}")
        self.occupied[(x, y)] = what


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_row_layout(
    flow_graph: FlowGraph,
    block_graph: BlockGraph,
    recipe_graph: RecipeHypergraph,
    demand: DemandPlan,
    target_item: str,
) -> RowLayoutResult:
    stages = _build_stages(flow_graph, recipe_graph)
    if not stages:
        raise RowLayoutError("no machine stages to lay out")
    feeders = _build_feeders(stages, recipe_graph)

    # ---- vertical layout ---------------------------------------------------
    y = 1
    for f in feeders:
        f.band_y = y
        y += 4  # chest, inserter, lane, separator
    for s in stages:
        s.band_y = y
        lanes_above = 1 + (1 if s.has_lane_b else 0)
        lane_c = 1 if s.has_lane_c else 0
        s.band_h = lanes_above + 1 + s.footprint[1] + 1 + 1 + lane_c + 1
        y += s.band_h
    sink_lane_y = y
    sink_inserter_y = y + 1
    sink_chest_y = y + 2
    sink_pole_y = y + 3
    height = sink_pole_y + 1 + _POWER_MARGIN

    channels = _build_channels(stages, feeders, recipe_graph, target_item, sink_lane_y)

    # ---- horizontal layout: corridor columns flank the rows region ---------
    n_west = sum(1 for c in channels if len(c.consumer_ys) >= 2)
    x0 = 2 + (_COL_PITCH * n_west + 2 if n_west else 0)
    sink_plan = _feeder_plan(demand.target_rate_per_sec)
    row_width = max(
        max(s.width for s in stages),
        _feeder_width(feeders) + 1,
        sink_plan[1] + 1,
    )
    east_base = x0 + row_width + 2
    west_idx = 0
    for k, ch in enumerate(channels):
        ch.east_col = east_base + _COL_PITCH * k
        if len(ch.consumer_ys) >= 2:
            ch.west_col = 2 + _COL_PITCH * west_idx
            west_idx += 1
    width = east_base + _COL_PITCH * len(channels) + 2

    canvas = _Canvas()
    all_cols = {c.east_col for c in channels} | {
        c.west_col for c in channels if c.west_col is not None
    }

    # ---- emit ---------------------------------------------------------------
    placed_blocks = _emit_machines(stages, block_graph, x0, canvas)

    inserters: list[Inserter] = []
    for s in stages:
        inserters.extend(_emit_stage_inserters(s, x0, canvas))

    chests: list[Chest] = []
    ports: list[IOPort] = []
    for f in feeders:
        f_chests, f_inserters = _emit_feeder_band(f, x0, canvas)
        chests.extend(f_chests)
        inserters.extend(f_inserters)
        ports.append(_feeder_port(f, stages, block_graph))

    s_chests, s_inserters = _emit_sink_band(
        sink_plan, target_item, x0, sink_inserter_y, sink_chest_y, canvas
    )
    chests.extend(s_chests)
    inserters.extend(s_inserters)
    ports.append(_sink_port(target_item, stages, block_graph, (x0, sink_chest_y)))

    paths = tuple(_emit_channel_belt(ch, x0, x0 + row_width, all_cols, canvas) for ch in channels)

    poles = _emit_poles(stages, feeders, x0, sink_plan[1], sink_pole_y, canvas)

    placement = Placement(
        blocks=placed_blocks,
        canvas=(width, height),
        bbox=(width, height),
        flow_weighted_distance=0.0,
        status="OPTIMAL",
        solver="rows",
    )
    routing = RoutingResult(
        paths=paths,
        splitters=(),
        ripup_count=0,
        unresolved=(),
        status="OK",
        canvas=(width, height),
    )
    return RowLayoutResult(
        placement=placement,
        routing=routing,
        inserters=InserterResult(inserters=tuple(inserters), boundary_belts=(), unresolved=()),
        chests=tuple(chests),
        ports=tuple(ports),
        poles=PoleResult(poles=tuple(poles), uncovered_machines=()),
        canvas=(width, height),
    )


# ---------------------------------------------------------------------------
# Stage / feeder / channel planning
# ---------------------------------------------------------------------------


def _build_stages(flow_graph: FlowGraph, recipe_graph: RecipeHypergraph) -> list[_Stage]:
    by_recipe: dict[str, list[MachineNode]] = {}
    for node in flow_graph.nodes.values():
        by_recipe.setdefault(node.recipe_id, []).append(node)
    for machines in by_recipe.values():
        machines.sort(key=lambda m: m.id)

    stages: list[_Stage] = []
    for recipe_id in _topo_order(set(by_recipe), recipe_graph):
        machines = by_recipe[recipe_id]
        recipe = recipe_graph.recipes[recipe_id]
        per_craft = {
            ing.item_id: ing.amount for ing in recipe.ingredients if ing.item_id not in FLUID_INPUTS
        }
        if len(per_craft) > 3:
            raise RowLayoutError(
                f"recipe {recipe_id} has {len(per_craft)} solid ingredients; "
                "the row layout supports at most 3"
            )
        crafts = machines[0].crafts_per_sec
        items = sorted(per_craft, key=lambda i: (-per_craft[i] * crafts, i))
        w = machines[0].machine.footprint[0]
        yield_ = recipe.yield_ or 1.0
        lane_plans, out_plan = _assign_lanes(
            recipe_id,
            [(i, crafts * per_craft[i]) for i in items],
            crafts * yield_,
            w,
        )
        stages.append(
            _Stage(
                recipe_id=recipe_id,
                machines=machines,
                footprint=machines[0].machine.footprint,
                lane_plans=lane_plans,
                out_plan=out_plan,
            )
        )
    return stages


def _assign_lanes(
    recipe_id: str,
    item_rates: list[tuple[str, float]],  # per-machine items/sec, rate-desc
    out_rate: float,
    w: int,
) -> tuple[list[_LanePlan], tuple[str, int]]:
    """Assign each ingredient a lane slot and inserter plan.

    Lane A (top, short inserters) takes the heaviest flow. Further
    ingredients are long-handed: the 2nd prefers slot B (top) but moves
    to slot C (bottom) when the top face can't host both; the 3rd always
    takes slot C. Slots share their face's `w` columns with each other
    (top: A+B) or with the output inserters (bottom: out+C).
    """

    def long_count(item: str, rate: float) -> int:
        n = max(
            1, math.ceil(rate * _INSERTER_HEADROOM / INSERTER_THROUGHPUT["long-handed-inserter"])
        )
        if n > w - 1:
            raise RowLayoutError(
                f"recipe {recipe_id}: ingredient {item} needs {n} long-handed "
                f"inserters per machine; only {w - 1} fit"
            )
        return n

    plans: list[_LanePlan] = []
    slot_b: _LanePlan | None = None
    slot_c: _LanePlan | None = None
    if len(item_rates) >= 2:
        item, rate = item_rates[1]
        slot_b = _LanePlan(item, "B", "long-handed-inserter", long_count(item, rate))
    if len(item_rates) >= 3:
        item, rate = item_rates[2]
        slot_c = _LanePlan(item, "C", "long-handed-inserter", long_count(item, rate))

    item_a, rate_a = item_rates[0]
    try:
        name_a, count_a = _pick_within_budget(
            recipe_id, rate_a, w - (slot_b.count if slot_b else 0)
        )
    except RowLayoutError:
        # Top face can't host lane A next to lane B — move the 2nd
        # ingredient to the bottom (slot C) if that side is free.
        if slot_b is None or slot_c is not None:
            raise
        slot_c = _LanePlan(slot_b.item_id, "C", slot_b.inserter, slot_b.count)
        slot_b = None
        name_a, count_a = _pick_within_budget(recipe_id, rate_a, w)

    plans.append(_LanePlan(item_a, "A", name_a, count_a))
    if slot_b is not None:
        plans.append(slot_b)
    if slot_c is not None:
        plans.append(slot_c)
    out_plan = _pick_within_budget(recipe_id, out_rate, w - (slot_c.count if slot_c else 0))
    return plans, out_plan


def _topo_order(recipe_ids: set[str], recipe_graph: RecipeHypergraph) -> list[str]:
    """Producers before consumers, deterministic by recipe id."""
    order: list[str] = []
    seen: set[str] = set()

    def visit(rid: str) -> None:
        if rid in seen:
            return
        seen.add(rid)
        recipe = recipe_graph.recipes.get(rid)
        if recipe is not None:
            for ing in sorted(recipe.ingredients, key=lambda i: i.item_id):
                if ing.item_id in recipe_ids:
                    visit(ing.item_id)
        order.append(rid)

    for rid in sorted(recipe_ids):
        visit(rid)
    return order


def _build_feeders(stages: list[_Stage], recipe_graph: RecipeHypergraph) -> list[_Feeder]:
    produced = {s.recipe_id for s in stages}
    rates: dict[str, float] = {}
    for s in stages:
        recipe = recipe_graph.recipes[s.recipe_id]
        for ing in recipe.ingredients:
            if ing.item_id in produced or ing.item_id in FLUID_INPUTS:
                continue
            stage_rate = sum(m.crafts_per_sec for m in s.machines) * ing.amount
            rates[ing.item_id] = rates.get(ing.item_id, 0.0) + stage_rate
    return [_Feeder(item_id=i, rate=rates[i]) for i in sorted(rates)]


def _build_channels(
    stages: list[_Stage],
    feeders: list[_Feeder],
    recipe_graph: RecipeHypergraph,
    target_item: str,
    sink_lane_y: int,
) -> list[_Channel]:
    channels: list[_Channel] = []

    for f in feeders:
        consumers = [s for s in stages if f.item_id in s.lane_items]
        channels.append(
            _Channel(
                item_id=f.item_id,
                rate=f.rate,
                source_y=f.lane_y,
                source_block="feeder",
                target_block=_first_consumer_block(consumers),
                consumer_ys=[s.lane_y(f.item_id) for s in consumers],
            )
        )

    for s in stages:
        consumers = [c for c in stages if s.recipe_id in c.lane_items]
        consumer_ys = [c.lane_y(s.recipe_id) for c in consumers]
        if s.recipe_id == target_item:
            consumer_ys.append(sink_lane_y)
        if not consumer_ys:
            continue
        yield_ = recipe_graph.recipes[s.recipe_id].yield_ or 1.0
        channels.append(
            _Channel(
                item_id=s.recipe_id,
                rate=sum(m.crafts_per_sec for m in s.machines) * yield_,
                source_y=s.output_lane_y,
                source_block=f"row-{s.recipe_id}",
                target_block=_first_consumer_block(consumers),
                consumer_ys=consumer_ys,
            )
        )

    for ch in channels:
        if pick_tier(ch.rate).lane_count > 1:
            raise RowLayoutError(
                f"item {ch.item_id} flows at {ch.rate:.1f}/s — beyond one blue belt "
                f"({TIER_CAPACITY[BeltTier.BLUE]:.0f}/s); the row layout is single-lane"
            )
        if ch.consumer_ys != sorted(ch.consumer_ys):
            raise RowLayoutError(
                f"channel {ch.item_id} has a consumer above its producer; "
                "stage topological order is broken"
            )
    return channels


def _first_consumer_block(consumers: list[_Stage]) -> str:
    return f"row-{consumers[0].recipe_id}" if consumers else "sink"


def _pick_within_budget(recipe_id: str, rate: float, budget: int) -> tuple[str, int]:
    """(inserter name, count) meeting `rate` within `budget` face tiles."""
    if budget < 1:
        raise RowLayoutError(
            f"recipe {recipe_id}: no machine-face tile left for a {rate:.2f}/s flow"
        )
    rate *= _INSERTER_HEADROOM
    for name in ("inserter", "fast-inserter", "stack-inserter"):
        n = max(1, math.ceil(rate / INSERTER_THROUGHPUT[name]))
        if n <= budget:
            return name, n
    raise RowLayoutError(
        f"recipe {recipe_id}: {rate:.2f}/s exceeds {budget} stack inserters per machine"
    )


def _feeder_width(feeders: list[_Feeder]) -> int:
    return max((_feeder_plan(f.rate)[1] for f in feeders), default=0)


def _feeder_plan(rate: float) -> tuple[str, int]:
    """(inserter name, count) for a feeder/sink row — width-unconstrained.

    One spare inserter on top of the exact requirement keeps the boundary
    transfer off the critical path in the tick simulator.
    """
    rate *= _INSERTER_HEADROOM
    for name in ("inserter", "fast-inserter", "stack-inserter"):
        n = max(1, math.ceil(rate / INSERTER_THROUGHPUT[name])) + 1
        if n <= 6:
            return name, n
    return (
        "stack-inserter",
        max(1, math.ceil(rate / INSERTER_THROUGHPUT["stack-inserter"])) + 1,
    )


# ---------------------------------------------------------------------------
# Emission: machines + stage inserters
# ---------------------------------------------------------------------------


def _emit_machines(
    stages: list[_Stage],
    block_graph: BlockGraph,
    x0: int,
    canvas: _Canvas,
) -> dict[str, PlacedBlock]:
    machine_pos: dict[str, tuple[int, int]] = {}
    for s in stages:
        w, h = s.footprint
        for i, node in enumerate(s.machines):
            mx = x0 + i * (w + _MACHINE_GAP)
            machine_pos[node.id] = (mx, s.machine_y)
            for dx in range(w):
                for dy in range(h):
                    canvas.claim(mx + dx, s.machine_y + dy, f"machine:{node.id}")

    stage_by_recipe = {s.recipe_id: s for s in stages}
    placed: dict[str, PlacedBlock] = {}
    for block_id in sorted(block_graph.blocks):
        block = block_graph.blocks[block_id]
        tiles = tuple(
            (m.id, *machine_pos[m.id])
            for m in sorted(block.members, key=lambda m: m.id)
            if m.id in machine_pos
        )
        if not tiles:
            continue
        stage = stage_by_recipe[block.primary_recipe]
        w, _h = stage.footprint
        min_x = min(t[1] for t in tiles)
        max_x = max(t[1] for t in tiles) + w
        placed[block_id] = PlacedBlock(
            block_id=block_id,
            x=min_x,
            y=stage.band_y,
            rotated=False,
            width=max_x - min_x,
            height=stage.band_h,
            machine_tiles=tiles,
        )
    return placed


def _emit_stage_inserters(stage: _Stage, x0: int, canvas: _Canvas) -> list[Inserter]:
    w = stage.footprint[0]
    inserters: list[Inserter] = []

    def stamp(x: int, y: int, direction: int, name: str) -> None:
        inserters.append(Inserter(x=x, y=y, direction=direction, name=name))
        canvas.claim(x, y, f"inserter:{stage.recipe_id}")

    plan_by_slot = {p.slot: p for p in stage.lane_plans}
    out_name, out_count = stage.out_plan
    for i in range(len(stage.machines)):
        mx = x0 + i * (w + _MACHINE_GAP)
        cols = list(range(mx, mx + w))
        # Input row: lane A fills columns from the left, lane B from the right.
        plan = plan_by_slot["A"]
        for c in cols[: plan.count]:
            stamp(c, stage.input_inserter_y, DIR_S, plan.inserter)
        if "B" in plan_by_slot:
            plan = plan_by_slot["B"]
            for c in cols[w - plan.count :]:
                stamp(c, stage.input_inserter_y, DIR_S, plan.inserter)
        # Output row: output inserters from the left, lane-C pickers (long-
        # handed, reaching down past the output lane) from the right.
        for c in cols[:out_count]:
            stamp(c, stage.output_inserter_y, DIR_S, out_name)
        if "C" in plan_by_slot:
            plan = plan_by_slot["C"]
            for c in cols[w - plan.count :]:
                stamp(c, stage.output_inserter_y, DIR_N, plan.inserter)
    return inserters


# ---------------------------------------------------------------------------
# Emission: feeder + sink bands, ports
# ---------------------------------------------------------------------------


def _emit_feeder_band(
    feeder: _Feeder, x0: int, canvas: _Canvas
) -> tuple[list[Chest], list[Inserter]]:
    name, count = _feeder_plan(feeder.rate)
    chests: list[Chest] = []
    inserters: list[Inserter] = []
    for x in range(x0, x0 + count):
        chests.append(Chest(x=x, y=feeder.chest_y, name="wooden-chest", item_id=feeder.item_id))
        canvas.claim(x, feeder.chest_y, "chest")
        inserters.append(Inserter(x=x, y=feeder.inserter_y, direction=DIR_S, name=name))
        canvas.claim(x, feeder.inserter_y, "inserter")
    return chests, inserters


def _emit_sink_band(
    plan: tuple[str, int],
    target_item: str,
    x0: int,
    inserter_y: int,
    chest_y: int,
    canvas: _Canvas,
) -> tuple[list[Chest], list[Inserter]]:
    name, count = plan
    chests: list[Chest] = []
    inserters: list[Inserter] = []
    for x in range(x0, x0 + count):
        inserters.append(Inserter(x=x, y=inserter_y, direction=DIR_S, name=name))
        canvas.claim(x, inserter_y, "inserter")
        chests.append(Chest(x=x, y=chest_y, name="wooden-chest", item_id=target_item))
        canvas.claim(x, chest_y, "chest")
    return chests, inserters


def _feeder_port(feeder: _Feeder, stages: list[_Stage], block_graph: BlockGraph) -> IOPort:
    consumer = next(s for s in stages if feeder.item_id in s.lane_items)
    machine = consumer.machines[0]
    return IOPort(
        block_id=block_graph.block_of(machine.id).id,
        machine_id=machine.id,
        item_id=feeder.item_id,
        role="input",
        canvas_edge_xy=(0, feeder.lane_y),
    )


def _sink_port(
    target_item: str,
    stages: list[_Stage],
    block_graph: BlockGraph,
    edge_xy: tuple[int, int],
) -> IOPort:
    producer = next(s for s in stages if s.recipe_id == target_item)
    machine = producer.machines[0]
    return IOPort(
        block_id=block_graph.block_of(machine.id).id,
        machine_id=machine.id,
        item_id=target_item,
        role="output",
        canvas_edge_xy=edge_xy,
    )


# ---------------------------------------------------------------------------
# Emission: channel belts
# ---------------------------------------------------------------------------


def _emit_channel_belt(
    ch: _Channel,
    x0: int,
    x_row_end: int,
    all_cols: set[int],
    canvas: _Canvas,
) -> BeltPath:
    """Emit one continuous belt for `ch` and claim its tiles."""
    tier = pick_tier(ch.rate).tier
    own_cols = {ch.east_col} | ({ch.west_col} if ch.west_col is not None else set())
    hop_cols = all_cols - own_cols

    # Waypoints in flow order: corners of the snake.
    pts: list[tuple[int, int]] = [(x0, ch.source_y), (ch.east_col, ch.source_y)]
    for i, lane_y in enumerate(ch.consumer_ys):
        westward = i % 2 == 0
        col = ch.east_col if westward else ch.west_col
        assert col is not None
        pts.append((col, lane_y))
        last = i == len(ch.consumer_ys) - 1
        if westward:
            pts.append((x0, lane_y) if last else (ch.west_col, lane_y))  # type: ignore[arg-type]
        else:
            pts.append((x_row_end - 1, lane_y) if last else (ch.east_col, lane_y))

    # Expand waypoints to the ordered tile walk.
    tiles: list[tuple[int, int]] = [pts[0]]
    for (ax, ay), (bx, by) in zip(pts, pts[1:], strict=False):
        if (ax, ay) == (bx, by):
            continue
        if ax == bx:
            step = 1 if by > ay else -1
            tiles.extend((ax, t) for t in range(ay + step, by + step, step))
        else:
            step = 1 if bx > ax else -1
            tiles.extend((t, ay) for t in range(ax + step, bx + step, step))

    # Classify: tiles on foreign corridor columns are skipped (the belt
    # dives underneath); their horizontal neighbours become the entry/exit.
    seq: list[list] = []  # [x, y, kind]
    skipped = False
    for x, y in tiles:
        if x in hop_cols:
            seq[-1][2] = "in"
            skipped = True
            continue
        seq.append([x, y, "out" if skipped else "surface"])
        skipped = False

    segments: list[BeltSegment] = []
    undergrounds: list[UndergroundBelt] = []
    direction = DIR_E
    for i, (x, y, kind) in enumerate(seq):
        if i + 1 < len(seq):
            nx, ny, _ = seq[i + 1][0], seq[i + 1][1], None
            if nx > x:
                direction = DIR_E
            elif nx < x:
                direction = DIR_W
            elif ny > y:
                direction = DIR_S
            elif ny < y:
                direction = DIR_N
        if kind == "surface":
            canvas.claim(x, y, f"belt:{ch.item_id}")
            segments.append(BeltSegment(x=x, y=y, direction=direction, name=str(tier)))
        else:
            canvas.claim(x, y, f"ug:{ch.item_id}")
            undergrounds.append(
                UndergroundBelt(
                    x=x,
                    y=y,
                    direction=direction,
                    name=_UG_NAME[tier],
                    io_type="input" if kind == "in" else "output",
                )
            )

    return BeltPath(
        item_id=ch.item_id,
        source_block=ch.source_block,
        target_block=ch.target_block,
        tier=tier,
        lane_count=1,
        segments=tuple(segments),
        undergrounds=tuple(undergrounds),
    )


# ---------------------------------------------------------------------------
# Emission: poles
# ---------------------------------------------------------------------------


def _emit_poles(
    stages: list[_Stage],
    feeders: list[_Feeder],
    x0: int,
    sink_count: int,
    sink_pole_y: int,
    canvas: _Canvas,
) -> list[Pole]:
    poles: list[Pole] = []

    def stamp(x: int, y: int) -> None:
        poles.append(Pole(x=x, y=y, name="medium-electric-pole"))
        canvas.claim(x, y, "pole")

    # Stage bands: a pole in the gap column east of every machine, on both
    # inserter rows. Gap columns host nothing else, so this is collision-free
    # and keeps every machine + inserter within Chebyshev 3 of a pole.
    for s in stages:
        w = s.footprint[0]
        for i in range(len(s.machines)):
            gap_x = x0 + i * (w + _MACHINE_GAP) + w
            stamp(gap_x, s.input_inserter_y)
            stamp(gap_x, s.output_inserter_y)

    # Feeder bands: poles along the separator row, pitched to cover the
    # chest + inserter columns above.
    for f in feeders:
        count = _feeder_plan(f.rate)[1]
        for x in range(x0, x0 + count, _POLE_COVER_PITCH):
            stamp(min(x + 3, x0 + count), f.separator_y)

    # Sink band: poles one row below the chests.
    for x in range(x0, x0 + sink_count, _POLE_COVER_PITCH):
        stamp(min(x + 3, x0 + sink_count), sink_pole_y)

    return poles


__all__ = [
    "RowLayoutError",
    "RowLayoutResult",
    "build_row_layout",
]
