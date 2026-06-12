"""Phase 11: End-to-end verification test.

Runs `fbg plan green-circuit --rate 60` (default rows layout, throughput
simulation ON), decodes the resulting blueprint string, and asserts:
  - The plan passes the tick simulator at the requested rate (exit 0)
  - Valid decodable blueprint string prefixed with '0'
  - Contains assembling machines, stone furnaces, belts and power poles
  - No two entities occupy the exact same grid position
  - The string-only verifier reports zero issues
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from factorio_blue_graph.cli import app
from factorio_blue_graph.export.blueprint_string import decode

runner = CliRunner()


@pytest.fixture(scope="module")
def green_circuit_result(tmp_path_factory: pytest.TempPathFactory) -> tuple[str, str]:
    """(blueprint string, CLI output) for a fully simulated default plan."""
    out = tmp_path_factory.mktemp("e2e") / "bp.txt"
    result = runner.invoke(
        app,
        [
            "plan",
            "green-circuit",
            "--rate",
            "60",
            "--output",
            str(out),
        ],
    )
    assert result.exit_code == 0, f"CLI failed:\n{result.output}"
    assert out.exists(), "output file not written"
    return out.read_text().strip(), result.output


@pytest.fixture(scope="module")
def green_circuit_blueprint(green_circuit_result: tuple[str, str]) -> str:
    return green_circuit_result[0]


def test_blueprint_string_format(green_circuit_blueprint: str) -> None:
    bp = green_circuit_blueprint
    assert bp.startswith("0"), "blueprint string must start with '0'"
    assert len(bp) > 50, "blueprint string is suspiciously short"


def test_blueprint_decodable(green_circuit_blueprint: str) -> None:
    obj = decode(green_circuit_blueprint)
    assert "blueprint" in obj
    assert isinstance(obj["blueprint"]["entities"], list)
    assert len(obj["blueprint"]["entities"]) > 0


def test_contains_assembling_machines(green_circuit_blueprint: str) -> None:
    entities = decode(green_circuit_blueprint)["blueprint"]["entities"]
    names = [e["name"] for e in entities]
    assert "assembling-machine-1" in names, (
        "expected assembling machines for copper-cable and green-circuit"
    )


def test_contains_furnaces(green_circuit_blueprint: str) -> None:
    entities = decode(green_circuit_blueprint)["blueprint"]["entities"]
    names = [e["name"] for e in entities]
    assert "stone-furnace" in names, "expected stone furnaces for iron/copper plate smelting"


def test_contains_power_poles(green_circuit_blueprint: str) -> None:
    entities = decode(green_circuit_blueprint)["blueprint"]["entities"]
    names = [e["name"] for e in entities]
    assert "medium-electric-pole" in names, "expected at least one power pole"


def test_entity_numbers_sequential(green_circuit_blueprint: str) -> None:
    entities = decode(green_circuit_blueprint)["blueprint"]["entities"]
    numbers = [e["entity_number"] for e in entities]
    assert numbers == list(range(1, len(numbers) + 1))


def test_no_entity_position_overlaps(green_circuit_blueprint: str) -> None:
    """No two entities share the same anchor tile (position rounded to integer grid)."""
    entities = decode(green_circuit_blueprint)["blueprint"]["entities"]
    seen: set[tuple[float, float]] = set()
    duplicates: list[tuple[float, float]] = []
    for e in entities:
        pos = (e["position"]["x"], e["position"]["y"])
        if pos in seen:
            duplicates.append(pos)
        seen.add(pos)
    assert duplicates == [], f"overlapping entity positions: {duplicates}"


def test_assembler_recipes_set(green_circuit_blueprint: str) -> None:
    entities = decode(green_circuit_blueprint)["blueprint"]["entities"]
    assemblers = [e for e in entities if e["name"] == "assembling-machine-1"]
    recipes = {e.get("recipe") for e in assemblers}
    assert "electronic-circuit" in recipes, "expected an assembler set to electronic-circuit"
    assert "copper-cable" in recipes, "expected an assembler set to copper-cable"


def test_all_entities_within_canvas(green_circuit_blueprint: str) -> None:
    # The rows layout computes its own canvas; a 60/min green-circuit plan
    # stays well inside 100×100.
    entities = decode(green_circuit_blueprint)["blueprint"]["entities"]
    for e in entities:
        x, y = e["position"]["x"], e["position"]["y"]
        assert 0 <= x <= 100, f"entity {e['name']} x={x} outside canvas"
        assert 0 <= y <= 100, f"entity {e['name']} y={y} outside canvas"


def test_contains_steam_power_cluster(green_circuit_blueprint: str) -> None:
    """Phase 8b emits a sized steam-engine + boiler + offshore-pump cluster."""
    entities = decode(green_circuit_blueprint)["blueprint"]["entities"]
    names = [e["name"] for e in entities]
    assert "steam-engine" in names
    assert "boiler" in names
    assert "offshore-pump" in names


def test_verify_reports_clean(green_circuit_blueprint: str) -> None:
    """`fbg verify` on the emitted blueprint reports zero structural issues."""
    result = runner.invoke(app, ["verify", green_circuit_blueprint])
    assert result.exit_code == 0, f"verifier found issues:\n{result.output}"


def test_contains_belts(green_circuit_blueprint: str) -> None:
    """The default rows layout connects machines with real belts."""
    entities = decode(green_circuit_blueprint)["blueprint"]["entities"]
    names = [e["name"] for e in entities]
    assert names.count("transport-belt") > 20, "expected routed belt lanes"
    assert "inserter" in names, "expected machine-side inserters"


def test_simulator_meets_target(green_circuit_result: tuple[str, str]) -> None:
    """Phase 8.6 runs by default and the plan delivers the requested rate
    (exit code 0 already implies it; the log line makes failure readable)."""
    _, output = green_circuit_result
    assert "achieved" in output
    assert "throughput target not met" not in output


def test_legacy_compact_layout_still_emits(tmp_path) -> None:
    """`--layout compact` (legacy CP-SAT + A*) still produces a decodable
    blueprint when simulation is skipped."""
    out = tmp_path / "bp.txt"
    result = runner.invoke(
        app,
        [
            "plan",
            "green-circuit",
            "--rate",
            "60",
            "--canvas",
            "60x60",
            "--layout",
            "compact",
            "--no-simulate",
            "--output",
            str(out),
        ],
    )
    assert result.exit_code == 0, f"CLI failed:\n{result.output}"
    obj = decode(out.read_text().strip())
    assert len(obj["blueprint"]["entities"]) > 0
