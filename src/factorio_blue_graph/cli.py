"""Typer CLI entrypoint. Phase 10 will implement the real commands."""

from __future__ import annotations

import typer
from rich.console import Console

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
def recipes(search: str = typer.Option("", "--search")) -> None:
    """List recipes (stub)."""
    console.print(f"[yellow]stub[/]: recipes search='{search}'")


if __name__ == "__main__":
    app()
