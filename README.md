# factorio-blue-graph

A graph-based optimizer for Factorio 1.1 blueprints. Given a target item and a
desired production rate, it plans machine counts (MILP), clusters them into
blocks (Louvain), places blocks on a grid (CP-SAT / simulated annealing),
routes belts (A* with ripup-and-reroute), covers everything with power
(greedy set cover), and emits an importable Factorio blueprint string.

CLI built on [Typer](https://typer.tiangolo.com/); progress visualized with
[Rich](https://rich.readthedocs.io/).

## Status

Early scaffolding. See [`docs/PLAN.md`](docs/PLAN.md) for the live roadmap
and what's implemented.

## Install / run

```bash
uv sync
uv run fbg --help
```

## Development

```bash
uv sync
uv run ruff format
uv run ruff check --fix
uv run pytest
```

See [`CLAUDE.md`](CLAUDE.md) for agent / contributor conventions.
