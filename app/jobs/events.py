"""Progress reporting port.

The graph nodes don't know how progress is persisted — they call a
ProgressSink callback that the runner provides. The runner binds it to
the per-job `store.update_progress` partial so persistence stays in the
jobs package, but graph code has zero compile-time dependency on the
store. This unlocks unit-testing nodes with a fake sink.
"""

from __future__ import annotations

from typing import Any, Protocol


class ProgressSink(Protocol):
    """Awaitable callable used by graph nodes to report progress.

    The runner closes over `job_id` and forwards to `store.update_progress`.
    Tests provide a record-calls fake. Graph code doesn't care which.
    """

    async def __call__(
        self,
        *,
        status: str | None = None,
        stage: str | None = None,
        scene: int | None = None,
        error: str | None = None,
        result_url: str | None = None,
        state_patch: dict[str, Any] | None = None,
    ) -> None: ...


async def noop_sink(
    *,
    status: str | None = None,
    stage: str | None = None,
    scene: int | None = None,
    error: str | None = None,
    result_url: str | None = None,
    state_patch: dict[str, Any] | None = None,
) -> None:
    """A ProgressSink that drops everything. Useful when running the graph
    outside the runner (tests, CLI smoke checks) where there's no Job row
    to update."""
    return None
