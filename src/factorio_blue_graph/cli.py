"""Typer CLI entrypoint for Factorio Blue Graph (Phase 10)."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from factorio_blue_graph.export.blueprint_string import encode
from factorio_blue_graph.layout.clustering import cluster_into_blocks
from factorio_blue_graph.layout.inserter import place_inserters
from factorio_blue_graph.layout.placement import PlacementError, place_blocks
from factorio_blue_graph.layout.power import cover_with_poles
from factorio_blue_graph.layout.routing import RoutingError, route_belts
from factorio_blue_graph.model.graph import FlowGraph, RecipeHypergraph
from factorio_blue_graph.optimize.pareto import pareto_sweep
from factorio_blue_graph.planning.demand import DemandError, expand_demand
from factorio_blue_graph.planning.lp import MachinePlanError, solve_machine_counts
from factorio_blue_graph.viz.progress import PipelineProgress

app = typer.Typer(help="Factorio Blue Graph — plan and optimize blueprints.")
console = Console()


def _parse_canvas(canvas: str) -> tuple[int, int]:
    parts = canvas.lower().split("x")
    if len(parts) != 2:
        raise ValueError(canvas)
    return (int(parts[0]), int(parts[1]))


def _occupied_tiles(placement, routing, inserters) -> frozenset[tuple[int, int]]:
    tiles: set[tuple[int, int]] = set()
    for pb in placement.blocks.values():
        for _mid, mx, my in pb.machine_tiles:
            for dx in range(3):
                for dy in range(3):
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
) -> None:
    """Plan a blueprint for ITEM at the given throughput rate."""
    try:
        canvas_wh = _parse_canvas(canvas)
    except (ValueError, TypeError):
        console.print(f"[red]invalid --canvas:[/] {canvas!r} — expected WxH, e.g. 60x60")
        raise typer.Exit(code=1) from None

    rate_per_sec = rate / 60.0
    graph = RecipeHypergraph.load_default()

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

        # Phase 4: block clustering
        with prog.phase(4):
            block_graph = cluster_into_blocks(flow_graph)
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

        # Phase 6: belt routing
        with prog.phase(6):
            try:
                routing = route_belts(placement, block_graph)
            except RoutingError as exc:
                console.print(f"[red]Routing error:[/] {exc}")
                raise typer.Exit(code=1) from exc
        unresolved_msg = f", {len(routing.unresolved)} unresolved" if routing.unresolved else ""
        prog.log(
            f"  Phase 6: routed {len(routing.paths)} belts "
            f"({routing.ripup_count} ripups{unresolved_msg})"
        )

        # Phase 7: inserters
        with prog.phase(7):
            inserters = place_inserters(placement, block_graph, flow_graph, routing)
        unres_ins = f", {len(inserters.unresolved)} unresolved" if inserters.unresolved else ""
        prog.log(
            f"  Phase 7: {len(inserters.inserters)} inserters, "
            f"{len(inserters.boundary_belts)} boundary belts{unres_ins}"
        )

        # Phase 8: power poles
        with prog.phase(8):
            occupied = _occupied_tiles(placement, routing, inserters)
            power = cover_with_poles(placement, block_graph, occupied)
        uncov = f", {len(power.uncovered_machines)} uncovered" if power.uncovered_machines else ""
        prog.log(f"  Phase 8: {len(power.poles)} power poles{uncov}")

        # Phase 9: blueprint export
        with prog.phase(9):
            bp_string = encode(
                placement,
                block_graph,
                routing,
                inserters,
                power,
                label=f"FBG {item} {rate:.0f}/min",
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


if __name__ == "__main__":
    app()
