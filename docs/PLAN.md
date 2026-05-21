# Factorio Blue Graph — Implementation Plan

A Python framework that plans and optimizes Factorio blueprints with respect to
**throughput** and **footprint**, exposed as a Typer CLI with Rich progress
visualization, and built around a multi-layer graph model that mixes algorithms
from several mathematical fields.

> **Plan file convention.** This file is the source of truth for the project
> roadmap. Each section is a checklist; when an agent finishes a task, it ticks
> the box and pushes the update so the next agent can continue cold. The plan
> itself will be committed into the repo at `docs/PLAN.md` so it stays with the
> code (see Phase 0).

---

## Context

The repository is currently empty (`LICENSE`, `.gitignore` only). The goal is
to design a framework from scratch that automates the part of Factorio play
that is naturally an OR problem: given a desired *output rate of an item*,
produce a valid blueprint that achieves that rate using as little space as
possible (or, with the dual knob, maximizes throughput within a footprint
budget).

Survey of prior art (see Phase 0 notes for full summaries):

| Project | Stack | Strength | What's missing |
|---|---|---|---|
| `kevinburke/factorio-layout-optimizer` | Python + OR-Tools CP-SAT | Clean CSP placement; rotation; fixed anchors | No recipe model, no flow constraints, no belt routing, no blueprint export |
| `Windfisch/production-flow` | C++ DAG flow | Push-pull simulation + Dijkstra upgrade search; bottleneck detection | Zero spatial/layout, no blueprint export, decoupled per-item flows |
| Patterson et al. 2023 (arXiv 2310.01505) — *Towards Automatic Design of Factorio Blueprints* | Constraint / MILP | Formal model for the combined problem; acknowledges NP-hardness | Paper is feasibility study; no open code; scaling limits |

This framework occupies the gap: end-to-end (recipe → machines → layout →
routed belts → importable blueprint), with both throughput and footprint as
first-class objectives.

### v1 scope decisions

- **Game:** vanilla 1.1 only (no Space Age, no mods).
- **Output:** importable Factorio blueprint string (base64(zlib(JSON))).
- **Routing depth:** belts (all three tiers) + inserters + power poles.

---

## Mathematical model

The pipeline runs as a sequence of relaxations, each producing the inputs for
the next. This lets us mix solvers and stay tractable.

### Graphs we maintain

1. **Recipe hypergraph** `G_R = (Items ∪ Recipes, E)`. Each recipe node `r`
   has weighted in-edges from its input items (with stoichiometry and crafting
   time) and weighted out-edges to its outputs. Cycles allowed (Kovarex, coal
   liquefaction); we mark them and handle them as fixed-point LPs.
2. **Machine flow graph** `G_F = (Machines, Item-flow edges)`. After
   solving for machine counts, we instantiate one node per machine. Edges
   carry `(item, rate)` pairs; capacity limited by the chosen belt tier.
3. **Block graph** `G_B`. Machines clustered into rectangular blocks (a
   block = `k` parallel assemblers of one recipe, plus its local belt loop).
   Nodes carry footprint `(w_b, h_b)`, edges carry aggregated item rates.
4. **Placement grid** `Z² ⊃ G_P`. Each block placed at `(x_b, y_b, θ_b)`.
5. **Routing graph** `G_T` = the placement grid minus block interiors;
   used as the search space for belt Steiner trees.

### Phases & math used

| Phase | Field | Concrete method |
|---|---|---|
| 1. Demand propagation | Graph traversal | Reverse BFS on `G_R` from target |
| 2. Machine counts | Linear / mixed-integer programming | PuLP MILP: minimize `∑ m_r` s.t. mass balance |
| 3. Belt tier per edge | LP rounding / dispatch | Pick smallest tier with `cap ≥ flow` |
| 4. Block clustering | Spectral / modularity clustering | Louvain on `G_F` weighted by flow |
| 5. Block placement | CP-SAT (small N) / simulated annealing (large N) | Minimize α·bbox + β·∑w·Manhattan |
| 6. Belt routing | Rectilinear Steiner tree w/ obstacles; VLSI ripup-and-reroute | Sequential A* with crossing penalties; lateral merge via splitters |
| 7. Inserter & power | Adjacency derivation; geometric set cover | Greedy k-center for power poles |
| 8. Pareto exploration | Multi-objective optimization | ε-constraint method on (throughput, footprint) |
| 9. Blueprint export | Encoding | JSON → zlib → base64 prefixed with `0` |

Decision variables, constraints and objectives are documented inline in each
phase below.

---

## Project layout

```
factorio_blue_graph/
├── pyproject.toml              # uv-managed; ruff config; pytest config
├── docs/
│   └── PLAN.md                 # this file, committed for cross-agent handoff
├── src/factorio_blue_graph/
│   ├── __init__.py
│   ├── cli.py                  # Typer entrypoint
│   ├── data/
│   │   ├── recipes.json        # vanilla 1.1 recipe dump
│   │   ├── items.json
│   │   └── entities.json       # belt speeds, machine footprints, pole radii
│   ├── model/
│   │   ├── recipe.py           # Pydantic Recipe, Item, Machine
│   │   ├── graph.py            # RecipeHypergraph, FlowGraph, BlockGraph
│   │   └── blueprint.py        # Entity, Blueprint dataclasses
│   ├── planning/
│   │   ├── demand.py           # Phase 1: reverse BFS
│   │   └── lp.py               # Phase 2: PuLP MILP for machine counts
│   ├── layout/
│   │   ├── tier.py             # Phase 3: belt tier picker
│   │   ├── clustering.py       # Phase 4: Louvain blocks
│   │   ├── placement.py        # Phase 5: CP-SAT + SA fallback
│   │   ├── routing.py          # Phase 6: A*/Steiner belt router
│   │   ├── inserter.py         # Phase 7a
│   │   └── power.py            # Phase 7b: greedy set cover
│   ├── optimize/
│   │   ├── objectives.py       # throughput, footprint, belt-length
│   │   └── pareto.py           # Phase 8: ε-constraint sweep
│   ├── export/
│   │   └── blueprint_string.py # Phase 9: encoder
│   └── viz/
│       ├── progress.py         # rich progress bars + status
│       └── debug.py            # internal grid render (not user output)
└── tests/
```

Dependencies (managed by `uv add`):
- runtime: `typer`, `rich`, `pulp`, `ortools`, `networkx`, `numpy`, `scipy`,
  `pydantic`, `python-louvain`
- dev: `ruff`, `pytest`, `pytest-cov`, `mypy`

---

## Phases & checklist

### Phase 0 — Project bootstrap

- [x] `uv init --package factorio_blue_graph`; commit `pyproject.toml`
- [x] Add deps via `uv add typer rich pulp ortools networkx numpy scipy pydantic python-louvain`
- [x] Add dev deps via `uv add --dev ruff pytest pytest-cov mypy`
- [x] Configure ruff in `pyproject.toml` (`[tool.ruff]` with `line-length=100`, `select=["E","F","I","UP","B","SIM"]`; `[tool.ruff.format]`)
- [x] Add `uv run ruff format` + `uv run ruff check --fix` as the canonical format/lint commands; document in `README.md`
- [x] Configure pytest (`[tool.pytest.ini_options]`, `testpaths=["tests"]`)
- [x] Copy this plan to `docs/PLAN.md` and commit; future agents update boxes there
- [x] Write `CLAUDE.md` at repo root with: project purpose (1-paragraph), the 9-phase pipeline (1 line each), the canonical commands (`uv sync`, `uv run ruff format`, `uv run ruff check --fix`, `uv run pytest`, `uv run fbg ...`), pointer to `docs/PLAN.md` as the live checklist, dev branch name `claude/factory-blueprint-research-zF12s`, and the v1 scope constraints (vanilla 1.1, blueprint-string output, belts+inserters+power)
- [x] Add `.claude/settings.json` permitting `uv run *`, `uv add *`, `uv sync`, `pytest`
- [x] First commit on `claude/factory-blueprint-research-zF12s`

### Phase 1 — Recipe data + demand propagation

- [x] Source vanilla 1.1 recipe dump (e.g. from Factorio wiki API or community JSON); store under `src/factorio_blue_graph/data/recipes.json`
- [x] `model/recipe.py`: Pydantic `Item`, `Recipe` (ingredients, products, time, category), `Machine` (crafting_speed, modules, footprint)
- [x] `model/graph.py::RecipeHypergraph`: build from data; expose `producers_of(item)`, `consumers_of(item)`, cycle detection
- [x] `planning/demand.py::expand_demand(target_item, rate_per_min)`: reverse BFS through `G_R`; resolve recipe choice via user override or default (use the first/cheapest); return `dict[recipe_id, required_crafts_per_sec]`
- [ ] Handle co-products (e.g. heavy/light oil): emit warning + require user to pin oil cracking ratios via CLI flags  *(deferred: v1 dataset has no multi-output recipes; oil products are encoded as raw inputs)*
- [x] Tests: small chains (iron-plate → gear; copper-cable → green-circuit; advanced-circuit fan-in)

### Phase 2 — Machine count MILP

- [x] `planning/lp.py::solve_machine_counts(demand, machine_choice)`:
  - Variables: `m_r ∈ ℤ≥0` per recipe
  - Constraints: for each item `i`, `∑_r produces(r,i)·m_r·speed(r) ≥ ∑_r consumes(r,i)·m_r·speed(r) + external_demand(i)`
  - Objective: `min ∑_r m_r` (footprint proxy); secondary: minimize wasted production
  - Solver: PuLP with CBC backend (bundled)
- [x] Surface utilization per machine (`actual_rate / max_rate`) for downstream tier choice
- [x] Tests: known recipes (60 SPM science) cross-checked against community calculators (Kirk McDonald)

### Phase 3 — Flow graph + belt tier assignment

- [x] `model/graph.py::FlowGraph.from_plan(plan)`: instantiate one node per machine, edges carry `(item, items_per_sec)`
- [x] `layout/tier.py::pick_tier(edge_flow)`: yellow=15/s, red=30/s, blue=45/s; lane splitting if flow > 45 (parallel belts)
- [x] Tests: high-throughput edges split into N parallel lanes

### Phase 4 — Block clustering

- [x] `layout/clustering.py::cluster_into_blocks(flow_graph, max_block_size=16)`:
  - Run Louvain modularity on `G_F` weighted by edge flow
  - Each cluster becomes a `Block` with bounding box = `ceil(sqrt(k))` × `ceil(sqrt(k))` of assembler footprint (3×3 each) plus 2-tile belt margin
  - Single-recipe blocks preferred; mixed-recipe blocks allowed only if tightly coupled
- [x] Tests: 8-assembler green-circuit cluster collapses to one block

### Phase 5 — Block placement

- [x] `layout/placement.py::place_blocks(block_graph, canvas)`:
  - Primary solver: OR-Tools CP-SAT with no-overlap2d, rotation in {0°,90°,180°,270°} (collapsed to binary 0°/90° toggle since the other two share footprints), bbox minimization
  - Objective: `α · bbox_perimeter + β · ∑_{(b,b')∈E_B} flow(b,b') · ManhattanDist(b,b') + γ · I/O port bias` (perimeter used as a linear proxy for the originally-specified bbox-area term to keep the CP-SAT objective LP-friendly; see module docstring)
  - Fallback for `len(blocks) > 30`: simulated annealing seeded from a shelf packing, same scalar objective
  - I/O ports: source blocks (in-degree 0) biased west, sink blocks (out-degree 0) biased east via the soft `γ` term
- [x] Tests: 3-block chain produces a left-to-right arrangement; non-square block rotates to fit; full-pipeline `electronic-circuit` placement fits 60×60 with no overlaps

### Phase 6 — Belt routing

- [ ] `layout/routing.py::route_belts(placement, flow_graph)`:
  - For each item flow (source block → sink block), find rectilinear path using A* on the routing grid
  - Cost: tile traversal + 10×crossing penalty + 5×turn penalty
  - Multi-source/multi-sink merge via splitters: model as rectilinear Steiner tree (FLUTE-style or NetworkX `steiner_tree`)
  - Conflict resolution: ripup-and-reroute up to `max_iters=5`; if unresolved, request a wider canvas via warning
  - Underground belts to cross obstacles (max gap = 6 tiles yellow / 8 red / 10 blue)
- [ ] Tests: two parallel flows route without overlap on a 20×20 canvas

### Phase 7 — Inserters + power

- [ ] `layout/inserter.py`: for every (belt-tile adjacent to assembler) pair on a recipe's I/O side, place inserter; choose long-handed if 2-tile reach needed
- [ ] `layout/power.py::cover_with_poles(machines)`:
  - Greedy set cover with medium electric poles (radius 3.5, area 7×7)
  - Variables: pole positions; constraints: every machine within radius of ≥1 pole; objective: minimize pole count
- [ ] Tests: 4-assembler block requires exactly 1 medium pole at center

### Phase 8 — Multi-objective Pareto

- [ ] `optimize/pareto.py::pareto_sweep(target_rates, k_points=5)`:
  - ε-constraint: fix throughput at `{target, 0.8·target, 0.6·target, ...}`, minimize footprint at each
  - Returns a list of blueprints; CLI lets user pick by index
- [ ] Tests: sweep produces monotone (throughput↑ ⇒ footprint↑) front

### Phase 9 — Blueprint string export

- [ ] `export/blueprint_string.py::encode(blueprint)`:
  - Build JSON per Factorio 1.1 blueprint schema (entities with `name`, `position`, `direction`)
  - `zlib.compress(json.dumps(...).encode())` → base64 → prefix `"0"`
- [ ] Round-trip test: encode small blueprint, paste into a known decoder (`factoriolab-blueprint-decoder` Python lib if vendored, else inline decoder in tests), confirm entity list matches

### Phase 10 — Typer CLI + Rich progress

- [ ] `cli.py`: commands
  - `fbg plan <item> --rate <items/min> --canvas WxH --output blueprint.txt`
  - `fbg pareto <item> --rate <items/min> --points 5`
  - `fbg recipes --search <substr>` (debug)
- [ ] `viz/progress.py`: `rich.progress.Progress` with one task per phase; show MILP iterations, CP-SAT branches, A* expansions
- [ ] Phase outputs streamed to stdout: "Phase 2: 18 assemblers, 4 chem plants"; "Phase 5: bbox 42×38"; "Phase 6: routed 14 belts (2 ripups)"
- [ ] On completion, write blueprint string to file and print copy-paste hint

### Phase 11 — Verification

- [ ] End-to-end: `fbg plan green-circuit --rate 60 --canvas 60x60` produces a string that, when pasted into Factorio (or the decoder lib), shows: 2 copper-cable assemblers + 1 green-circuit assembler, belts connected, 1 pole, no overlaps
- [ ] CI workflow `.github/workflows/ci.yml`: `uv sync`, `uv run ruff check`, `uv run ruff format --check`, `uv run pytest`
- [ ] README with installation (`uv tool install .`), quickstart, and a generated example blueprint

---

## Verification recipe for any agent

After making changes:

```bash
uv sync
uv run ruff format
uv run ruff check --fix
uv run pytest -q
uv run fbg plan green-circuit --rate 60 --canvas 60x60 --output /tmp/bp.txt
```

The final command must terminate within 60s and produce a non-empty
blueprint string in `/tmp/bp.txt`.

---

## Out of scope (v1)

- Space Age, Krastorio, other mods
- Trains, fluids beyond oil cracking, logistic robots
- Beacons & modules (slot left open in the data model)
- ASCII / matplotlib previews (decoder is the visualization)
- GUI

These are explicitly deferred so v1 stays shippable.
