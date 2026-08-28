"""The enrichment prune loop.

Both stores had a `prune` from the day they landed and nothing called it, so the
undo history - a complete tag snapshot per write - grew without bound. Asserting
that the task calls them is the whole point; a test of the store's own prune
would have stayed green throughout.
"""

from __future__ import annotations

import asyncio

import pytest

from core import tasks


class _Pruner:
    def __init__(self, removed: int) -> None:
        self.removed = removed
        self.calls = 0

    async def prune(self) -> int:
        self.calls += 1
        return self.removed


@pytest.fixture
def _no_startup_delay(monkeypatch: pytest.MonkeyPatch):
    """The loop waits 15 minutes before its first pass; nobody waits for that."""
    real_sleep = asyncio.sleep

    async def _sleep(seconds: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(tasks.asyncio, "sleep", _sleep)


@pytest.mark.asyncio
async def test_the_loop_prunes_both_stores(
    monkeypatch: pytest.MonkeyPatch, _no_startup_delay
) -> None:
    history, reviews = _Pruner(3), _Pruner(1)
    monkeypatch.setattr(
        "core.dependencies.get_enrichment_history_service", lambda: history
    )
    monkeypatch.setattr("core.dependencies.get_import_review_service", lambda: reviews)

    task = asyncio.create_task(tasks.prune_enrichment_periodically(interval=0))
    # Long enough for several passes with sleep neutralised, then stopped the way
    # the task registry stops it at shutdown.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert history.calls >= 1
    assert reviews.calls >= 1


@pytest.mark.asyncio
async def test_a_failing_prune_does_not_kill_the_loop(
    monkeypatch: pytest.MonkeyPatch, _no_startup_delay
) -> None:
    reviews = _Pruner(0)

    class _Broken:
        async def prune(self) -> int:
            raise RuntimeError("the database is locked")

    monkeypatch.setattr(
        "core.dependencies.get_enrichment_history_service", lambda: _Broken()
    )
    monkeypatch.setattr("core.dependencies.get_import_review_service", lambda: reviews)

    task = asyncio.create_task(tasks.prune_enrichment_periodically(interval=0))
    for _ in range(6):
        await asyncio.sleep(0)
    still_running = not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert still_running
