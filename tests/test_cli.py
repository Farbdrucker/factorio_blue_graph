"""Tests for the Typer CLI (Phase 10)."""

from __future__ import annotations

import re
from pathlib import Path

from typer.testing import CliRunner

from factorio_blue_graph.cli import app

runner = CliRunner()


def test_plan_unknown_item(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["plan", "no-such-item", "--rate", "60", "--output", str(tmp_path / "bp.txt")]
    )
    assert result.exit_code == 1
    assert "unknown item" in result.output


def test_plan_bad_canvas(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "plan",
            "green-circuit",
            "--rate",
            "60",
            "--canvas",
            "bad",
            "--output",
            str(tmp_path / "bp.txt"),
        ],
    )
    assert result.exit_code == 1
    assert "canvas" in result.output


def test_plan_green_circuit(tmp_path: Path) -> None:
    out = tmp_path / "bp.txt"
    result = runner.invoke(
        app,
        ["plan", "electronic-circuit", "--rate", "60", "--canvas", "60x60", "--output", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    bp = out.read_text()
    assert bp.startswith("0")
    assert len(bp) > 10
    # Phase summaries present in output
    assert "Phase 1" in result.output
    assert "Phase 5" in result.output
    assert "Phase 9" in result.output


def test_plan_writes_valid_blueprint_string(tmp_path: Path) -> None:
    """Blueprint string must start with '0' followed by base64 characters."""
    out = tmp_path / "bp.txt"
    runner.invoke(
        app,
        ["plan", "iron-gear-wheel", "--rate", "30", "--canvas", "40x40", "--output", str(out)],
    )
    if out.exists():
        bp = out.read_text()
        assert re.match(r"^0[A-Za-z0-9+/=]+$", bp.strip())


def test_recipes_search() -> None:
    result = runner.invoke(app, ["recipes", "--search", "circuit"])
    assert result.exit_code == 0
    assert "circuit" in result.output.lower()


def test_recipes_show() -> None:
    result = runner.invoke(app, ["recipes", "--show", "electronic-circuit", "--rate", "60"])
    assert result.exit_code == 0
    assert "electronic-circuit" in result.output
    assert "Demand" in result.output


def test_recipes_show_unknown() -> None:
    result = runner.invoke(app, ["recipes", "--show", "no-such-item"])
    assert result.exit_code == 1
    assert "unknown item" in result.output


def test_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "plan" in result.output
    assert "pareto" in result.output
    assert "recipes" in result.output
