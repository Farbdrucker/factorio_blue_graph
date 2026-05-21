# CLAUDE.md

Guidance for any Claude agent (or human) picking up this repo cold.

## What this project is

`factorio-blue-graph` is a Python framework that plans and optimizes
**Factorio blueprints** with respect to two objectives — **throughput**
(items/sec produced) and **footprint** (tiles occupied). It exposes a
Typer CLI (`fbg`) with Rich progress visualization, and uses a multi-layer
graph model that mixes algorithms from several mathematical fields
(MILP, CP-SAT, graph clustering, Steiner-tree routing, set cover).

## The 9-phase pipeline

1. **Demand propagation** — reverse BFS on the recipe hypergraph from the target item.
2. **Machine counts** — PuLP MILP: minimize machine count subject to mass balance.
3. **Belt tier per edge** — pick the smallest belt tier whose capacity ≥ flow.
4. **Block clustering** — Louvain modularity on the machine flow graph.
5. **Block placement** — OR-Tools CP-SAT (small N) or simulated annealing (large N).
6. **Belt routing** — sequential A* with ripup-and-reroute; rectilinear Steiner trees for multi-net merges.
7. **Inserter + power coverage** — adjacency derivation; greedy set cover for poles.
8. **Pareto sweep** — ε-constraint sweep over throughput targets.
9. **Blueprint string export** — JSON → zlib → base64 prefixed with `"0"`.

## Canonical commands

```bash
uv sync                       # install / refresh deps
uv run ruff format            # format
uv run ruff check --fix       # lint
uv run pytest                 # tests
uv run fbg plan green-circuit --rate 60 --canvas 60x60 --output bp.txt
```

## Live checklist

`docs/PLAN.md` is the live, checkbox-driven roadmap. **When you finish a
task, tick its box in `docs/PLAN.md` and push.** Do not start work without
reading it first — phases depend on each other.

## Branch

All development goes on a specific feature branches.
Do not push elsewhere without explicit permission.

## v1 scope constraints

- Vanilla Factorio 1.1 only (no Space Age, no mods)
- Output is the importable Factorio blueprint string (no ASCII / matplotlib previews)
- Routing covers belts (yellow / red / blue), inserters, and medium power poles
- Out of scope: trains, robots, beacons, modules, fluids beyond oil cracking

## Conventions

- Dep management & tooling: `uv` only (no pip, no poetry).
- Format / lint: `ruff` (config in `pyproject.toml`).
- Type hints encouraged; `mypy` available but not strict.
- Tests live under `tests/`, mirror `src/` layout.
- Recipe / entity data lives under `src/factorio_blue_graph/data/`.
