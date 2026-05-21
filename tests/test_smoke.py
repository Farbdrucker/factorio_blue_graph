"""Smoke tests: package imports and CLI is wired."""

from typer.testing import CliRunner

from factorio_blue_graph import __version__
from factorio_blue_graph.cli import app


def test_version() -> None:
    assert __version__ == "0.1.0"


def test_cli_help() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "plan" in result.stdout
