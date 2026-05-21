"""Phase 7b: power-pole coverage via greedy set cover.

Given the placed machines, the belts already laid by Phase 6, and the
inserters from Phase 7a, choose a minimum number of medium electric
poles such that every machine sits inside at least one pole's 7×7
supply area. The greedy heuristic picks the candidate tile that covers
the most as-yet-uncovered machines, ties broken lexicographically by
`(x, y)` for determinism.

A medium pole has supply area half-extent `POLE_RADIUS = 3` (centred
on its tile), so a machine with top-left `(mx, my)` and footprint
`(mw, mh)` is covered iff `[px-3..px+3]` overlaps `[mx..mx+mw-1]` and
the same holds on the y axis.

v1 limitations:
- We do NOT enforce wire connectivity between poles (medium poles have
  a 9-tile wire reach in vanilla 1.1). A coverage-minimal pole set may
  fragment into disconnected sub-graphs — deferred to a future "trunk
  pole" pass.
- Inserters are not part of the coverage universe; in practice they
  sit immediately next to assemblers so they are always covered as a
  side effect, but they are not asserted.
"""

from __future__ import annotations

from dataclasses import dataclass

from factorio_blue_graph.layout.placement import Placement
from factorio_blue_graph.model.blueprint import POLE_RADIUS, Pole
from factorio_blue_graph.model.graph import BlockGraph

# Footprint assumed for each machine when computing coverage. Matches the
# vanilla 1.1 assembler / chemical-plant size; the v1 dataset uses 3×3
# uniformly for production buildings.
_MACHINE_FOOTPRINT = (3, 3)


@dataclass(frozen=True)
class PoleResult:
    poles: tuple[Pole, ...]
    uncovered_machines: tuple[str, ...]


def cover_with_poles(
    placement: Placement,
    block_graph: BlockGraph,
    occupied_tiles: frozenset[tuple[int, int]],
) -> PoleResult:
    """Greedy minimum cover of every placed machine by medium poles.

    `occupied_tiles` should include every belt segment, underground belt,
    splitter tile (both halves), inserter tile, and machine footprint
    tile. Pole candidates are drawn from canvas tiles not in this set.
    """
    machines = _collect_machines(placement, block_graph)
    if not machines:
        return PoleResult(poles=(), uncovered_machines=())

    W, H = placement.canvas
    # Candidate poles: every canvas tile not occupied, considered in
    # lexicographic order so ties resolve deterministically.
    candidates = [(x, y) for y in range(H) for x in range(W) if (x, y) not in occupied_tiles]
    # Precompute the machine set each candidate would cover so the greedy
    # loop is O(iterations · |candidates|) instead of O(|candidates| ·
    # |machines|) per iteration.
    covers: dict[tuple[int, int], set[str]] = {}
    for tile in candidates:
        covers[tile] = _machines_in_coverage(tile, machines)

    remaining = {mid for mid, *_ in machines}
    poles: list[Pole] = []
    while remaining:
        best: tuple[int, int] | None = None
        best_score = 0
        for tile in candidates:
            score = len(covers[tile] & remaining)
            if score > best_score:
                best_score = score
                best = tile
        if best is None or best_score == 0:
            break
        poles.append(Pole(x=best[0], y=best[1], name="medium-electric-pole"))
        remaining -= covers[best]

    return PoleResult(
        poles=tuple(poles),
        uncovered_machines=tuple(sorted(remaining)),
    )


def _collect_machines(
    placement: Placement, block_graph: BlockGraph
) -> list[tuple[str, int, int, int, int]]:
    """Return [(machine_id, mx, my, mw, mh), ...] from every placed block."""
    out: list[tuple[str, int, int, int, int]] = []
    mw, mh = _MACHINE_FOOTPRINT
    for pb in placement.blocks.values():
        block = block_graph.blocks.get(pb.block_id)
        # Per-machine footprint may differ in mixed blocks; default to
        # the block's largest member footprint to stay safe.
        if block is not None and block.members:
            mw = max(m.machine.footprint[0] for m in block.members)
            mh = max(m.machine.footprint[1] for m in block.members)
        for mid, mx, my in pb.machine_tiles:
            out.append((mid, mx, my, mw, mh))
    return out


def _machines_in_coverage(
    tile: tuple[int, int],
    machines: list[tuple[str, int, int, int, int]],
) -> set[str]:
    """Set of machine ids whose footprint overlaps the 7×7 box at `tile`."""
    px, py = tile
    lo_x, hi_x = px - POLE_RADIUS, px + POLE_RADIUS
    lo_y, hi_y = py - POLE_RADIUS, py + POLE_RADIUS
    out: set[str] = set()
    for mid, mx, my, mw, mh in machines:
        if mx + mw - 1 < lo_x or mx > hi_x:
            continue
        if my + mh - 1 < lo_y or my > hi_y:
            continue
        out.add(mid)
    return out


__all__ = [
    "PoleResult",
    "cover_with_poles",
]
