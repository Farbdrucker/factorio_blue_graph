"""Diagnostic types for the tick simulator.

``Bottleneck`` mirrors ``verify.report.Issue`` so the throughput-repair loop
can be modelled after the structural repair loop without translation. The
``SimReport`` aggregator mirrors ``VerifyReport``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from factorio_blue_graph.simulate.constants import ACHIEVEMENT_THRESHOLD
from factorio_blue_graph.verify.report import Severity


@dataclass(frozen=True)
class Bottleneck:
    code: str
    severity: Severity
    message: str
    location: tuple[int, int] | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class SimReport:
    target_rate_per_sec: float = 0.0
    achieved_rate_per_sec: float = 0.0
    warmup_ticks: int = 0
    measure_ticks: int = 0
    bottlenecks: list[Bottleneck] = field(default_factory=list)
    total_crafts_by_recipe: dict[str, int] = field(default_factory=dict)

    @property
    def achievement_ratio(self) -> float:
        if self.target_rate_per_sec <= 0:
            return 1.0
        return self.achieved_rate_per_sec / self.target_rate_per_sec

    @property
    def ok(self) -> bool:
        if self.achievement_ratio < ACHIEVEMENT_THRESHOLD:
            return False
        return not any(b.severity is Severity.ERROR for b in self.bottlenecks)

    def by_code(self) -> dict[str, list[Bottleneck]]:
        out: dict[str, list[Bottleneck]] = {}
        for b in self.bottlenecks:
            out.setdefault(b.code, []).append(b)
        return out

    def codes_multiset(self) -> tuple[tuple[str, int], ...]:
        by_code = self.by_code()
        return tuple(sorted((code, len(items)) for code, items in by_code.items()))

    def counts(self) -> tuple[int, int, int]:
        e = sum(1 for b in self.bottlenecks if b.severity is Severity.ERROR)
        w = sum(1 for b in self.bottlenecks if b.severity is Severity.WARNING)
        n = sum(1 for b in self.bottlenecks if b.severity is Severity.INFO)
        return (e, w, n)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "target_rate_per_sec": self.target_rate_per_sec,
            "achieved_rate_per_sec": self.achieved_rate_per_sec,
            "achievement_ratio": self.achievement_ratio,
            "warmup_ticks": self.warmup_ticks,
            "measure_ticks": self.measure_ticks,
            "counts": {"error": self.counts()[0], "warning": self.counts()[1]},
            "bottlenecks": [
                {
                    "code": b.code,
                    "severity": b.severity.value,
                    "message": b.message,
                    "location": list(b.location) if b.location else None,
                    "payload": b.payload,
                }
                for b in self.bottlenecks
            ],
            "total_crafts_by_recipe": self.total_crafts_by_recipe,
        }

    def render(self, console: Any) -> None:
        from rich.table import Table

        ratio_pct = self.achievement_ratio * 100
        title = (
            f"throughput sim: {self.achieved_rate_per_sec * 60:.1f}/min "
            f"({ratio_pct:.0f}% of {self.target_rate_per_sec * 60:.1f}/min target)"
        )
        if not self.bottlenecks:
            console.print(f"[green]{title}[/]")
            return

        table = Table(title=title)
        table.add_column("severity", style="bold")
        table.add_column("code")
        table.add_column("location", justify="right")
        table.add_column("message")
        sev_color = {
            Severity.ERROR: "red",
            Severity.WARNING: "yellow",
            Severity.INFO: "dim",
        }
        for b in self.bottlenecks:
            color = sev_color[b.severity]
            loc = f"({b.location[0]}, {b.location[1]})" if b.location else "—"
            table.add_row(
                f"[{color}]{b.severity.value}[/]",
                b.code,
                loc,
                b.message,
            )
        console.print(table)


__all__ = ["Bottleneck", "SimReport"]
