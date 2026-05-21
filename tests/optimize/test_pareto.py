"""Tests for Phase 8 multi-objective Pareto sweep."""

from __future__ import annotations

import pytest

from factorio_blue_graph.model.graph import RecipeHypergraph
from factorio_blue_graph.optimize.objectives import belt_length, footprint, throughput
from factorio_blue_graph.optimize.pareto import (
    ParetoPoint,
    default_epsilons,
    pareto_sweep,
)

# -- unit -------------------------------------------------------------------


def test_default_epsilons_shape() -> None:
    eps = default_epsilons(5)
    assert eps == pytest.approx([1.0, 0.8, 0.6, 0.4, 0.2])
    assert all(0.0 < e <= 1.0 for e in eps)
    assert all(eps[i] > eps[i + 1] for i in range(len(eps) - 1))


def test_default_epsilons_single_point() -> None:
    assert default_epsilons(1) == [1.0]


def test_default_epsilons_rejects_zero() -> None:
    with pytest.raises(ValueError):
        default_epsilons(0)


def test_pareto_sweep_rejects_zero_rate() -> None:
    with pytest.raises(ValueError):
        pareto_sweep("electronic-circuit", 0.0, k_points=1)


# -- integration ------------------------------------------------------------


@pytest.fixture(scope="module")
def recipe_graph() -> RecipeHypergraph:
    return RecipeHypergraph.load_default()


def test_pareto_sweep_monotone_front(recipe_graph: RecipeHypergraph) -> None:
    target = 1.0  # 60 items/min of green circuits
    result = pareto_sweep(
        "electronic-circuit",
        target,
        graph=recipe_graph,
        canvas=(80, 80),
        k_points=3,
        placement_time_limit_s=5.0,
    )

    assert 1 <= len(result) <= 3
    assert all(isinstance(p, ParetoPoint) for p in result)

    # Sorted by ascending throughput; footprint non-decreasing.
    for i in range(len(result) - 1):
        a, b = result[i], result[i + 1]
        assert a.target_rate_per_sec <= b.target_rate_per_sec
        assert a.footprint <= b.footprint

    # Indices are dense after sorting.
    for i, p in enumerate(result):
        assert p.index == i

    # At least one point reaches the full target rate.
    rates = [p.target_rate_per_sec for p in result]
    assert max(rates) == pytest.approx(target)

    # All surviving points have a usable status.
    assert all(p.status in {"OK", "PARTIAL"} for p in result)


def test_pareto_sweep_overrides_epsilons(recipe_graph: RecipeHypergraph) -> None:
    result = pareto_sweep(
        "electronic-circuit",
        1.0,
        graph=recipe_graph,
        canvas=(80, 80),
        epsilons=[1.0, 0.5],
        placement_time_limit_s=5.0,
    )
    rates = sorted({p.target_rate_per_sec for p in result})
    # The schedule we passed (sans drops) — assert the sweep used those eps.
    assert rates == sorted({1.0, 0.5})


def test_pareto_sweep_tiny_canvas_does_not_raise(recipe_graph: RecipeHypergraph) -> None:
    # 10×10 cannot fit the green-circuit assemblers + margin; the sweep
    # should swallow PlacementError and either return an empty list or
    # only points it could place.
    result = pareto_sweep(
        "electronic-circuit",
        1.0,
        graph=recipe_graph,
        canvas=(10, 10),
        k_points=2,
        placement_time_limit_s=2.0,
    )
    assert isinstance(result, list)
    # Every survivor still satisfies its canvas (sanity).
    for p in result:
        assert p.placement.canvas == (10, 10)


# -- objectives -------------------------------------------------------------


def test_objectives_helpers_agree_with_point(recipe_graph: RecipeHypergraph) -> None:
    result = pareto_sweep(
        "electronic-circuit",
        1.0,
        graph=recipe_graph,
        canvas=(80, 80),
        k_points=1,
        placement_time_limit_s=5.0,
    )
    assert result, "expected at least one feasible point for the full target rate"
    p = result[0]
    assert footprint(p.placement) == p.footprint
    assert throughput(p.demand) == pytest.approx(p.target_rate_per_sec)
    assert belt_length(p.routing) >= 0
