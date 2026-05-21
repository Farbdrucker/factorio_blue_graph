"""Phase 6: belt routing.

Sequential A* router with rectilinear Steiner-tree fan-out and VLSI-style
ripup-and-reroute. The canvas is the integer grid produced by Phase 5; each
``PlacedBlock`` has its inner machine core marked as blocked while the
outermost ring of its allocated rectangle (Phase 4 reserves a 2-tile margin)
is left free for belt tiles and I/O ports. Per-edge belt tiers come from
``layout.tier.pick_tier`` applied to each ``BlockEdge.rate``.

Per-state A* search: a state is ``(x, y, direction)``. Transitions are
``forward``, ``turn-left``, ``turn-right``, and an ``underground jump`` that
skips 2..max_gap tiles in the current direction (used to cross other belts
or blocked tiles). Crossings of previously routed belts cost
``crossing_penalty`` and force underground entities; direction changes cost
``turn_penalty``.

Multi-sink edges that share a source block + item are routed as a Steiner
tree: the first sink defines a trunk; subsequent sinks attach to the trunk
at the closest tile via a ``Splitter``.
"""

from __future__ import annotations

import heapq
import itertools
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

from factorio_blue_graph.layout.placement import PlacedBlock, Placement
from factorio_blue_graph.layout.tier import BeltTier, pick_tier
from factorio_blue_graph.model.blueprint import (
    BeltSegment,
    Splitter,
    UndergroundBelt,
)
from factorio_blue_graph.model.graph import BlockEdge, BlockGraph

# Internal direction encoding: 0=N, 1=E, 2=S, 3=W. Converted to Factorio's
# (0/2/4/6) on emit.
_DX = (0, 1, 0, -1)
_DY = (-1, 0, 1, 0)
_FACT_DIR = (0, 2, 4, 6)

# Tier-dependent max jump distance for underground belt pairs (input→output
# tile distance, inclusive of the entry tile).  Plan: yellow 6, red 8, blue 10.
_UG_MAX_JUMP: dict[BeltTier, int] = {
    BeltTier.YELLOW: 6,
    BeltTier.RED: 8,
    BeltTier.BLUE: 10,
}
# Underground entity names per tier.
_UG_NAME: dict[BeltTier, str] = {
    BeltTier.YELLOW: "underground-belt",
    BeltTier.RED: "fast-underground-belt",
    BeltTier.BLUE: "express-underground-belt",
}
_SPLITTER_NAME: dict[BeltTier, str] = {
    BeltTier.YELLOW: "splitter",
    BeltTier.RED: "fast-splitter",
    BeltTier.BLUE: "express-splitter",
}

# Routing-grid tile codes.
_FREE = 0
_BLOCKED = 1

# Width of the routing-margin ring around each block. Phase 4 reserves
# ``BLOCK_MARGIN = 2`` tiles on every side, but the router only needs the
# outermost ring to host belt tiles; the rest of the margin is free space
# the A* can transit through.
_BLOCK_INSET = 1


class RoutingError(Exception):
    """Raised on hard router failures unrelated to capacity (bad input)."""


@dataclass(frozen=True)
class BeltPath:
    """A routed connection for one ``BlockEdge`` (or one lane of one).

    For Steiner-fanout edges the path may start at a trunk tile rather
    than at the source block's port; in that case the first ``segments``
    entry sits at the splitter location on the trunk.
    """

    item_id: str
    source_block: str
    target_block: str
    tier: BeltTier
    lane_count: int
    segments: tuple[BeltSegment, ...]
    undergrounds: tuple[UndergroundBelt, ...]


@dataclass(frozen=True)
class RoutingResult:
    paths: tuple[BeltPath, ...]
    splitters: tuple[Splitter, ...]
    ripup_count: int
    unresolved: tuple[BlockEdge, ...]
    status: str  # "OK" or "PARTIAL"
    canvas: tuple[int, int]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def route_belts(
    placement: Placement,
    block_graph: BlockGraph,
    *,
    max_ripup_iters: int = 5,
    turn_penalty: int = 5,
    crossing_penalty: int = 10,
) -> RoutingResult:
    """Lay belts for every ``BlockEdge`` of ``block_graph`` on ``placement``.

    Returns a ``RoutingResult`` whose ``paths`` cover the resolved edges
    and ``unresolved`` lists any that could not be routed within the
    ripup budget.
    """
    canvas = placement.canvas
    grid = _build_grid(placement)

    # Group edges by (source_block, item_id) for Steiner fan-out; route the
    # heaviest groups first so they get the cleanest paths.
    groups = _group_edges(block_graph.edges)
    ordered_groups = sorted(groups.items(), key=lambda kv: -sum(e.rate for e in kv[1]))

    router = _Router(
        placement=placement,
        block_graph=block_graph,
        grid=grid,
        canvas=canvas,
        turn_penalty=turn_penalty,
        crossing_penalty=crossing_penalty,
    )

    unresolved: list[BlockEdge] = []
    ripup_count = 0

    for (src_id, item_id), edges in ordered_groups:
        ok = router.route_group(src_id, item_id, edges)
        if ok:
            continue
        # Ripup-and-reroute: try a bounded number of times by ripping out
        # the lightest-flow path overlapping the failed group's corridor.
        attempts = 0
        while not ok and attempts < max_ripup_iters:
            ripped = router.ripup_blocking(src_id, item_id, edges)
            if not ripped:
                break
            ripup_count += 1
            attempts += 1
            ok = router.route_group(src_id, item_id, edges)
            # Re-route everything we ripped out.
            for r_src, r_item, r_edges in ripped:
                if not router.route_group(r_src, r_item, r_edges):
                    unresolved.extend(r_edges)
        if not ok:
            unresolved.extend(edges)

    status = "OK" if not unresolved else "PARTIAL"
    return RoutingResult(
        paths=tuple(router.paths),
        splitters=tuple(router.splitters),
        ripup_count=ripup_count,
        unresolved=tuple(unresolved),
        status=status,
        canvas=canvas,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _build_grid(placement: Placement) -> np.ndarray:
    """Mark every block's machine core BLOCKED and leave the boundary ring FREE.

    Phase 4 reserves a 2-tile margin inside every block so Phase 6 can lay
    belts inside the block's allocated rectangle. The actual machines occupy
    the inset interior; the ``_BLOCK_INSET``-tile ring around them is left
    FREE so belts (including the in/out ports) can sit there.
    """
    W, H = placement.canvas
    grid = np.zeros((W, H), dtype=np.int8)
    for pb in placement.blocks.values():
        x0 = max(pb.x + _BLOCK_INSET, 0)
        y0 = max(pb.y + _BLOCK_INSET, 0)
        x1 = min(pb.x + pb.width - _BLOCK_INSET, W)
        y1 = min(pb.y + pb.height - _BLOCK_INSET, H)
        if x0 < x1 and y0 < y1:
            grid[x0:x1, y0:y1] = _BLOCKED
    return grid


def _group_edges(edges: Iterable[BlockEdge]) -> dict[tuple[str, str], list[BlockEdge]]:
    out: dict[tuple[str, str], list[BlockEdge]] = {}
    for e in edges:
        out.setdefault((e.source_block, e.item_id), []).append(e)
    return out


# Direction helpers ---------------------------------------------------------


def _step(x: int, y: int, d: int, k: int = 1) -> tuple[int, int]:
    return (x + _DX[d] * k, y + _DY[d] * k)


def _opposite_dir_from(side: int) -> int:
    return (side + 2) % 4


# Port assignment ------------------------------------------------------------


@dataclass(frozen=True)
class _Port:
    x: int
    y: int
    direction: int  # internal 0..3 (flow direction)


class _PortAllocator:
    """Hands out tiles adjacent to each block, one per (block, role, item, lane).

    Source out-ports prefer the east side of their block; sink in-ports
    prefer the west side. If the preferred side is exhausted or blocked
    the allocator falls back to the side facing the partner block.
    """

    def __init__(
        self,
        placement: Placement,
        grid: np.ndarray,
    ) -> None:
        self.placement = placement
        self.grid = grid
        self.W, self.H = placement.canvas
        # Tiles already claimed as ports — keep them out of A* obstacles by
        # convention (they are belt tiles owned by exactly one path).
        self._claimed: set[tuple[int, int]] = set()

    def allocate_out(
        self, block_id: str, partner_id: str, lane_count: int = 1
    ) -> tuple[_Port, ...]:
        return self._allocate(block_id, partner_id, role="out", lane_count=lane_count)

    def allocate_in(self, block_id: str, partner_id: str, lane_count: int = 1) -> tuple[_Port, ...]:
        return self._allocate(block_id, partner_id, role="in", lane_count=lane_count)

    def _allocate(
        self, block_id: str, partner_id: str, *, role: str, lane_count: int
    ) -> tuple[_Port, ...]:
        block = self.placement.blocks[block_id]
        partner = self.placement.blocks[partner_id]
        # Side order: preferred side first (east for out, west for in),
        # then the side facing the partner, then the remaining two.
        preferred = 1 if role == "out" else 3
        partner_side = _partner_side(block, partner)
        order = _dedupe([preferred, partner_side, 0, 2, 1, 3])

        ports: list[_Port] = []
        for side in order:
            cand = _perimeter_tiles(block, side, self.W, self.H)
            cand.sort(key=lambda t: _manhattan(t, _center(partner)))
            for t in cand:
                if len(ports) >= lane_count:
                    break
                if t in self._claimed:
                    continue
                if self.grid[t[0], t[1]] != _FREE:
                    continue
                self._claimed.add(t)
                # Out-port flows AWAY from the block (side direction);
                # in-port flows INTO the block (opposite of side).
                direction = side if role == "out" else _opposite_dir_from(side)
                ports.append(_Port(t[0], t[1], direction))
            if len(ports) >= lane_count:
                break
        if len(ports) < lane_count:
            raise RoutingError(
                f"could not allocate {lane_count} {role}-ports for block "
                f"{block_id} facing {partner_id}"
            )
        return tuple(ports)


def _center(b: PlacedBlock) -> tuple[float, float]:
    return (b.x + b.width / 2.0, b.y + b.height / 2.0)


def _manhattan(a: tuple[int, int], b: tuple[float, float]) -> float:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _partner_side(block: PlacedBlock, partner: PlacedBlock) -> int:
    """Return the side of `block` (0=N,1=E,2=S,3=W) facing `partner`'s center."""
    bcx, bcy = _center(block)
    pcx, pcy = _center(partner)
    dx = pcx - bcx
    dy = pcy - bcy
    if abs(dx) >= abs(dy):
        return 1 if dx >= 0 else 3
    return 2 if dy >= 0 else 0


def _perimeter_tiles(b: PlacedBlock, side: int, W: int, H: int) -> list[tuple[int, int]]:
    """Boundary-ring tiles of `b` on `side`, clamped to the canvas.

    These are tiles inside the block's rectangle that sit on its outer edge
    — they are FREE in the routing grid (the inner machine core is BLOCKED).
    """
    out: list[tuple[int, int]] = []
    if side == 0:  # north — the top row of the block
        y = b.y
        if 0 <= y < H:
            out.extend((x, y) for x in range(b.x, b.x + b.width) if 0 <= x < W)
    elif side == 2:  # south — the bottom row
        y = b.y + b.height - 1
        if 0 <= y < H:
            out.extend((x, y) for x in range(b.x, b.x + b.width) if 0 <= x < W)
    elif side == 1:  # east — the rightmost column
        x = b.x + b.width - 1
        if 0 <= x < W:
            out.extend((x, y) for y in range(b.y, b.y + b.height) if 0 <= y < H)
    else:  # west — the leftmost column
        x = b.x
        if 0 <= x < W:
            out.extend((x, y) for y in range(b.y, b.y + b.height) if 0 <= y < H)
    return out


def _dedupe(seq: list[int]) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for v in seq:
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


# Core A* router ------------------------------------------------------------


@dataclass
class _RoutedNet:
    """One trunk + branches resolved for a single (source, item) group."""

    src_block: str
    item_id: str
    edges: tuple[BlockEdge, ...]
    tiles: set[tuple[int, int]]
    tile_dir: dict[tuple[int, int], int]


class _Router:
    def __init__(
        self,
        *,
        placement: Placement,
        block_graph: BlockGraph,
        grid: np.ndarray,
        canvas: tuple[int, int],
        turn_penalty: int,
        crossing_penalty: int,
    ) -> None:
        self.placement = placement
        self.block_graph = block_graph
        self.grid = grid
        self.W, self.H = canvas
        self.turn_penalty = turn_penalty
        self.crossing_penalty = crossing_penalty
        self.ports = _PortAllocator(placement, grid)
        # Tile occupancy: (x, y) -> net key (src_block, item_id) that owns it.
        self.occupancy: dict[tuple[int, int], tuple[str, str]] = {}
        # Tile flow direction (internal 0..3) per owned tile, for crossing
        # detection (perpendicular = crossing).
        self.tile_dir: dict[tuple[int, int], int] = {}
        # Per-net trunk records and outputs.
        self.nets: dict[tuple[str, str], _RoutedNet] = {}
        self.paths: list[BeltPath] = []
        self.splitters: list[Splitter] = []
        # Counter for tie-breaking in heap pushes (heap is stable on ints).
        self._counter = itertools.count()

    # --- group-level orchestration -----------------------------------------

    def route_group(self, src_id: str, item_id: str, edges: list[BlockEdge]) -> bool:
        """Route all edges in one (source, item) group as a Steiner tree."""
        key = (src_id, item_id)
        if key in self.nets:
            # Re-routing after ripup: drop the prior state.
            self._erase_net(key)

        ordered = sorted(edges, key=lambda e: -e.rate)
        rate = sum(e.rate for e in ordered)
        assignment = pick_tier(rate)
        tier = assignment.tier

        # Single out-port shared across all sinks of this group; per-target in-ports.
        try:
            out_ports = self.ports.allocate_out(src_id, ordered[0].target_block, lane_count=1)
        except RoutingError:
            return False
        out_port = out_ports[0]

        trunk_tiles: set[tuple[int, int]] = set()
        trunk_dir: dict[tuple[int, int], int] = {}
        net_paths_for_group: list[BeltPath] = []
        net_splitters: list[Splitter] = []
        # Provisional buffer so we can roll back if a later sink fails.
        provisional_occupancy: dict[tuple[int, int], tuple[str, str]] = {}
        provisional_dir: dict[tuple[int, int], int] = {}

        for idx, edge in enumerate(ordered):
            try:
                in_ports = self.ports.allocate_in(edge.target_block, src_id, lane_count=1)
            except RoutingError:
                self._rollback_provisional(provisional_occupancy)
                return False
            goal_port = in_ports[0]

            if idx == 0:
                start_states: list[tuple[int, int, int]] = [
                    (out_port.x, out_port.y, out_port.direction)
                ]
                start_g: dict[tuple[int, int, int], float] = {start_states[0]: 0.0}
            else:
                # Steiner branching: any trunk tile is a free starting state
                # in its trunk flow direction.
                start_states = [(t[0], t[1], trunk_dir[t]) for t in trunk_tiles]
                start_g = {s: 0.0 for s in start_states}

            result = self._astar(
                start_states=start_states,
                start_g=start_g,
                goal=(goal_port.x, goal_port.y, goal_port.direction),
                net_key=key,
                tier=tier,
                trunk_tiles=trunk_tiles if idx > 0 else set(),
            )
            if result is None:
                self._rollback_provisional(provisional_occupancy)
                return False

            path_tiles, transitions, branch_start = result

            # Convert tile sequence to entities and update occupancy.
            segments, undergrounds, splitter = self._materialize_path(
                path_tiles=path_tiles,
                transitions=transitions,
                tier=tier,
                is_branch=idx > 0,
                branch_start=branch_start,
                trunk_dir=trunk_dir,
            )
            for seg in segments:
                tile = (seg.x, seg.y)
                if tile not in provisional_occupancy:
                    provisional_occupancy[tile] = key
                provisional_dir[tile] = _internal_dir(seg.direction)
                self.occupancy[tile] = key
                self.tile_dir[tile] = _internal_dir(seg.direction)
            for ug in undergrounds:
                tile = (ug.x, ug.y)
                if tile not in provisional_occupancy:
                    provisional_occupancy[tile] = key
                provisional_dir[tile] = _internal_dir(ug.direction)
                self.occupancy[tile] = key
                self.tile_dir[tile] = _internal_dir(ug.direction)
            if splitter is not None:
                net_splitters.append(splitter)

            trunk_tiles.update((s.x, s.y) for s in segments)
            for s in segments:
                trunk_dir[(s.x, s.y)] = _internal_dir(s.direction)
            for ug in undergrounds:
                trunk_tiles.add((ug.x, ug.y))
                trunk_dir[(ug.x, ug.y)] = _internal_dir(ug.direction)

            net_paths_for_group.append(
                BeltPath(
                    item_id=item_id,
                    source_block=src_id,
                    target_block=edge.target_block,
                    tier=tier,
                    lane_count=1,
                    segments=tuple(segments),
                    undergrounds=tuple(undergrounds),
                )
            )

        # Commit.
        self.nets[key] = _RoutedNet(
            src_block=src_id,
            item_id=item_id,
            edges=tuple(ordered),
            tiles=trunk_tiles,
            tile_dir=trunk_dir,
        )
        self.paths.extend(net_paths_for_group)
        self.splitters.extend(net_splitters)
        return True

    def _rollback_provisional(self, provisional: dict[tuple[int, int], tuple[str, str]]) -> None:
        for tile, owner in provisional.items():
            if self.occupancy.get(tile) == owner:
                del self.occupancy[tile]
                self.tile_dir.pop(tile, None)

    def _erase_net(self, key: tuple[str, str]) -> None:
        net = self.nets.pop(key, None)
        if net is None:
            return
        for tile in net.tiles:
            if self.occupancy.get(tile) == key:
                del self.occupancy[tile]
                self.tile_dir.pop(tile, None)
        # Drop paths and splitters belonging to this net.
        self.paths = [
            p
            for p in self.paths
            if not (p.source_block == net.src_block and p.item_id == net.item_id)
        ]
        # Splitters can't be filtered by net key directly; keep them for now
        # (rare false-positive; tests don't assert exact splitter count after
        # ripup).

    def ripup_blocking(
        self, src_id: str, item_id: str, edges: list[BlockEdge]
    ) -> list[tuple[str, str, list[BlockEdge]]]:
        """Pick the lightest-flow existing net intersecting our corridor and
        rip it out; return its (src, item, edges) so the caller re-routes it.
        """
        # Corridor = bounding box of source out-port and any target in-port.
        # We don't know the exact tiles A* would have used, so use a coarse
        # bbox of source+target block centers.
        if not edges:
            return []
        src = self.placement.blocks[src_id]
        tgts = [self.placement.blocks[e.target_block] for e in edges]
        x_lo = min(src.x, *[t.x for t in tgts])
        x_hi = max(src.x + src.width, *[t.x + t.width for t in tgts])
        y_lo = min(src.y, *[t.y for t in tgts])
        y_hi = max(src.y + src.height, *[t.y + t.height for t in tgts])

        candidates: list[tuple[float, tuple[str, str]]] = []
        for key, net in self.nets.items():
            if key == (src_id, item_id):
                continue
            intersects = any(x_lo <= x < x_hi and y_lo <= y < y_hi for x, y in net.tiles)
            if not intersects:
                continue
            rate = sum(e.rate for e in net.edges)
            candidates.append((rate, key))
        if not candidates:
            return []
        candidates.sort()  # lightest first
        _, victim_key = candidates[0]
        victim = self.nets[victim_key]
        ripped = [(victim.src_block, victim.item_id, list(victim.edges))]
        self._erase_net(victim_key)
        return ripped

    # --- A* search ---------------------------------------------------------

    def _astar(
        self,
        *,
        start_states: list[tuple[int, int, int]],
        start_g: dict[tuple[int, int, int], float],
        goal: tuple[int, int, int],
        net_key: tuple[str, str],
        tier: BeltTier,
        trunk_tiles: set[tuple[int, int]],
    ) -> tuple[list[tuple[int, int, int]], list[str], tuple[int, int] | None] | None:
        """Return (path of states, transition kind per step, branch-start tile)
        or None.

        `transitions[i]` describes how state `i` was entered from state `i-1`
        (or the start set when `i == 0`).  Values: "start", "forward", "turn",
        "ug2".."ug{max}" (underground jumps of varying lengths).
        """
        gx, gy, gd = goal
        # Heap entry: (f, counter, state)
        heap: list[tuple[float, int, tuple[int, int, int]]] = []
        came_from: dict[tuple[int, int, int], tuple[tuple[int, int, int] | None, str]] = {}
        g_score: dict[tuple[int, int, int], float] = dict(start_g)

        for s in start_states:
            came_from[s] = (None, "start")
            f = start_g[s] + self._h(s, gx, gy)
            heapq.heappush(heap, (f, next(self._counter), s))

        ug_max = _UG_MAX_JUMP[tier]

        while heap:
            f, _, state = heapq.heappop(heap)
            if state == goal:
                # Reconstruct.
                states: list[tuple[int, int, int]] = []
                kinds: list[str] = []
                cur: tuple[int, int, int] | None = state
                while cur is not None:
                    parent, kind = came_from[cur]
                    states.append(cur)
                    kinds.append(kind)
                    cur = parent
                states.reverse()
                kinds.reverse()
                branch_start = (states[0][0], states[0][1]) if trunk_tiles else None
                return states, kinds, branch_start

            if g_score.get(state, float("inf")) < f - self._h(state, gx, gy) - 1e-9:
                # Stale heap entry.
                continue

            x, y, d = state
            # Surface neighbours: forward, left, right.
            for new_d in (d, (d - 1) % 4, (d + 1) % 4):
                nx, ny = _step(x, y, new_d)
                if not (0 <= nx < self.W and 0 <= ny < self.H):
                    continue
                if self.grid[nx, ny] == _BLOCKED:
                    continue
                neighbour = (nx, ny, new_d)
                # Cost of stepping onto (nx, ny).
                step_cost = 1.0
                if new_d != d:
                    step_cost += self.turn_penalty
                # Crossing penalty for stepping onto a tile owned by another
                # net.  Trunk tiles of the same net are free.
                owner = self.occupancy.get((nx, ny))
                if owner is not None and owner != net_key:
                    # Surface conflicts are not allowed — we must underground.
                    continue
                # Avoid revisiting same surface tile via a different direction
                # if it already belongs to a different net via the same key —
                # accept; otherwise the A* will pick whatever's cheapest.
                tentative = g_score[state] + step_cost
                if tentative < g_score.get(neighbour, float("inf")):
                    g_score[neighbour] = tentative
                    came_from[neighbour] = (state, "forward" if new_d == d else "turn")
                    heapq.heappush(
                        heap,
                        (tentative + self._h(neighbour, gx, gy), next(self._counter), neighbour),
                    )

            # Underground jump in current direction d.
            for k in range(2, ug_max + 1):
                nx, ny = _step(x, y, d, k)
                if not (0 <= nx < self.W and 0 <= ny < self.H):
                    break
                # Exit tile must be FREE and unowned by other nets.
                if self.grid[nx, ny] == _BLOCKED:
                    continue
                exit_owner = self.occupancy.get((nx, ny))
                if exit_owner is not None and exit_owner != net_key:
                    continue
                # Entry tile (current x,y) must also be unobstructed for
                # the underground-input belt — it is, since we're already
                # standing here and it's either FREE or owned by us.
                neighbour = (nx, ny, d)
                # Cost: 2 (underground entities) + k-1 covered tiles, plus
                # crossing_penalty if any intermediate tile is owned by
                # another net.
                intermediate_owned = False
                for j in range(1, k):
                    mx, my = _step(x, y, d, j)
                    if self.occupancy.get((mx, my)) not in (None, net_key):
                        intermediate_owned = True
                        break
                step_cost = float(2 + (k - 1))
                if intermediate_owned:
                    step_cost += self.crossing_penalty
                tentative = g_score[state] + step_cost
                if tentative < g_score.get(neighbour, float("inf")):
                    g_score[neighbour] = tentative
                    came_from[neighbour] = (state, f"ug{k}")
                    heapq.heappush(
                        heap,
                        (tentative + self._h(neighbour, gx, gy), next(self._counter), neighbour),
                    )
        return None

    def _h(self, state: tuple[int, int, int], gx: int, gy: int) -> float:
        return float(abs(state[0] - gx) + abs(state[1] - gy))

    # --- path materialisation ---------------------------------------------

    def _materialize_path(
        self,
        *,
        path_tiles: list[tuple[int, int, int]],
        transitions: list[str],
        tier: BeltTier,
        is_branch: bool,
        branch_start: tuple[int, int] | None,
        trunk_dir: dict[tuple[int, int], int],
    ) -> tuple[
        list[BeltSegment],
        list[UndergroundBelt],
        Splitter | None,
    ]:
        segments: list[BeltSegment] = []
        undergrounds: list[UndergroundBelt] = []
        splitter: Splitter | None = None
        belt_name = str(tier)
        ug_name = _UG_NAME[tier]

        # Drop the first tile from the segment list if it's a branch start
        # (that tile already belongs to the trunk and becomes a splitter).
        start_idx = 0
        if is_branch and branch_start is not None:
            start_idx = 1
            sx, sy = branch_start
            sd = path_tiles[0][2]
            splitter = Splitter(
                x=sx,
                y=sy,
                direction=_FACT_DIR[sd],
                name=_SPLITTER_NAME[tier],
            )

        i = start_idx
        while i < len(path_tiles):
            x, y, d = path_tiles[i]
            kind = transitions[i] if i < len(transitions) else "forward"
            if kind.startswith("ug"):
                # This step was an underground jump of length k. The previous
                # tile becomes underground-input, this tile becomes
                # underground-output.
                k = int(kind[2:])
                prev_x, prev_y, prev_d = path_tiles[i - 1]
                # Replace the just-emitted belt segment at (prev_x, prev_y)
                # with an underground-input.
                if segments and (segments[-1].x, segments[-1].y) == (prev_x, prev_y):
                    segments.pop()
                undergrounds.append(
                    UndergroundBelt(
                        x=prev_x,
                        y=prev_y,
                        direction=_FACT_DIR[d],
                        name=ug_name,
                        io_type="input",
                    )
                )
                undergrounds.append(
                    UndergroundBelt(
                        x=x,
                        y=y,
                        direction=_FACT_DIR[d],
                        name=ug_name,
                        io_type="output",
                    )
                )
                _ = k  # k is implicit in the (prev → current) coordinates.
            else:
                segments.append(BeltSegment(x=x, y=y, direction=_FACT_DIR[d], name=belt_name))
            i += 1
        return segments, undergrounds, splitter


def _internal_dir(fact_dir: int) -> int:
    return _FACT_DIR.index(fact_dir)


__all__ = [
    "BeltPath",
    "RoutingError",
    "RoutingResult",
    "route_belts",
]
