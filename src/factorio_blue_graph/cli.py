"""Typer CLI entrypoint. Phase 10 will implement the full `plan` command."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from factorio_blue_graph.model.graph import RecipeHypergraph
from factorio_blue_graph.planning.demand import expand_demand

app = typer.Typer(help="Factorio Blue Graph — plan and optimize blueprints.")
console = Console()


@app.command()
def plan(
    item: str = typer.Argument(..., help="Target item, e.g. 'green-circuit'."),
    rate: float = typer.Option(..., "--rate", help="Items per minute."),
    canvas: str = typer.Option("60x60", "--canvas", help="Canvas size WxH in tiles."),
    output: str = typer.Option("blueprint.txt", "--output", help="Output file."),
) -> None:
    """Plan a blueprint for ITEM at the given rate (stub)."""
    console.print(f"[yellow]stub[/]: plan {item} @ {rate}/min on {canvas} -> {output}")


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
