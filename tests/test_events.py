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


@pytest.mark.asyncio
async def test_open_loop_broadcast_delivers_and_unsubscribes():
    """A live SSE subscriber receives a worker-thread broadcast."""
    events.set_loop(asyncio.get_running_loop())
    queue = events.subscribe()
    try:
        await asyncio.to_thread(
            events.broadcast_threadsafe,
            "ingest_done",
            {"ok": True},
        )
        payload = await asyncio.wait_for(queue.get(), timeout=1)
        assert payload == 'event: ingest_done\ndata: {"ok": true}\n\n'
    finally:
        events.unsubscribe(queue)
        events.clear_loop()
