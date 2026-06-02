"""Tests for SimReport diagnostics and helpers."""

from __future__ import annotations

from factorio_blue_graph.simulate.report import Bottleneck, SimReport
from factorio_blue_graph.verify.report import Severity


def test_empty_report_is_ok():
    r = SimReport(target_rate_per_sec=1.0, achieved_rate_per_sec=1.0)
    assert r.ok
    assert r.achievement_ratio == 1.0


def test_under_threshold_is_not_ok():
    r = SimReport(target_rate_per_sec=1.0, achieved_rate_per_sec=0.5)
    assert not r.ok
    assert r.achievement_ratio == 0.5


def test_zero_target_ratio_is_one():
    r = SimReport(target_rate_per_sec=0.0, achieved_rate_per_sec=0.0)
    assert r.achievement_ratio == 1.0
    assert r.ok


def test_error_bottleneck_makes_report_not_ok_even_if_at_target():
    r = SimReport(target_rate_per_sec=1.0, achieved_rate_per_sec=1.0)
    r.bottlenecks.append(Bottleneck(code="DUMMY", severity=Severity.ERROR, message="x"))
    assert not r.ok


def test_warning_bottleneck_does_not_block_ok():
    r = SimReport(target_rate_per_sec=1.0, achieved_rate_per_sec=1.0)
    r.bottlenecks.append(Bottleneck(code="DUMMY", severity=Severity.WARNING, message="x"))
    assert r.ok


def test_codes_multiset_is_stable_and_hashable():
    r = SimReport()
    r.bottlenecks = [
        Bottleneck(code="A", severity=Severity.ERROR, message=""),
        Bottleneck(code="B", severity=Severity.ERROR, message=""),
        Bottleneck(code="A", severity=Severity.WARNING, message=""),
    ]
    ms = r.codes_multiset()
    assert hash(ms) == hash(r.codes_multiset())
    assert ms == (("A", 2), ("B", 1))


def test_counts_breakdown():
    r = SimReport()
    r.bottlenecks = [
        Bottleneck(code="A", severity=Severity.ERROR, message=""),
        Bottleneck(code="B", severity=Severity.WARNING, message=""),
        Bottleneck(code="C", severity=Severity.INFO, message=""),
    ]
    e, w, n = r.counts()
    assert (e, w, n) == (1, 1, 1)


def test_to_dict_round_trip():
    r = SimReport(target_rate_per_sec=2.0, achieved_rate_per_sec=1.5)
    r.bottlenecks.append(
        Bottleneck(
            code="X",
            severity=Severity.ERROR,
            message="hello",
            location=(3, 4),
            payload={"foo": 1},
        )
    )
    d = r.to_dict()
    assert d["target_rate_per_sec"] == 2.0
    assert d["achieved_rate_per_sec"] == 1.5
    assert d["bottlenecks"][0]["code"] == "X"
    assert d["bottlenecks"][0]["location"] == [3, 4]
    assert d["bottlenecks"][0]["payload"] == {"foo": 1}
