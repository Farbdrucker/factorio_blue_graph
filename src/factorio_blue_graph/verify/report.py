"""Diagnostic data model for the structural verifier.

`Issue` is a single check failure with a stable string `code`, a severity,
a human-readable `message`, an optional tile location, and a structured
`payload` that the repair loop consumes. `VerifyReport` aggregates many
issues and knows how to render itself to Rich.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class Issue:
    code: str
    severity: Severity
    message: str
    location: tuple[int, int] | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class VerifyReport:
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.severity is Severity.ERROR for i in self.issues)

    def by_code(self) -> dict[str, list[Issue]]:
        out: dict[str, list[Issue]] = {}
        for issue in self.issues:
            out.setdefault(issue.code, []).append(issue)
        return out

    def codes_multiset(self) -> tuple[tuple[str, int], ...]:
        """Stable, hashable representation of the issue-code multiset, used by
        the repair loop's oscillation guard."""
        by_code = self.by_code()
        return tuple(sorted((code, len(items)) for code, items in by_code.items()))

    def counts(self) -> tuple[int, int, int]:
        e = sum(1 for i in self.issues if i.severity is Severity.ERROR)
        w = sum(1 for i in self.issues if i.severity is Severity.WARNING)
        n = sum(1 for i in self.issues if i.severity is Severity.INFO)
        return (e, w, n)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "counts": {"error": self.counts()[0], "warning": self.counts()[1]},
            "issues": [
                {
                    "code": i.code,
                    "severity": i.severity.value,
                    "message": i.message,
                    "location": list(i.location) if i.location else None,
                    "payload": i.payload,
                }
                for i in self.issues
            ],
        }

    def render(self, console: Any) -> None:
        """Pretty-print the report using Rich.

        Imported lazily so the verifier itself has no hard Rich dependency
        (useful for unit tests and JSON-only callers).
        """
        from rich.table import Table

        if not self.issues:
            console.print("[green]verifier: OK (0 issues)[/]")
            return

        e, w, _ = self.counts()
        title = f"verifier: {e} error" + ("s" if e != 1 else "")
        if w:
            title += f", {w} warning" + ("s" if w != 1 else "")
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
        for issue in self.issues:
            color = sev_color[issue.severity]
            loc = f"({issue.location[0]}, {issue.location[1]})" if issue.location else "—"
            table.add_row(
                f"[{color}]{issue.severity.value}[/]",
                issue.code,
                loc,
                issue.message,
            )
        console.print(table)


__all__ = ["Issue", "Severity", "VerifyReport"]
