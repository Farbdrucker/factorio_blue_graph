"""Rich progress tracking for the FBG pipeline (Phase 10).

One task per pipeline phase; overall task tracks how many phases are done.
Log messages printed via ``PipelineProgress.log`` are safely interleaved with
the live progress bars.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

if TYPE_CHECKING:
    from rich.progress import TaskID

console = Console()

_PHASES: dict[int, str] = {
    1: "Demand propagation",
    2: "Machine counts (MILP)",
    3: "Flow graph",
    4: "Block clustering",
    5: "Block placement",
    6: "Belt routing",
    7: "Inserters",
    8: "Power poles",
    9: "Blueprint export",
    10: "Throughput simulation",
}
_N_PHASES = len(_PHASES)


class PipelineProgress:
    """Context manager that shows a Rich progress panel for each pipeline phase.

    Usage::

        with PipelineProgress() as prog:
            with prog.phase(1):
                demand = expand_demand(...)
            prog.log(f"Phase 1: {len(demand.recipe_crafts)} recipes")
    """

    def __init__(self) -> None:
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=20),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
        )
        self._overall_id: TaskID | None = None
        self._done: int = 0

    def __enter__(self) -> PipelineProgress:
        self._progress.__enter__()
        self._overall_id = self._progress.add_task("[bold cyan]Pipeline[/]", total=_N_PHASES)
        return self

    def __exit__(self, *args: object) -> None:
        self._progress.__exit__(*args)

    @contextmanager
    def phase(self, phase_num: int) -> Generator[None, None, None]:
        """Show a running task for *phase_num* until the block exits."""
        name = _PHASES.get(phase_num, f"Phase {phase_num}")
        task_id = self._progress.add_task(f"  [dim]Phase {phase_num}:[/] {name}", total=1)
        try:
            yield
        finally:
            self._progress.update(
                task_id,
                completed=1,
                description=f"  [dim]Phase {phase_num}:[/] [green]{name}[/]",
            )
            self._done += 1
            if self._overall_id is not None:
                self._progress.update(self._overall_id, completed=self._done)

    def log(self, message: str) -> None:
        """Print *message* safely interleaved with the live progress bars."""
        self._progress.console.print(message)


__all__ = ["PipelineProgress", "console"]
