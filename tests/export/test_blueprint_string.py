"""Tests for Phase 9: blueprint string encoder.

Round-trip strategy: encode a small hand-crafted set of entities, decode
with the inline `decode()` helper, and assert that the JSON structure and
entity attributes match expectations.  No external decoder library needed.
"""

from __future__ import annotations

import pytest

from factorio_blue_graph.export.blueprint_string import decode, encode
from factorio_blue_graph.layout.inserter import InserterResult
from factorio_blue_graph.layout.placement import PlacedBlock, Placement
from factorio_blue_graph.layout.power import PoleResult
from factorio_blue_graph.layout.routing import BeltPath, RoutingResult
from factorio_blue_graph.layout.tier import BeltTier
from factorio_blue_graph.model.blueprint import (
    BeltSegment,
    Inserter,
    Pole,
    Splitter,
    UndergroundBelt,
)
from factorio_blue_graph.model.graph import Block, BlockGraph, MachineNode
from factorio_blue_graph.model.recipe import Machine

# ---------------------------------------------------------------------------
# Minimal pipeline fixtures
# ---------------------------------------------------------------------------

_MACHINE = Machine(id="assembling-machine-1", crafting_speed=0.5, footprint=(3, 3))
_NODE = MachineNode(
    id="green-circuit#0",
    recipe_id="electronic-circuit",
    machine=_MACHINE,
    crafts_per_sec=0.5,
)
_BLOCK = Block(
    id="B0",
    members=(_NODE,),
    recipes=("electronic-circuit",),
    footprint=(7, 7),
    primary_recipe="electronic-circuit",
)
_PLACED = PlacedBlock(
    block_id="B0",
    x=0,
    y=0,
    rotated=False,
    width=7,
    height=7,
    machine_tiles=(("green-circuit#0", 2, 2),),
)
_PLACEMENT = Placement(
    blocks={"B0": _PLACED},
    canvas=(20, 20),
    bbox=(7, 7),
    flow_weighted_distance=0.0,
    status="OPTIMAL",
    solver="cp-sat",
)
_BLOCK_GRAPH = BlockGraph()
_BLOCK_GRAPH.add_block(_BLOCK)

# Belt entities: surface segment, underground pair, splitter.
_SEG = BeltSegment(x=0, y=0, direction=2, name="transport-belt")
_UG_IN = UndergroundBelt(x=1, y=0, direction=2, name="underground-belt", io_type="input")
_UG_OUT = UndergroundBelt(x=4, y=0, direction=2, name="underground-belt", io_type="output")
# Splitter facing N at anchor (5, 0): second tile (6, 0); center (6.0, 0.5).
_SPLITTER = Splitter(x=5, y=0, direction=0, name="splitter")
_INSERTER = Inserter(x=2, y=7, direction=4, name="inserter")
_BOUNDARY = BeltSegment(x=0, y=10, direction=0, name="transport-belt")
_POLE = Pole(x=3, y=3, name="medium-electric-pole")

_PATH = BeltPath(
    item_id="electronic-circuit",
    source_block="B0",
    target_block="B0",
    tier=BeltTier.YELLOW,
    lane_count=1,
    segments=(_SEG,),
    undergrounds=(_UG_IN, _UG_OUT),
)
_ROUTING = RoutingResult(
    paths=(_PATH,),
    splitters=(_SPLITTER,),
    ripup_count=0,
    unresolved=(),
    status="OK",
    canvas=(20, 20),
)
_INSERTER_RESULT = InserterResult(
    inserters=(_INSERTER,),
    boundary_belts=(_BOUNDARY,),
    unresolved=(),
)
_POLE_RESULT = PoleResult(poles=(_POLE,), uncovered_machines=())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_encode_starts_with_zero() -> None:
    result = encode(_PLACEMENT, _BLOCK_GRAPH, _ROUTING, _INSERTER_RESULT, _POLE_RESULT)
    assert result.startswith("0")


def test_round_trip_structure() -> None:
    result = encode(_PLACEMENT, _BLOCK_GRAPH, _ROUTING, _INSERTER_RESULT, _POLE_RESULT)
    bp = decode(result)["blueprint"]
    assert bp["item"] == "blueprint"
    assert isinstance(bp["entities"], list)
    assert len(bp["entities"]) > 0
    assert "icons" in bp
    assert bp["version"] == 281479274496000


def test_round_trip_entity_names() -> None:
    result = encode(_PLACEMENT, _BLOCK_GRAPH, _ROUTING, _INSERTER_RESULT, _POLE_RESULT)
    names = {e["name"] for e in decode(result)["blueprint"]["entities"]}
    assert "assembling-machine-1" in names
    assert "transport-belt" in names
    assert "underground-belt" in names
    assert "splitter" in names
    assert "inserter" in names
    assert "medium-electric-pole" in names


def test_assembler_position_and_recipe() -> None:
    result = encode(_PLACEMENT, _BLOCK_GRAPH, _ROUTING, _INSERTER_RESULT, _POLE_RESULT)
    entities = decode(result)["blueprint"]["entities"]
    asm = next(e for e in entities if e["name"] == "assembling-machine-1")
    # 3×3 machine at tile (2, 2) → center (3.5, 3.5)
    assert asm["position"] == {"x": 3.5, "y": 3.5}
    assert asm["recipe"] == "electronic-circuit"


def test_belt_segment_position_and_direction() -> None:
    result = encode(_PLACEMENT, _BLOCK_GRAPH, _ROUTING, _INSERTER_RESULT, _POLE_RESULT)
    entities = decode(result)["blueprint"]["entities"]
    belt = next(e for e in entities if e["name"] == "transport-belt" and e["position"]["y"] == 0.5)
    assert belt["position"] == {"x": 0.5, "y": 0.5}
    assert belt["direction"] == 2


def test_underground_belt_positions_and_types() -> None:
    result = encode(_PLACEMENT, _BLOCK_GRAPH, _ROUTING, _INSERTER_RESULT, _POLE_RESULT)
    entities = decode(result)["blueprint"]["entities"]
    ug_in = next(
        e for e in entities if e["name"] == "underground-belt" and e.get("type") == "input"
    )
    assert ug_in["position"] == {"x": 1.5, "y": 0.5}
    ug_out = next(
        e for e in entities if e["name"] == "underground-belt" and e.get("type") == "output"
    )
    assert ug_out["position"] == {"x": 4.5, "y": 0.5}


def test_splitter_center_north_facing() -> None:
    # Splitter at anchor (5, 0), facing N (dir=0): second tile (6, 0), center (6.0, 0.5).
    result = encode(_PLACEMENT, _BLOCK_GRAPH, _ROUTING, _INSERTER_RESULT, _POLE_RESULT)
    entities = decode(result)["blueprint"]["entities"]
    sp = next(e for e in entities if e["name"] == "splitter")
    assert sp["position"] == {"x": 6.0, "y": 0.5}
    assert sp["direction"] == 0


def test_splitter_suppresses_belt_at_anchor_tile() -> None:
    # _SEG is at (0, 0); _SPLITTER anchor is at (5, 0). A trunk belt at the
    # splitter anchor should be suppressed.  Build a scenario where the trunk
    # path has a segment at the splitter anchor tile.
    seg_at_anchor = BeltSegment(x=5, y=0, direction=0, name="transport-belt")
    path_with_anchor = BeltPath(
        item_id="x",
        source_block="B0",
        target_block="B0",
        tier=BeltTier.YELLOW,
        lane_count=1,
        segments=(seg_at_anchor,),
        undergrounds=(),
    )
    routing = RoutingResult(
        paths=(path_with_anchor,),
        splitters=(_SPLITTER,),
        ripup_count=0,
        unresolved=(),
        status="OK",
        canvas=(20, 20),
    )
    empty_inserters = InserterResult(inserters=(), boundary_belts=(), unresolved=())
    empty_poles = PoleResult(poles=(), uncovered_machines=())
    result = encode(_PLACEMENT, _BLOCK_GRAPH, routing, empty_inserters, empty_poles)
    entities = decode(result)["blueprint"]["entities"]
    # The belt at (5, 0) is the splitter anchor — it must not appear as a belt.
    belts_at_anchor = [
        e
        for e in entities
        if e["name"] == "transport-belt" and e["position"] == {"x": 5.5, "y": 0.5}
    ]
    assert belts_at_anchor == [], "belt segment at splitter anchor tile must be suppressed"


def test_pole_position() -> None:
    result = encode(_PLACEMENT, _BLOCK_GRAPH, _ROUTING, _INSERTER_RESULT, _POLE_RESULT)
    entities = decode(result)["blueprint"]["entities"]
    pole = next(e for e in entities if e["name"] == "medium-electric-pole")
    assert pole["position"] == {"x": 3.5, "y": 3.5}


def test_entity_numbers_sequential_from_one() -> None:
    result = encode(_PLACEMENT, _BLOCK_GRAPH, _ROUTING, _INSERTER_RESULT, _POLE_RESULT)
    numbers = [e["entity_number"] for e in decode(result)["blueprint"]["entities"]]
    assert numbers == list(range(1, len(numbers) + 1))


def test_icons_use_primary_recipe() -> None:
    result = encode(_PLACEMENT, _BLOCK_GRAPH, _ROUTING, _INSERTER_RESULT, _POLE_RESULT)
    icons = decode(result)["blueprint"]["icons"]
    assert len(icons) >= 1
    assert icons[0]["signal"]["name"] == "electronic-circuit"
    assert icons[0]["index"] == 1


def test_empty_pipeline_produces_valid_string() -> None:
    empty_bg = BlockGraph()
    empty_p = Placement(
        blocks={},
        canvas=(10, 10),
        bbox=(0, 0),
        flow_weighted_distance=0.0,
        status="OPTIMAL",
        solver="cp-sat",
    )
    empty_r = RoutingResult(
        paths=(), splitters=(), ripup_count=0, unresolved=(), status="OK", canvas=(10, 10)
    )
    empty_i = InserterResult(inserters=(), boundary_belts=(), unresolved=())
    empty_pol = PoleResult(poles=(), uncovered_machines=())
    result = encode(empty_p, empty_bg, empty_r, empty_i, empty_pol)
    assert result.startswith("0")
    bp = decode(result)["blueprint"]
    assert bp["entities"] == []
    # Falls back to the generic transport-belt icon.
    assert bp["icons"][0]["signal"]["name"] == "transport-belt"


def test_decode_rejects_bad_prefix() -> None:
    with pytest.raises(ValueError, match="missing leading '0'"):
        decode("not_a_blueprint_string")


def test_boundary_belt_included() -> None:
    result = encode(_PLACEMENT, _BLOCK_GRAPH, _ROUTING, _INSERTER_RESULT, _POLE_RESULT)
    entities = decode(result)["blueprint"]["entities"]
    # _BOUNDARY is at (0, 10), direction 0 → position (0.5, 10.5)
    boundary = next(
        (e for e in entities if e["name"] == "transport-belt" and e["position"]["y"] == 10.5),
        None,
    )
    assert boundary is not None, "boundary belt must be included in the blueprint"
    assert boundary["position"] == {"x": 0.5, "y": 10.5}


def test_label_propagated() -> None:
    result = encode(
        _PLACEMENT,
        _BLOCK_GRAPH,
        _ROUTING,
        _INSERTER_RESULT,
        _POLE_RESULT,
        label="my-factory",
    )
    assert decode(result)["blueprint"]["label"] == "my-factory"
