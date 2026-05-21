# factorio-blue-graph

A graph-based optimizer for Factorio 1.1 blueprints. Given a target item and
a desired production rate, it plans machine counts (MILP), clusters them into
blocks (Louvain), places blocks on a grid (CP-SAT / simulated annealing),
routes belts (A* with ripup-and-reroute), covers everything with power
(greedy set cover), and emits an importable Factorio blueprint string.

CLI built on [Typer](https://typer.tiangolo.com/); progress visualized with
[Rich](https://rich.readthedocs.io/).

## Install

```bash
# as a standalone tool (recommended)
uv tool install .

# or in a local venv for development
uv sync
```

After `uv tool install .` the `fbg` command is available globally.

## Quickstart

```bash
# Plan 60 green circuits/min on a 60×60 tile canvas
fbg plan green-circuit --rate 60 --canvas 60x60 --output bp.txt

# Explore the (throughput, footprint) Pareto front
fbg pareto green-circuit --rate 60 --points 5

# Search the recipe database
fbg recipes --search circuit

# Expand the full dependency tree for an item
fbg recipes --show green-circuit --rate 60
```

On completion, `fbg plan` writes the Factorio blueprint string to the output
file and prints a copy-paste preview. Import it in-game via
**Factorio → Import String**.

## Example output

Running `fbg plan green-circuit --rate 60 --canvas 60x60` produces a blueprint
that assembles electronic circuits (green circuits) at 60/min including all
upstream smelting:

```
0eNqll1FvmzAQgP9K5WfobMfghMc9bD+imipDbp0lY5AxU6OI/z6TlIqGZvXBUyLHfN+dzxebMylND63T
1pPiTLSHmhSzsYQYVYIJYz++/3wAA5V3jdVVWmlX9do/5PRbrW2YB9Zrr6EjxdOZWFVDeEZ1HdSl0fYl
rVX1R1tIWZjaNl2Y2tjR+EqK3WOWkNPlc0jIUbsgufxKExK+63ZEVU3bgksrVRqYbKdn29clOFKwIUFK
5XYpn0k73wTP795ZVcHSxtgjfdPRL3WtUf4T3Q6hy7brRLxu92aT621ZvE1ut+UrKrdBJ1dUboNuj26G
qQNDsv/phmX3L90HfB3HFb4v1UF4L1FGV9Rxg46tqOMGHcd34KhdaZv/vWjbgfNheJnXtFPo7U7hS6S
IQ9KJeYsUS2QWhxQIZI5LXEQkLuOQDBHlPg6ZIZAHVOL513lzGkXM42PkLC5GjkDyOCQmynnveKds1z
bOp+He5JdgiuggLhDg9z6iERFnGLBAgPM1SxHRU1yuAMfs2T2CmyMWYt5dNRx1X6fXAzQcn21j4P6VN
LvQb29/FM1j07EuPwUyNDD7eE24BfLhV0J01djrO0CnX6wy46P+dD1+xpeLZFJ+uMMEmrZHGIMeIX/B
ddd67ZmQBy6FOOSU0mH4BytiJCk=
```

This encodes 32 entities: 3 assembling machines (copper cable × 2,
electronic circuit × 1), 10 stone furnaces, 10 inserters, 3 power poles,
and 6 transport belts, with no overlapping placements.

## Development

```bash
uv sync
uv run ruff format
uv run ruff check --fix
uv run pytest
```

See [`CLAUDE.md`](CLAUDE.md) for agent / contributor conventions and
[`docs/PLAN.md`](docs/PLAN.md) for the live implementation roadmap.

## Scope (v1)

- Vanilla Factorio 1.1 only (no Space Age, no mods)
- Output: importable blueprint string (base64(zlib(JSON)))
- Routing: belts (yellow/red/blue), inserters, medium electric poles
- Out of scope: trains, fluids, beacons, modules, robots
