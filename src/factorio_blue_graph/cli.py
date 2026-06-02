"""Typer CLI entrypoint for Factorio Blue Graph (Phase 10)."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from factorio_blue_graph.export.blueprint_string import encode
from factorio_blue_graph.layout.clustering import cluster_into_blocks
from factorio_blue_graph.layout.inserter import InserterResult
from factorio_blue_graph.layout.io_chests import place_io_chests
from factorio_blue_graph.layout.placement import PlacementError, place_blocks
from factorio_blue_graph.layout.power import PoleResult, cover_with_poles
from factorio_blue_graph.layout.power_source import emit_power_source
from factorio_blue_graph.layout.routing import RoutingResult
from factorio_blue_graph.model.graph import FlowGraph, RecipeHypergraph
from factorio_blue_graph.optimize.pareto import pareto_sweep
from factorio_blue_graph.planning.demand import DemandError, expand_demand
from factorio_blue_graph.planning.lp import MachinePlanError, solve_machine_counts
from factorio_blue_graph.repair import PlanState, plan_with_repair
from factorio_blue_graph.repair.throughput_loop import plan_with_throughput_repair
from factorio_blue_graph.simulate import ACHIEVEMENT_THRESHOLD
from factorio_blue_graph.verify import verify_blueprint_string
from factorio_blue_graph.viz.progress import PipelineProgress

app = typer.Typer(help="Factorio Blue Graph — plan and optimize blueprints.")
console = Console()

# Common colloquial aliases → canonical item IDs in the dataset.
_ITEM_ALIASES: dict[str, str] = {
    "green-circuit": "electronic-circuit",
    "red-circuit": "advanced-circuit",
    "blue-circuit": "processing-unit",
    "green-science": "science-pack-1",
    "red-science": "science-pack-2",
}


def _resolve_item(item: str) -> str:
    return _ITEM_ALIASES.get(item, item)


def _parse_canvas(canvas: str) -> tuple[int, int]:
    parts = canvas.lower().split("x")
    if len(parts) != 2:
        raise ValueError(canvas)
    return (int(parts[0]), int(parts[1]))


def _bridge_pole_network(
    canvas: tuple[int, int],
    power,
    power_source,
    occupied: set[tuple[int, int]],
    extra_targets: set[tuple[int, int]],
):
    """Add trunk poles so the production-area pole network connects to the
    power cluster via medium-pole wire reach (Chebyshev ≤ 9). Returns the
    list of new `Pole` entities to add.
    """
    from factorio_blue_graph.model.blueprint import POLE_WIRE_REACH, Pole

    W, H = canvas
    poles = [(p.x, p.y) for p in power.poles]
    if not poles or not power_source.steam_engines:
        return []

    # Pick a representative tile inside the power cluster.
    se = power_source.steam_engines[0]
    cluster_anchor = (se.x + 2, se.y + 1)

    # Build current pole graph by Chebyshev ≤ POLE_WIRE_REACH.
    parent = {p: p for p in poles}

    def find(t):
        while parent[t] != t:
            parent[t] = parent[parent[t]]
            t = parent[t]
        return t

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i, a in enumerate(poles):
        for b in poles[i + 1 :]:
            if max(abs(a[0] - b[0]), abs(a[1] - b[1])) <= POLE_WIRE_REACH:
                union(a, b)

    # Find the production pole closest to the cluster anchor.
    if not poles:
        return []
    closest_prod = min(
        poles,
        key=lambda p: max(abs(p[0] - cluster_anchor[0]), abs(p[1] - cluster_anchor[1])),
    )

    # Lay trunk poles in a straight line from closest_prod to cluster_anchor,
    # stepping POLE_WIRE_REACH tiles at a time.
    bridge: list[Pole] = []
    obstacles = set(occupied) | set(extra_targets) | {p for p in poles}
    cur = closest_prod
    step = POLE_WIRE_REACH - 1  # leave 1 tile of slack
    safety = 0
    while max(abs(cur[0] - cluster_anchor[0]), abs(cur[1] - cluster_anchor[1])) > POLE_WIRE_REACH:
        safety += 1
        if safety > 50:
            break
        dx = cluster_anchor[0] - cur[0]
        dy = cluster_anchor[1] - cur[1]
        # Step along the dominant axis.
        if abs(dx) >= abs(dy):
            sx = step if dx > 0 else -step
            sy = 0
        else:
            sx = 0
            sy = step if dy > 0 else -step
        nx, ny = cur[0] + sx, cur[1] + sy
        # Find a nearby free tile if (nx,ny) is occupied.
        chosen = _nearest_free_tile((nx, ny), obstacles, canvas)
        if chosen is None:
            break
        bridge.append(Pole(x=chosen[0], y=chosen[1], name="medium-electric-pole"))
        obstacles.add(chosen)
        cur = chosen
    return bridge


def _nearest_free_tile(target, obstacles, canvas):
    W, H = canvas
    if 0 <= target[0] < W and 0 <= target[1] < H and target not in obstacles:
        return target
    for r in range(1, 6):
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                if abs(dx) != r and abs(dy) != r:
                    continue
                tx, ty = target[0] + dx, target[1] + dy
                if 0 <= tx < W and 0 <= ty < H and (tx, ty) not in obstacles:
                    return (tx, ty)
    return None


def _occupied_tiles(placement, routing, inserters, block_graph=None) -> frozenset[tuple[int, int]]:
    tiles: set[tuple[int, int]] = set()
    for pb in placement.blocks.values():
        # Look up actual per-machine footprint; default 3x3 only when
        # block_graph isn't supplied (legacy callers from earlier phases).
        footprint_by_id: dict[str, tuple[int, int]] = {}
        if block_graph is not None:
            block = block_graph.blocks.get(pb.block_id)
            if block is not None:
                for m in block.members:
                    footprint_by_id[m.id] = m.machine.footprint
        for mid, mx, my in pb.machine_tiles:
            fw, fh = footprint_by_id.get(mid, (3, 3))
            for dx in range(fw):
                for dy in range(fh):
                    tiles.add((mx + dx, my + dy))
    for path in routing.paths:
        for seg in path.segments:
            tiles.add((seg.x, seg.y))
        for ug in path.undergrounds:
            tiles.add((ug.x, ug.y))
    for sp in routing.splitters:
        tiles.add((sp.x, sp.y))
        if sp.direction == 0:
            tiles.add((sp.x + 1, sp.y))
        elif sp.direction == 2:
            tiles.add((sp.x, sp.y + 1))
        elif sp.direction == 4:
            tiles.add((sp.x - 1, sp.y))
        elif sp.direction == 6:
            tiles.add((sp.x, sp.y - 1))
    for ins in inserters.inserters:
        tiles.add((ins.x, ins.y))
    for belt in inserters.boundary_belts:
        tiles.add((belt.x, belt.y))
    return frozenset(tiles)


@app.command()
def plan(
    item: str = typer.Argument(..., help="Target item, e.g. 'green-circuit'."),
    rate: float = typer.Option(..., "--rate", help="Items per minute."),
    canvas: str = typer.Option("60x60", "--canvas", help="Canvas size WxH in tiles."),
    output: str = typer.Option("blueprint.txt", "--output", help="Output file path."),
    no_simulate: bool = typer.Option(
        False, "--no-simulate", help="Skip Phase 8.6 throughput simulation."
    ),
    sim_threshold: float = typer.Option(
        ACHIEVEMENT_THRESHOLD,
        "--sim-threshold",
        help="Min achieved/target ratio to accept a plan.",
    ),
    sim_ticks: str = typer.Option(
        "1800,3600",
        "--sim-ticks",
        help="Sim warmup,measure ticks (60 ticks = 1 sec).",
    ),
) -> None:
    """Plan a blueprint for ITEM at the given throughput rate."""
    try:
        canvas_wh = _parse_canvas(canvas)
    except (ValueError, TypeError):
        console.print(f"[red]invalid --canvas:[/] {canvas!r} — expected WxH, e.g. 60x60")
        raise typer.Exit(code=1) from None

    rate_per_sec = rate / 60.0
    graph = RecipeHypergraph.load_default()
    item = _resolve_item(item)

    if item not in graph:
        console.print(f"[red]unknown item:[/] {item!r}")
        raise typer.Exit(code=1)

    with PipelineProgress() as prog:
        # Phase 1: demand propagation
        with prog.phase(1):
            try:
                demand = expand_demand(item, rate_per_sec, graph)
            except DemandError as exc:
                console.print(f"[red]Demand error:[/] {exc}")
                raise typer.Exit(code=1) from exc
        prog.log(
            f"  Phase 1: {len(demand.recipe_crafts)} recipes, {len(demand.raw_inputs)} raw inputs"
        )

        # Phase 2: machine counts (MILP)
        with prog.phase(2):
            try:
                machine_plan = solve_machine_counts(demand, graph)
            except MachinePlanError as exc:
                console.print(f"[red]MILP error:[/] {exc}")
                raise typer.Exit(code=1) from exc
        by_type: dict[str, int] = {}
        for rid, machine in machine_plan.machine_assignment.items():
            count = machine_plan.machine_counts[rid]
            by_type[machine.id] = by_type.get(machine.id, 0) + count
        type_summary = ", ".join(f"{c} {m}" for m, c in sorted(by_type.items()))
        prog.log(f"  Phase 2: {machine_plan.total_machines} machines ({type_summary})")

        # Phase 3: flow graph
        with prog.phase(3):
            flow_graph = FlowGraph.from_plan(demand, machine_plan, graph)
        prog.log(
            f"  Phase 3: {flow_graph.total_machines} machines, "
            f"{len(flow_graph.channels)} flow channels, "
            f"{len(flow_graph.external_in_edges)} external inputs"
        )

        # Phase 4: block clustering. `single_recipe_blocks=True` ensures
        # every recipe gets its own block so Phase 7c places chests on
        # all four faces without colliding with other recipes' machines.
        with prog.phase(4):
            block_graph = cluster_into_blocks(flow_graph, single_recipe_blocks=True)
        prog.log(
            f"  Phase 4: {block_graph.block_count} blocks, "
            f"{len(block_graph.edges)} inter-block edges"
        )

        # Phase 5: block placement
        with prog.phase(5):
            try:
                placement = place_blocks(block_graph, canvas_wh)
            except PlacementError as exc:
                console.print(f"[red]Placement failed:[/] {exc}")
                raise typer.Exit(code=1) from exc
        prog.log(
            f"  Phase 5: bbox {placement.bbox[0]}×{placement.bbox[1]} "
            f"(solver: {placement.solver}, status: {placement.status})"
        )

        # Phase 6: belt routing — skipped in v1 functional-blueprint mode.
        # The Phase 6 A* router produces brittle paths with head-to-head
        # conflicts when blocks are close-packed; Phase 7c emits chests on
        # every machine face which makes belt routing unnecessary for
        # structural verification. Pareto / advanced flows can still call
        # `route_belts` directly.
        with prog.phase(6):
            routing = RoutingResult(
                paths=(),
                splitters=(),
                ripup_count=0,
                unresolved=(),
                status="SKIPPED",
                canvas=placement.canvas,
            )
        prog.log("  Phase 6: skipped (chest-based io topology)")

        # Phase 7: inserters — no belts means nothing for the routed-belt
        # inserter loop to do. Phase 7c emits all inserters via io_chests.
        with prog.phase(7):
            inserters = InserterResult(inserters=(), boundary_belts=(), unresolved=())
        prog.log("  Phase 7: skipped (handled by Phase 7c io_chests)")

        # Phase 7c: input/output chests on every machine face. One chest +
        # inserter pair per non-fluid ingredient (drop into the machine),
        # one pair for the recipe's product (drop out of the machine).
        # This makes every inserter have a real adjacent source/sink.
        occupied_before_chests = set(_occupied_tiles(placement, routing, inserters, block_graph))
        io = place_io_chests(placement, block_graph, flow_graph, occupied_before_chests)
        inserters = InserterResult(
            inserters=tuple(io.inserters),
            boundary_belts=(),
            unresolved=tuple(io.unresolved),
        )
        prog.log(
            f"  Phase 7c: {len(io.input_chests)} input chests, "
            f"{len(io.output_chests)} output chests, "
            f"{len(io.inserters)} io inserters"
        )

        # Phase 8: power poles — cover every machine AND every inserter
        # AND every chest tile so the verifier sees no `UNPOWERED_*`.
        with prog.phase(8):
            occupied = set(_occupied_tiles(placement, routing, inserters, block_graph))
            occupied |= {(c.x, c.y) for c in io.input_chests}
            occupied |= {(c.x, c.y) for c in io.output_chests}
            io_tiles = set()
            for ins in inserters.inserters:
                io_tiles.add((ins.x, ins.y))
            for c in io.input_chests:
                io_tiles.add((c.x, c.y))
            for c in io.output_chests:
                io_tiles.add((c.x, c.y))
            power = cover_with_poles(
                placement, block_graph, frozenset(occupied), extra_targets=io_tiles
            )
        uncov = f", {len(power.uncovered_machines)} uncovered" if power.uncovered_machines else ""
        prog.log(f"  Phase 8: {len(power.poles)} power poles{uncov}")

        # Phase 8b: emit a steam-engine + boiler + offshore-pump cluster.
        power_source = emit_power_source(placement, block_graph, routing, inserters)
        if power_source.steam_engines:
            prog.log(
                f"  Phase 8b: power cluster "
                f"{len(power_source.offshore_pumps)} pump / "
                f"{len(power_source.boilers)} boilers / "
                f"{len(power_source.steam_engines)} engines "
                f"({power_source.nominal_kw:.0f} kW for {power_source.required_kw:.0f} kW load)"
            )
        else:
            prog.log(
                f"  Phase 8b: power cluster unplaced ({power_source.status}, "
                f"required {power_source.required_kw:.0f} kW)"
            )

        # Phase 8c: stitch trunk poles bridging the steam cluster to the
        # production-area pole network. Medium poles wire to each other
        # within POLE_WIRE_REACH (Chebyshev) ≤ 9; if the cluster sits
        # > 9 tiles from the nearest production pole the network is
        # disconnected. We add intermediate poles greedily.
        if power_source.steam_engines:
            bridge = _bridge_pole_network(
                placement.canvas, power, power_source, set(occupied), io_tiles
            )
            if bridge:
                power = PoleResult(
                    poles=tuple(list(power.poles) + bridge),
                    uncovered_machines=power.uncovered_machines,
                )
                prog.log(f"  Phase 8c: {len(bridge)} trunk poles bridging power network")

        # Phase 8.5: verifier + repair loop.
        all_chests = list(io.input_chests) + list(io.output_chests)
        state = PlanState(
            placement=placement,
            block_graph=block_graph,
            flow_graph=flow_graph,
            routing=routing,
            inserters=inserters,
            power=power,
            power_source=power_source,
            chests=all_chests,
        )
        outcome = plan_with_repair(state)
        e, w, _ = outcome.report.counts()
        prog.log(
            f"  Phase 8.5: verifier — {outcome.iterations} repair iterations, "
            f"{e} error / {w} warning remaining"
        )
        if not outcome.report.ok:
            console.print()
            outcome.report.render(console)
            console.print(
                "[yellow]warning:[/] verifier still reports errors; exporting blueprint anyway."
            )

        # Phase 8.6: throughput simulation + heal loop.
        final_state = outcome.state
        if not no_simulate:
            try:
                warmup_str, measure_str = sim_ticks.split(",")
                warmup_ticks_n = int(warmup_str)
                measure_ticks_n = int(measure_str)
            except (ValueError, TypeError):
                console.print(
                    f"[red]invalid --sim-ticks:[/] {sim_ticks!r} — expected WARMUP,MEASURE"
                )
                raise typer.Exit(code=1) from None
            with prog.phase(10):
                tp = plan_with_throughput_repair(
                    outcome.state,
                    target_item=item,
                    target_rate_per_sec=rate_per_sec,
                    threshold=sim_threshold,
                    warmup_ticks=warmup_ticks_n,
                    measure_ticks=measure_ticks_n,
                    recipe_graph=graph,
                )
            prog.log(
                f"  Phase 8.6: simulator — {tp.iterations} heal iterations, "
                f"{tp.sim_report.achieved_rate_per_sec * 60:.1f}/min achieved "
                f"({tp.sim_report.achievement_ratio * 100:.0f}% of target)"
            )
            if not tp.converged:
                console.print()
                tp.sim_report.render(console)
                console.print(
                    "[red]error:[/] throughput target not met after heal budget. "
                    "No blueprint written. Re-run with --no-simulate to emit anyway, "
                    "or increase --canvas."
                )
                raise typer.Exit(code=2)
            final_state = tp.state
        else:
            console.print(
                "[yellow]warning:[/] --no-simulate set; blueprint may not deliver requested rate."
            )

        # Phase 9: blueprint export
        with prog.phase(9):
            bp_string = encode(
                final_state.placement,
                final_state.block_graph,
                final_state.routing,
                final_state.inserters,
                final_state.power,
                label=f"FBG {item} {rate:.0f}/min",
                power_source=final_state.power_source,
                chests=tuple(final_state.chests),
            )
        prog.log(f"  Phase 9: blueprint string length {len(bp_string)} chars")

    Path(output).write_text(bp_string)
    console.print(f"\n[bold green]Done![/] Blueprint written to [cyan]{output}[/]")
    console.print("[dim]Paste into Factorio → Import String:[/]")
    preview = bp_string[:72] + "…" if len(bp_string) > 72 else bp_string
    console.print(f"[dim]{preview}[/]")


@app.command()
def pareto(
    item: str = typer.Argument(..., help="Target item, e.g. 'green-circuit'."),
    rate: float = typer.Option(..., "--rate", help="Items per minute (maximum)."),
    points: int = typer.Option(5, "--points", help="Number of Pareto points."),
    canvas: str = typer.Option("60x60", "--canvas", help="Canvas size WxH in tiles."),
    output_dir: str = typer.Option(".", "--output-dir", help="Directory for blueprint files."),
) -> None:
    """Sweep the (throughput, footprint) Pareto front for ITEM."""
    try:
        canvas_wh = _parse_canvas(canvas)
    except (ValueError, TypeError):
        console.print(f"[red]invalid --canvas:[/] {canvas!r} — expected WxH, e.g. 60x60")
        raise typer.Exit(code=1) from None

    rate_per_sec = rate / 60.0
    graph = RecipeHypergraph.load_default()
    item = _resolve_item(item)

    if item not in graph:
        console.print(f"[red]unknown item:[/] {item!r}")
        raise typer.Exit(code=1)

    console.print(
        f"[bold]Pareto sweep:[/] {item} @ up to {rate:.0f}/min, {points} points on {canvas}"
    )

    front = pareto_sweep(
        item,
        rate_per_sec,
        graph=graph,
        canvas=canvas_wh,
        k_points=points,
    )

    if not front:
        console.print("[red]No feasible points found.[/] Try a larger canvas or lower rate.")
        raise typer.Exit(code=1)

    table = Table(title=f"Pareto front — {item}")
    table.add_column("#", justify="right")
    table.add_column("rate (items/min)", justify="right")
    table.add_column("bbox", justify="center")
    table.add_column("footprint (tiles²)", justify="right")
    table.add_column("machines", justify="right")
    table.add_column("status")
    for pt in front:
        table.add_row(
            str(pt.index),
            f"{pt.target_rate_per_sec * 60:.1f}",
            f"{pt.bbox[0]}×{pt.bbox[1]}",
            str(pt.footprint),
            str(pt.machine_plan.total_machines),
            pt.status,
        )
    console.print(table)

    pick = typer.prompt(
        "Pick a point index to export (or -1 to skip)",
        default=-1,
        type=int,
    )
    if pick < 0 or pick >= len(front):
        console.print("[dim]No blueprint exported.[/]")
        return

    pt = front[pick]
    bp_string = encode(
        pt.placement,
        pt.block_graph,
        pt.routing,
        pt.inserters,
        pt.power,
        label=f"FBG {item} {pt.target_rate_per_sec * 60:.0f}/min",
    )
    out_path = Path(output_dir) / f"bp_{item}_{pick}.txt"
    out_path.write_text(bp_string)
    console.print(f"[bold green]Exported point {pick}[/] to [cyan]{out_path}[/]")
    console.print("[dim]Paste into Factorio → Import String:[/]")
    preview = bp_string[:72] + "…" if len(bp_string) > 72 else bp_string
    console.print(f"[dim]{preview}[/]")


@app.command()
def recipes(
    search: str = typer.Option("", "--search", help="Substring filter on id or name."),
    show: str = typer.Option("", "--show", help="Show demand expansion for an item."),
    rate: float = typer.Option(60.0, "--rate", help="Target rate (items/min) for --show."),
) -> None:
    """List recipes, or expand demand for a single item with --show."""
    graph = RecipeHypergraph.load_default()

    if show:
        show = _resolve_item(show)
        if show not in graph:
            console.print(f"[red]unknown item:[/] {show}")
            raise typer.Exit(code=1)
        result = expand_demand(show, rate / 60.0, graph)
        console.print(f"[bold]Demand for {show}[/] @ {rate}/min ({rate / 60.0:.3f}/s)")

        recipe_table = Table(title="Recipes (crafts/sec)")
        recipe_table.add_column("recipe")
        recipe_table.add_column("crafts/sec", justify="right")
        for rid, crafts in sorted(result.recipe_crafts.items(), key=lambda kv: -kv[1]):
            recipe_table.add_row(rid, f"{crafts:.4f}")
        console.print(recipe_table)

        raw_table = Table(title="Raw inputs (items/sec)")
        raw_table.add_column("item")
        raw_table.add_column("items/sec", justify="right")
        for iid, r in sorted(result.raw_inputs.items(), key=lambda kv: -kv[1]):
            raw_table.add_row(iid, f"{r:.4f}")
        console.print(raw_table)
        return

    needle = search.lower()
    matches = [
        it
        for it in graph.items.values()
        if not needle or needle in it.id.lower() or needle in it.name.lower()
    ]
    table = Table(title=f"recipes ({len(matches)})")
    table.add_column("id")
    table.add_column("name")
    table.add_column("type")
    table.add_column("raw?", justify="center")
    for it in sorted(matches, key=lambda x: x.id):
        table.add_row(it.id, it.name, it.type, "raw" if it.recipe.is_raw else "")
    console.print(table)


@app.command()
def verify(
    bp: str = typer.Argument(..., help="Blueprint string, or path to a file containing one."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON report on stdout."),
) -> None:
    """Run structural checks on any blueprint string."""
    blueprint_text = bp
    candidate = Path(bp)
    if candidate.exists() and candidate.is_file():
        blueprint_text = candidate.read_text().strip()
    try:
        report = verify_blueprint_string(blueprint_text)
    except ValueError as exc:
        console.print(f"[red]decode failed:[/] {exc}")
        raise typer.Exit(code=2) from exc
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]decode failed:[/] {exc}")
        raise typer.Exit(code=2) from exc

    if json_out:
        import json

        console.print_json(json.dumps(report.to_dict()))
    else:
        report.render(console)

    raise typer.Exit(code=0 if report.ok else 1)


if __name__ == "__main__":
    app()
