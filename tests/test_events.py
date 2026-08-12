"""SSE broadcaster lifecycle regressions."""
import asyncio

import pytest

from backend import events


def test_broadcast_after_loop_closed_is_a_noop(monkeypatch):
    """A scheduler job finishing after shutdown must not raise from its
    daemon thread when the captured event loop has already closed."""
    monkeypatch.setattr(events, "_main_loop", None)
    monkeypatch.setattr(events, "_shutdown_event", None)
    loop = asyncio.new_event_loop()
    events.set_loop(loop)
    loop.close()

    events.broadcast_threadsafe("late", {"ok": True})


@pytest.mark.asyncio
async def test_shutdown_signal_wakes_subscribers_before_loop_clear():
    """Shutdown signalling remains usable until the loop is released."""
    loop = asyncio.get_running_loop()
    events.set_loop(loop)
    event = events.shutdown_event()
    assert event is not None
    events.signal_shutdown()
    await asyncio.wait_for(event.wait(), timeout=1)
    events.clear_loop()
    assert events.shutdown_event() is None
