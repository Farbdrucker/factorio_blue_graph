"""Discrete-tick simulator.

Tick order:
1. Machines step (consume inputs that landed last tick, advance progress,
   emit outputs into their output slots).
2. Belt paths step (advance items along their delay buffer).
3. Inserters step (pickup from machine outputs / source chests / belt tails,
   drop into machine inputs / sink chests / belt heads). The inserter state
   is "busy after a successful pickup until ``swing_ticks`` elapse, then
   deposit"; this is a faithful enough proxy for steady-state throughput.

Deterministic: all entity iteration goes through stable, sorted lists built
once at construction time. No ``set`` iteration in the hot loop.
"""

from __future__ import annotations

from collections.abc import Iterable

from factorio_blue_graph.simulate.report import Bottleneck, SimReport
from factorio_blue_graph.simulate.runtime import (
    BeltPathRuntime,
    ChestRuntime,
    InserterRuntime,
    MachineRuntime,
)
from factorio_blue_graph.simulate.topology import Topology, find_all_sinks
from factorio_blue_graph.verify.report import Severity

# Inserter state codes (kept here to avoid importing from runtime — keeps the
# runtime module free of policy).
_IDLE = 0
_BUSY = 1


class Simulator:
    def __init__(
        self,
        topology: Topology,
        *,
        target_item: str,
        target_rate_per_sec: float,
    ) -> None:
        self.topo = topology
        self.target_item = target_item
        self.target_rate_per_sec = target_rate_per_sec
        self.tick = 0
        self._sinks = find_all_sinks(topology, target_item)
        # Pre-resolved adjacencies the tick loop needs.
        self._inserters = list(topology.inserters)
        self._machines = sorted(topology.machines.values(), key=lambda m: m.machine_id)
        self._belt_paths = list(topology.belt_paths)
        # Per-inserter neighbour resolution table built once.
        self._inserter_pickup_sink: list[_Endpoint | None] = [
            _resolve_endpoint(topology, ins.pickup_tile, pickup=True) for ins in self._inserters
        ]
        self._inserter_drop_sink: list[_Endpoint | None] = [
            _resolve_endpoint(topology, ins.drop_tile, pickup=False) for ins in self._inserters
        ]

    def run(self, warmup_ticks: int, measure_ticks: int) -> SimReport:
        for _ in range(warmup_ticks):
            self._step()
        sink_before = self._total_target_in_sinks()
        for _ in range(measure_ticks):
            self._step()
        sink_after = self._total_target_in_sinks()
        delivered = sink_after - sink_before
        achieved = delivered / max(1, measure_ticks) * 60.0  # items/sec
        report = SimReport(
            target_rate_per_sec=self.target_rate_per_sec,
            achieved_rate_per_sec=achieved,
            warmup_ticks=warmup_ticks,
            measure_ticks=measure_ticks,
            total_crafts_by_recipe=self._crafts_by_recipe(),
        )
        report.bottlenecks = list(self._attribute_bottlenecks(measure_ticks, achieved))
        return report

    # ------------------------------------------------------------------ tick

    def _step(self) -> None:
        for m in self._machines:
            m.step()
        for b in self._belt_paths:
            b.step(self.tick)
        # Rotate the starting inserter each tick. With a fixed order the
        # first inserters drain a scarce belt before later ones ever see an
        # item; on a real belt items flow past busy inserters, so access to
        # a shared lane is roughly fair.
        n = len(self._inserters)
        start = self.tick % n if n else 0
        for i in range(n):
            idx = (start + i) % n
            self._step_inserter(idx, self._inserters[idx])
        self.tick += 1

    def _step_inserter(self, idx: int, ins: InserterRuntime) -> None:
        if ins.state == _BUSY:
            ins.remaining_ticks -= 1
            ins.active_ticks += 1
            if ins.remaining_ticks <= 0:
                drop = self._inserter_drop_sink[idx]
                deposited = _deposit(drop, ins.held_item, ins.held_count, self.tick)
                if deposited == 0:
                    ins.blocked_drop_ticks += 1
                    # Hold position; retry next tick.
                    ins.remaining_ticks = 1
                    return
                ins.held_item = None
                ins.held_count = 0
                ins.state = _IDLE
            return
        # IDLE: try to pick up
        pickup = self._inserter_pickup_sink[idx]
        item, count = _withdraw(pickup, ins.items_per_swing)
        if item is None:
            ins.blocked_pickup_ticks += 1
            return
        ins.held_item = item
        ins.held_count = count
        ins.state = _BUSY
        ins.remaining_ticks = ins.swing_ticks
        ins.active_ticks += 1

    # -------------------------------------------------------------- metrics

    def _total_target_in_sinks(self) -> int:
        total = 0
        for sink in self._sinks:
            total += sink.inventory.get(self.target_item, 0)
        return total

    def _crafts_by_recipe(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for m in self._machines:
            out[m.recipe_id] = out.get(m.recipe_id, 0) + m.total_crafts
        return out

    def _attribute_bottlenecks(self, measure_ticks: int, achieved: float) -> Iterable[Bottleneck]:
        from factorio_blue_graph.simulate.constants import ACHIEVEMENT_THRESHOLD

        ratio = achieved / self.target_rate_per_sec if self.target_rate_per_sec > 0 else 1.0
        if ratio >= ACHIEVEMENT_THRESHOLD:
            return

        # OUTPUT_UNDERDELIVERY first — always emitted at under-target.
        yield Bottleneck(
            code="OUTPUT_UNDERDELIVERY",
            severity=Severity.ERROR,
            message=(
                f"target {self.target_rate_per_sec * 60:.1f}/min, "
                f"achieved {achieved * 60:.1f}/min ({ratio * 100:.0f}%)"
            ),
            payload={
                "target_rate_per_sec": self.target_rate_per_sec,
                "achieved_rate_per_sec": achieved,
                "ratio": ratio,
            },
        )

        # Disconnected machines: never received any input during the run.
        for m in self._machines:
            if not m.inputs_per_craft:
                continue
            missing = sorted(
                item
                for item in m.inputs_per_craft
                if m.input_slots.get(item, 0) == 0 and m.total_crafts == 0
            )
            if missing:
                yield Bottleneck(
                    code="DISCONNECTED_FLOW",
                    severity=Severity.ERROR,
                    message=f"machine {m.machine_id} never received {', '.join(missing)}",
                    location=m.tile,
                    payload={
                        "block_id": m.block_id,
                        "machine_id": m.machine_id,
                        "missing_items": missing,
                    },
                )
                continue
            starv_ratio = m.starvation_ticks / max(1, measure_ticks)
            if starv_ratio > 0.20:
                yield Bottleneck(
                    code="MACHINE_STARVED",
                    severity=Severity.ERROR,
                    message=(f"machine {m.machine_id} starved {starv_ratio * 100:.0f}% of ticks"),
                    location=m.tile,
                    payload={
                        "block_id": m.block_id,
                        "machine_id": m.machine_id,
                        "starvation_ratio": starv_ratio,
                    },
                )

        # Belt saturation.
        for b in self._belt_paths:
            sat = b.saturation_ratio()
            if sat > 0.95:
                yield Bottleneck(
                    code="BELT_SATURATED",
                    severity=Severity.WARNING,
                    message=(
                        f"belt {b.source_block}→{b.target_block} ({b.item_id}) "
                        f"rejected {sat * 100:.0f}% of offers"
                    ),
                    payload={
                        "path_id": b.path_id,
                        "source_block": b.source_block,
                        "target_block": b.target_block,
                        "item_id": b.item_id,
                        "tier": str(b.tier),
                        "saturation": sat,
                    },
                )

        # Inserter bottleneck — busy > 90% of ticks and downstream blocked.
        for ins in self._inserters:
            total = max(1, measure_ticks)
            active = ins.active_ticks / total
            if active > 0.90 and ins.blocked_drop_ticks / total > 0.10:
                yield Bottleneck(
                    code="INSERTER_BOTTLENECK",
                    severity=Severity.WARNING,
                    message=(
                        f"inserter at {ins.tile} active {active * 100:.0f}% with "
                        f"{ins.blocked_drop_ticks / total * 100:.0f}% drop-blocked"
                    ),
                    location=ins.tile,
                    payload={"name": ins.name, "active_ratio": active},
                )


# --------------------------------------------------------------------- helpers


class _Endpoint:
    __slots__ = ("kind", "ref", "belt")

    def __init__(
        self,
        kind: str,
        ref: object | None = None,
        belt: BeltPathRuntime | None = None,
    ) -> None:
        self.kind = kind
        self.ref = ref
        self.belt = belt


def _resolve_endpoint(topo: Topology, tile: tuple[int, int], *, pickup: bool) -> _Endpoint | None:
    machine = topo.machine_by_tile.get(tile)
    if machine is not None:
        return _Endpoint(kind="machine", ref=machine)
    chest = topo.chests.get(tile)
    if chest is not None:
        return _Endpoint(kind="chest", ref=chest)
    belt = topo.belt_by_tile.get(tile)
    if belt is not None:
        return _Endpoint(kind="belt", belt=belt)
    return None


def _withdraw(endpoint: _Endpoint | None, count: int) -> tuple[str | None, int]:
    if endpoint is None:
        return (None, 0)
    if endpoint.kind == "machine":
        m: MachineRuntime = endpoint.ref  # type: ignore[assignment]
        item = m.output_item
        taken = m.take_output(item, count)
        return (item if taken > 0 else None, taken)
    if endpoint.kind == "chest":
        c: ChestRuntime = endpoint.ref  # type: ignore[assignment]
        item = c.item_filter
        if item is None:
            return (None, 0)
        taken = c.take(item, count)
        return (item if taken > 0 else None, taken)
    if endpoint.kind == "belt" and endpoint.belt is not None:
        taken = 0
        item = None
        for _ in range(count):
            popped = endpoint.belt.pop()
            if popped is None:
                break
            item = popped
            taken += 1
        return (item, taken)
    return (None, 0)


def _deposit(endpoint: _Endpoint | None, item: str | None, count: int, tick: int) -> int:
    if endpoint is None or item is None:
        return 0
    if endpoint.kind == "machine":
        m: MachineRuntime = endpoint.ref  # type: ignore[assignment]
        return m.take_input(item, count)
    if endpoint.kind == "chest":
        c: ChestRuntime = endpoint.ref  # type: ignore[assignment]
        return c.give(item, count)
    if endpoint.kind == "belt" and endpoint.belt is not None:
        accepted = 0
        for _ in range(count):
            if not endpoint.belt.offer(tick, item):
                break
            accepted += 1
        return accepted
    return 0


__all__ = ["Simulator"]
