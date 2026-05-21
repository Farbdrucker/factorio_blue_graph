"""Phase 5: place `Block`s on the integer canvas grid.

For `N ≤ sa_threshold` blocks we use **OR-Tools CP-SAT**: each block is
an axis-aligned rectangle with a binary rotation toggle (0° vs 90° —
180°/270° collapse to the same footprint), all rectangles share a
single `AddNoOverlap2D` constraint, and the objective combines

- the canvas bounding-box **perimeter** (a fully-linear proxy for the
  ``bbox area`` term in `docs/PLAN.md`; area would require
  `AddMultiplicationEquality` and slows the search on N ≈ 30 — kept
  swappable behind the same objective scaling),
- the flow-weighted Manhattan distance between block top-lefts
  (aggregated over items per (source, target) pair), and
- a soft I/O port bias pulling source blocks west and sink blocks east.

For larger graphs we fall through to a deterministic shelf-packed
simulated annealing pass that minimises the same scalar objective.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from ortools.sat.python import cp_model

from factorio_blue_graph.model.graph import Block, BlockGraph

_SCALE = 1000  # integer scale for floating-point weights / rates


class PlacementError(Exception):
    """Raised when no feasible placement exists on the requested canvas."""


@dataclass(frozen=True)
class PlacedBlock:
    block_id: str
    x: int
    y: int
    rotated: bool
    width: int
    height: int


@dataclass(frozen=True)
class Placement:
    blocks: dict[str, PlacedBlock]
    canvas: tuple[int, int]
    bbox: tuple[int, int]
    flow_weighted_distance: float
    status: str
    solver: str


def place_blocks(
    block_graph: BlockGraph,
    canvas: tuple[int, int],
    *,
    alpha: float = 1.0,
    beta: float = 1.0,
    gamma: float = 0.5,
    time_limit_s: float = 30.0,
    sa_threshold: int = 30,
    random_state: int = 42,
) -> Placement:
    """Lay `block_graph`'s blocks on an `(W, H)` canvas.

    Returns a `Placement` mapping each block id to its `(x, y)` corner,
    rotation, and post-rotation `(width, height)`.
    """
    width, height = canvas
    if width <= 0 or height <= 0:
        raise ValueError(f"canvas must be positive, got {canvas}")

    blocks = list(block_graph.blocks.values())
    if not blocks:
        return Placement(
            blocks={},
            canvas=canvas,
            bbox=(0, 0),
            flow_weighted_distance=0.0,
            status="OPTIMAL",
            solver="cp-sat",
        )
    if len(blocks) == 1:
        b = blocks[0]
        w0, h0 = b.footprint
        if w0 <= width and h0 <= height:
            pb = PlacedBlock(b.id, 0, 0, False, w0, h0)
        elif h0 <= width and w0 <= height:
            pb = PlacedBlock(b.id, 0, 0, True, h0, w0)
        else:
            raise PlacementError(f"block {b.id} footprint {b.footprint} exceeds canvas {canvas}")
        return Placement(
            blocks={b.id: pb},
            canvas=canvas,
            bbox=(pb.width, pb.height),
            flow_weighted_distance=0.0,
            status="OPTIMAL",
            solver="cp-sat",
        )

    pair_flows = _aggregate_pair_flows(block_graph)
    sources, sinks = _io_blocks(block_graph)

    if len(blocks) <= sa_threshold:
        return _place_cp_sat(
            blocks,
            pair_flows,
            sources,
            sinks,
            canvas,
            alpha,
            beta,
            gamma,
            time_limit_s,
            random_state,
        )
    return _place_anneal(
        blocks,
        pair_flows,
        sources,
        sinks,
        canvas,
        alpha,
        beta,
        gamma,
        random_state,
    )


def _aggregate_pair_flows(bg: BlockGraph) -> dict[tuple[str, str], float]:
    sums: dict[tuple[str, str], float] = {}
    for edge in bg.edges:
        key = (edge.source_block, edge.target_block)
        sums[key] = sums.get(key, 0.0) + edge.rate
    return sums


def _io_blocks(bg: BlockGraph) -> tuple[list[str], list[str]]:
    sources = [bid for bid in bg.blocks if bg.graph.in_degree(bid) == 0]
    sinks = [bid for bid in bg.blocks if bg.graph.out_degree(bid) == 0]
    # If every block is both (isolated graph) skip the bias.
    if set(sources) == set(sinks):
        return [], []
    return sources, sinks


# ---------------------------------------------------------------------------
# CP-SAT solver
# ---------------------------------------------------------------------------


def _place_cp_sat(
    blocks: list[Block],
    pair_flows: dict[tuple[str, str], float],
    sources: list[str],
    sinks: list[str],
    canvas: tuple[int, int],
    alpha: float,
    beta: float,
    gamma: float,
    time_limit_s: float,
    random_state: int,
) -> Placement:
    W, H = canvas
    model = cp_model.CpModel()

    x: dict[str, cp_model.IntVar] = {}
    y: dict[str, cp_model.IntVar] = {}
    w: dict[str, cp_model.IntVar] = {}
    h: dict[str, cp_model.IntVar] = {}
    r: dict[str, cp_model.IntVar] = {}
    x_iv: list[cp_model.IntervalVar] = []
    y_iv: list[cp_model.IntervalVar] = []

    for b in blocks:
        w0, h0 = b.footprint
        if w0 > W and h0 > W:
            raise PlacementError(
                f"block {b.id} footprint {b.footprint} cannot fit canvas width {W}"
            )
        if w0 > H and h0 > H:
            raise PlacementError(
                f"block {b.id} footprint {b.footprint} cannot fit canvas height {H}"
            )

        x[b.id] = model.NewIntVar(0, W, f"x_{b.id}")
        y[b.id] = model.NewIntVar(0, H, f"y_{b.id}")
        r[b.id] = model.NewBoolVar(f"r_{b.id}")
        if w0 == h0:
            model.Add(r[b.id] == 0)

        lo, hi = (w0, h0) if w0 <= h0 else (h0, w0)
        w[b.id] = model.NewIntVar(lo, hi, f"w_{b.id}")
        h[b.id] = model.NewIntVar(lo, hi, f"h_{b.id}")
        # w == w0 + r*(h0 - w0); h == h0 + r*(w0 - h0)
        model.Add(w[b.id] == w0 + (h0 - w0) * r[b.id])
        model.Add(h[b.id] == h0 + (w0 - h0) * r[b.id])

        model.Add(x[b.id] + w[b.id] <= W)
        model.Add(y[b.id] + h[b.id] <= H)

        x_end = model.NewIntVar(0, W, f"xe_{b.id}")
        y_end = model.NewIntVar(0, H, f"ye_{b.id}")
        model.Add(x_end == x[b.id] + w[b.id])
        model.Add(y_end == y[b.id] + h[b.id])
        x_iv.append(model.NewIntervalVar(x[b.id], w[b.id], x_end, f"xiv_{b.id}"))
        y_iv.append(model.NewIntervalVar(y[b.id], h[b.id], y_end, f"yiv_{b.id}"))

    model.AddNoOverlap2D(x_iv, y_iv)

    max_x = model.NewIntVar(0, W, "max_x")
    max_y = model.NewIntVar(0, H, "max_y")
    for b in blocks:
        model.Add(max_x >= x[b.id] + w[b.id])
        model.Add(max_y >= y[b.id] + h[b.id])

    # Flow-weighted Manhattan over (src,tgt) pairs.
    flow_terms: list[cp_model.LinearExpr] = []
    flow_coefs: list[int] = []
    for (src, tgt), rate in pair_flows.items():
        if src not in x or tgt not in x:
            continue
        rate_int = int(round(rate * _SCALE))
        if rate_int == 0:
            continue
        dx = model.NewIntVar(-W, W, f"dx_{src}_{tgt}")
        dy = model.NewIntVar(-H, H, f"dy_{src}_{tgt}")
        model.Add(dx == x[src] - x[tgt])
        model.Add(dy == y[src] - y[tgt])
        adx = model.NewIntVar(0, W, f"adx_{src}_{tgt}")
        ady = model.NewIntVar(0, H, f"ady_{src}_{tgt}")
        model.AddAbsEquality(adx, dx)
        model.AddAbsEquality(ady, dy)
        flow_terms.append(adx)
        flow_terms.append(ady)
        flow_coefs.append(rate_int)
        flow_coefs.append(rate_int)

    # I/O port bias: source.x small, sink right edge close to W.
    port_terms: list[cp_model.LinearExpr] = []
    for s in sources:
        port_terms.append(x[s])
    for t in sinks:
        # distance of right edge from canvas east edge
        rd = model.NewIntVar(0, W, f"rd_{t}")
        model.Add(rd == W - x[t] - w[t])
        port_terms.append(rd)

    alpha_int = int(round(alpha * _SCALE))
    beta_int = int(round(beta * _SCALE))
    gamma_int = int(round(gamma * _SCALE))

    objective = alpha_int * (max_x + max_y)
    if flow_terms:
        objective += beta_int * cp_model.LinearExpr.WeightedSum(flow_terms, flow_coefs)
    if port_terms:
        objective += gamma_int * cp_model.LinearExpr.Sum(port_terms)
    model.Minimize(objective)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.random_seed = random_state
    solver.parameters.num_search_workers = 1  # deterministic
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise PlacementError(
            f"CP-SAT could not place {len(blocks)} blocks on canvas {canvas} "
            f"within {time_limit_s}s (status={solver.StatusName(status)})"
        )

    placed: dict[str, PlacedBlock] = {}
    for b in blocks:
        rotated = bool(solver.Value(r[b.id]))
        placed[b.id] = PlacedBlock(
            block_id=b.id,
            x=int(solver.Value(x[b.id])),
            y=int(solver.Value(y[b.id])),
            rotated=rotated,
            width=int(solver.Value(w[b.id])),
            height=int(solver.Value(h[b.id])),
        )

    bbox = (int(solver.Value(max_x)), int(solver.Value(max_y)))
    fwd = _flow_weighted_distance(placed, pair_flows)

    return Placement(
        blocks=placed,
        canvas=canvas,
        bbox=bbox,
        flow_weighted_distance=fwd,
        status=solver.StatusName(status),
        solver="cp-sat",
    )


# ---------------------------------------------------------------------------
# Simulated annealing fallback
# ---------------------------------------------------------------------------


def _place_anneal(
    blocks: list[Block],
    pair_flows: dict[tuple[str, str], float],
    sources: list[str],
    sinks: list[str],
    canvas: tuple[int, int],
    alpha: float,
    beta: float,
    gamma: float,
    random_state: int,
) -> Placement:
    W, H = canvas
    rng = np.random.default_rng(random_state)

    state = _shelf_pack(blocks, W, H)
    source_set = set(sources)
    sink_set = set(sinks)

    def energy(s: dict[str, PlacedBlock]) -> float:
        mx = max(p.x + p.width for p in s.values())
        my = max(p.y + p.height for p in s.values())
        bbox_term = alpha * (mx + my)
        flow_term = 0.0
        for (src, tgt), rate in pair_flows.items():
            if src not in s or tgt not in s:
                continue
            ps, pt = s[src], s[tgt]
            flow_term += rate * (abs(ps.x - pt.x) + abs(ps.y - pt.y))
        port_term = 0.0
        for sid in source_set:
            port_term += s[sid].x
        for tid in sink_set:
            p = s[tid]
            port_term += W - p.x - p.width
        return bbox_term + beta * flow_term + gamma * port_term

    cur_e = energy(state)
    best_state, best_e = dict(state), cur_e
    T0 = max(cur_e / 10.0, 1.0)
    T = T0
    n_iter = 5000
    block_ids = list(state.keys())

    for _ in range(n_iter):
        move = rng.integers(0, 3)
        new_state = _apply_move(state, block_ids, move, W, H, rng)
        if new_state is None:
            T *= 0.995
            continue
        new_e = energy(new_state)
        dE = new_e - cur_e
        if dE <= 0 or rng.random() < math.exp(-dE / max(T, 1e-9)):
            state = new_state
            cur_e = new_e
            if cur_e < best_e:
                best_state, best_e = dict(state), cur_e
        T *= 0.995

    mx = max(p.x + p.width for p in best_state.values())
    my = max(p.y + p.height for p in best_state.values())
    fwd = _flow_weighted_distance(best_state, pair_flows)
    return Placement(
        blocks=best_state,
        canvas=canvas,
        bbox=(mx, my),
        flow_weighted_distance=fwd,
        status="SA",
        solver="sa",
    )


def _shelf_pack(blocks: list[Block], W: int, H: int) -> dict[str, PlacedBlock]:
    """Greedy shelf packing as the SA initial state."""
    ordered = sorted(blocks, key=lambda b: -(b.footprint[0] * b.footprint[1]))
    out: dict[str, PlacedBlock] = {}
    cx, cy, row_h = 0, 0, 0
    for b in ordered:
        w0, h0 = b.footprint
        # rotate to land tall side as height for better shelf packing
        if w0 > W and h0 <= W:
            w0, h0 = h0, w0
            rotated = True
        else:
            rotated = False
        if cx + w0 > W:
            cx = 0
            cy += row_h
            row_h = 0
        if cy + h0 > H:
            raise PlacementError(f"shelf packing exhausted canvas {W}×{H} placing block {b.id}")
        out[b.id] = PlacedBlock(b.id, cx, cy, rotated, w0, h0)
        cx += w0
        row_h = max(row_h, h0)
    return out


def _apply_move(
    state: dict[str, PlacedBlock],
    block_ids: list[str],
    move: int,
    W: int,
    H: int,
    rng: np.random.Generator,
) -> dict[str, PlacedBlock] | None:
    new_state = dict(state)
    if move == 0 and len(block_ids) >= 2:
        # swap positions of two blocks
        i, j = rng.choice(len(block_ids), size=2, replace=False)
        a, b = new_state[block_ids[i]], new_state[block_ids[j]]
        a2 = PlacedBlock(a.block_id, b.x, b.y, a.rotated, a.width, a.height)
        b2 = PlacedBlock(b.block_id, a.x, a.y, b.rotated, b.width, b.height)
        if a2.x + a2.width > W or a2.y + a2.height > H:
            return None
        if b2.x + b2.width > W or b2.y + b2.height > H:
            return None
        new_state[a.block_id] = a2
        new_state[b.block_id] = b2
    elif move == 1:
        # rotate one block
        idx = int(rng.integers(0, len(block_ids)))
        p = new_state[block_ids[idx]]
        if p.width == p.height:
            return None
        rot = PlacedBlock(p.block_id, p.x, p.y, not p.rotated, p.height, p.width)
        if rot.x + rot.width > W or rot.y + rot.height > H:
            return None
        new_state[p.block_id] = rot
    else:
        # translate one block by ±1 on an axis
        idx = int(rng.integers(0, len(block_ids)))
        p = new_state[block_ids[idx]]
        dxdy = [(-1, 0), (1, 0), (0, -1), (0, 1)][int(rng.integers(0, 4))]
        nx_, ny_ = p.x + dxdy[0], p.y + dxdy[1]
        if nx_ < 0 or ny_ < 0:
            return None
        if nx_ + p.width > W or ny_ + p.height > H:
            return None
        new_state[p.block_id] = PlacedBlock(p.block_id, nx_, ny_, p.rotated, p.width, p.height)
    # overlap check against the rest
    moved_ids = {bid for bid in new_state if new_state[bid] != state.get(bid)}
    for mid in moved_ids:
        m = new_state[mid]
        for oid, o in new_state.items():
            if oid == mid:
                continue
            if _rects_overlap(m, o):
                return None
    return new_state


def _rects_overlap(a: PlacedBlock, b: PlacedBlock) -> bool:
    return not (
        a.x + a.width <= b.x
        or b.x + b.width <= a.x
        or a.y + a.height <= b.y
        or b.y + b.height <= a.y
    )


def _flow_weighted_distance(
    placed: dict[str, PlacedBlock],
    pair_flows: dict[tuple[str, str], float],
) -> float:
    total = 0.0
    for (src, tgt), rate in pair_flows.items():
        if src not in placed or tgt not in placed:
            continue
        ps, pt = placed[src], placed[tgt]
        total += rate * (abs(ps.x - pt.x) + abs(ps.y - pt.y))
    return total


__all__ = [
    "PlacedBlock",
    "Placement",
    "PlacementError",
    "place_blocks",
]
