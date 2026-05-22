"""Tests for the Issue/VerifyReport dataclasses and aggregation logic."""

from __future__ import annotations

from factorio_blue_graph.verify.report import Issue, Severity, VerifyReport


def test_empty_report_is_ok():
    assert VerifyReport().ok is True


def test_warning_only_report_is_ok():
    report = VerifyReport(issues=[Issue(code="X", severity=Severity.WARNING, message="warn")])
    assert report.ok is True


def test_any_error_makes_report_not_ok():
    report = VerifyReport(
        issues=[
            Issue(code="X", severity=Severity.WARNING, message="warn"),
            Issue(code="Y", severity=Severity.ERROR, message="err"),
        ]
    )
    assert report.ok is False
    assert report.counts() == (1, 1, 0)


def test_codes_multiset_stable():
    a = VerifyReport(
        issues=[
            Issue(code="A", severity=Severity.ERROR, message="a"),
            Issue(code="B", severity=Severity.ERROR, message="b1"),
            Issue(code="B", severity=Severity.ERROR, message="b2"),
        ]
    )
    b = VerifyReport(
        issues=[
            Issue(code="B", severity=Severity.ERROR, message="b1"),
            Issue(code="A", severity=Severity.ERROR, message="a"),
            Issue(code="B", severity=Severity.ERROR, message="b2"),
        ]
    )
    # Order-independent.
    assert a.codes_multiset() == b.codes_multiset()
    assert a.codes_multiset() == (("A", 1), ("B", 2))


def test_to_dict_round_trip_fields():
    report = VerifyReport(
        issues=[
            Issue(
                code="X",
                severity=Severity.ERROR,
                message="m",
                location=(1, 2),
                payload={"k": "v"},
            )
        ]
    )
    out = report.to_dict()
    assert out["ok"] is False
    assert out["counts"]["error"] == 1
    assert out["issues"][0]["location"] == [1, 2]
    assert out["issues"][0]["payload"] == {"k": "v"}
