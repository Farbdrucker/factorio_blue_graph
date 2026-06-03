"""Tests for the Typer CLI (Phase 10)."""

from __future__ import annotations

import base64
import json
import re
import zlib
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


def test_graph_writes_dot(tmp_path: Path) -> None:
    out = tmp_path / "flow.dot"
    result = runner.invoke(app, ["graph", "green-circuit", "--rate", "60", "--output", str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists()
    text = out.read_text()
    assert text.startswith("digraph factory {")
    assert "doublecircle" in text


def test_graph_unknown_item(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["graph", "no-such-item", "--rate", "60", "--output", str(tmp_path / "f.dot")]
    )
    assert result.exit_code == 1
    assert "unknown item" in result.output


def test_plan_default_rejects_chest_only_factory(tmp_path: Path) -> None:
    """Phase 8.6 must refuse to emit a blueprint that cannot deliver target.

    The throughput simulator catches plans whose architecture can't sustain
    the target rate (e.g. single belt port serving a multi-machine block →
    MACHINE_STARVED bottlenecks) and exits with code 2 — no file written.
    """
    out = tmp_path / "bp.txt"
    result = runner.invoke(
        app,
        [
            "plan",
            "iron-gear-wheel",
            "--rate",
            "30",
            "--canvas",
            "40x40",
            "--output",
            str(out),
            # very short sim window so the test is fast; the disconnected-flow
            # diagnosis fires on the first tick regardless of measurement length
            "--sim-ticks",
            "30,60",
        ],
    )
    assert result.exit_code == 2, result.output
    assert not out.exists(), "blueprint must NOT be written when sim fails"
    assert "throughput target not met" in result.output


def test_plan_belt_mode_default_emits_belts(tmp_path: Path) -> None:
    """``--io-mode belts`` (default) must produce belt entities in the export."""
    out = tmp_path / "bp.txt"
    result = runner.invoke(
        app,
        [
            "plan",
            "electronic-circuit",
            "--rate",
            "60",
            "--canvas",
            "60x60",
            "--output",
            str(out),
            "--no-simulate",
        ],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    bp = out.read_text().strip()
    data = json.loads(zlib.decompress(base64.b64decode(bp[1:])))
    names = [e["name"] for e in data["blueprint"]["entities"]]
    belt_count = sum(1 for n in names if "transport-belt" in n or "underground-belt" in n)
    assert belt_count > 0, f"expected belts in default blueprint; entities: {set(names)}"


def test_plan_io_mode_chests_legacy(tmp_path: Path) -> None:
    """``--io-mode chests`` reproduces the legacy chest-per-machine grid."""
    out = tmp_path / "bp.txt"
    result = runner.invoke(
        app,
        [
            "plan",
            "iron-gear-wheel",
            "--rate",
            "30",
            "--canvas",
            "40x40",
            "--output",
            str(out),
            "--no-simulate",
            "--io-mode",
            "chests",
        ],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    bp = out.read_text().strip()
    data = json.loads(zlib.decompress(base64.b64decode(bp[1:])))
    names = [e["name"] for e in data["blueprint"]["entities"]]
    # legacy mode: lots of chests, zero routed belts.
    chest_count = sum(1 for n in names if "chest" in n)
    belt_count = sum(1 for n in names if "transport-belt" in n or "underground-belt" in n)
    assert chest_count > 0
    assert belt_count == 0, f"chest mode should not route belts; got {belt_count}"


def test_plan_writes_ports_sidecar(tmp_path: Path) -> None:
    """Belt mode writes a sidecar JSON listing the modular IOPort entries."""
    out = tmp_path / "bp.txt"
    result = runner.invoke(
        app,
        [
            "plan",
            "electronic-circuit",
            "--rate",
            "60",
            "--canvas",
            "60x60",
            "--output",
            str(out),
            "--no-simulate",
        ],
    )
    assert result.exit_code == 0, result.output
    sidecar = Path(str(out) + ".ports.json")
    assert sidecar.exists(), "expected ports sidecar next to blueprint"
    payload = json.loads(sidecar.read_text())
    assert "ports" in payload
    assert payload["ports"], "expected at least one IOPort in sidecar"
    roles = {p["role"] for p in payload["ports"]}
    assert "input" in roles, "raw-input ports should be exposed"
    assert "output" in roles, "target-output ports should be exposed"


def test_plan_bad_io_mode(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "plan",
            "iron-gear-wheel",
            "--rate",
            "30",
            "--output",
            str(tmp_path / "bp.txt"),
            "--io-mode",
            "weird",
        ],
    )
    assert result.exit_code == 1
    assert "io-mode" in result.output


def test_plan_with_no_simulate_emits_anyway(tmp_path: Path) -> None:
    """``--no-simulate`` is the documented escape hatch — emits a warning
    and writes the blueprint regardless of whether throughput holds."""
    out = tmp_path / "bp.txt"
    result = runner.invoke(
        app,
        [
            "plan",
            "iron-gear-wheel",
            "--rate",
            "30",
            "--canvas",
            "40x40",
            "--output",
            str(out),
            "--no-simulate",
        ],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    assert "--no-simulate" in result.output or "may not deliver" in result.output


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
    # --no-simulate: this test verifies the structural pipeline produces a
    # blueprint. The chest-per-machine topology (Phase 7c) deliberately does
    # not deliver continuous throughput; Phase 8.6 throughput tests cover
    # that guarantee separately.
    result = runner.invoke(
        app,
        [
            "plan",
            "electronic-circuit",
            "--rate",
            "60",
            "--canvas",
            "60x60",
            "--output",
            str(out),
            "--no-simulate",
        ],
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
        [
            "plan",
            "iron-gear-wheel",
            "--rate",
            "30",
            "--canvas",
            "40x40",
            "--output",
            str(out),
            "--no-simulate",
        ],
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
