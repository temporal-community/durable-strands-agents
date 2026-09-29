"""Unit tests for agentcore_worker.py's ActivityTracker — the idle-debounce logic that decides
when the AgentCore-hosted Worker is safe to drain and shut down. No real Temporal, AgentCore, or
Bedrock calls; this is pure asyncio logic this repo owns.
"""

import asyncio

import pytest

from agentcore_worker import ActivityTracker


class FakeNextActivity:
    """Stands in for the next ActivityInboundInterceptor in the chain."""

    def __init__(self, *, delay: float = 0.0, raises: Exception | None = None) -> None:
        self.delay = delay
        self.raises = raises
        self.calls = 0

    async def execute_activity(self, input):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises:
            raise self.raises
        return "ok"


async def test_wait_until_idle_returns_promptly_with_no_activity_ever():
    tracker = ActivityTracker()

    # Should return once debounce elapses, not hang waiting for an event that never fires.
    await asyncio.wait_for(tracker.wait_until_idle(debounce=0.05), timeout=1.0)


async def test_wait_until_idle_blocks_while_an_activity_is_in_flight():
    tracker = ActivityTracker()
    tracker.inflight = 1  # Simulates an Activity that started before wait_until_idle was called.

    with pytest.raises(asyncio.TimeoutError):
        # A short overall timeout that's several multiples of the debounce window: if
        # wait_until_idle incorrectly returned once per debounce tick instead of blocking on
        # inflight, this would falsely pass instead of timing out.
        await asyncio.wait_for(tracker.wait_until_idle(debounce=0.02), timeout=0.15)


async def test_wait_until_idle_first_check_honors_startup_grace_over_a_shorter_debounce():
    """A slow-to-arrive first Task (cold gRPC handshake, dispatch latency) must not be mistaken
    for genuine idleness before any Activity has ever run -- the first wait should hold out for
    startup_grace even when debounce alone would have already timed out."""
    tracker = ActivityTracker()

    with pytest.raises(asyncio.TimeoutError):
        # debounce (0.02) alone would return well within this window; only startup_grace (0.2)
        # correctly keeps it waiting past it.
        await asyncio.wait_for(
            tracker.wait_until_idle(debounce=0.02, startup_grace=0.2), timeout=0.08
        )


async def test_wait_until_idle_only_extends_the_first_check_not_later_ones():
    """Once the first (grace-extended) check has passed, later checks fall back to the plain
    debounce -- startup_grace should not keep re-applying and defeat normal idle detection."""
    tracker = ActivityTracker()

    async def bump_activity_after(delay: float) -> None:
        await asyncio.sleep(delay)
        tracker.inflight += 1
        tracker.changed.set()
        await asyncio.sleep(0.01)
        tracker.inflight -= 1
        tracker.changed.set()

    # Bump activity once, after the startup-grace window has already elapsed, to move past the
    # first check; wait_until_idle should still return promptly afterward on the plain debounce.
    asyncio.get_event_loop().create_task(bump_activity_after(0.05))

    await asyncio.wait_for(
        tracker.wait_until_idle(debounce=0.03, startup_grace=0.05), timeout=1.0
    )


async def test_wait_until_idle_returns_once_the_in_flight_activity_finishes():
    tracker = ActivityTracker()
    tracker.inflight = 1

    async def finish_soon():
        await asyncio.sleep(0.03)
        tracker.inflight = 0
        tracker.changed.set()

    asyncio.get_event_loop().create_task(finish_soon())

    # Should unblock shortly after finish_soon() drops inflight to 0 and wakes the tracker,
    # not hang forever and not return before that happens.
    await asyncio.wait_for(tracker.wait_until_idle(debounce=0.05), timeout=1.0)


async def test_intercept_activity_tracks_inflight_around_a_successful_call():
    tracker = ActivityTracker()
    next_activity = FakeNextActivity()
    tracked = tracker.intercept_activity(next_activity)

    assert tracker.inflight == 0
    result = await tracked.execute_activity(object())
    assert result == "ok"
    assert next_activity.calls == 1
    # Decremented back to 0 once the call completes.
    assert tracker.inflight == 0


async def test_intercept_activity_decrements_inflight_even_when_the_activity_raises():
    tracker = ActivityTracker()
    next_activity = FakeNextActivity(raises=RuntimeError("boom"))
    tracked = tracker.intercept_activity(next_activity)

    with pytest.raises(RuntimeError, match="boom"):
        await tracked.execute_activity(object())

    # A crashing Activity must not leak a phantom "still in flight" count that would wedge
    # wait_until_idle forever — the finally block in _TrackedActivity is what guarantees this.
    assert tracker.inflight == 0
