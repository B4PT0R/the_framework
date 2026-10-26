import asyncio

from the_framework.agent.runtime.event_loop import EventLoop


def test_async_stream_forwards_events_and_errors():
    async def test():
        events = EventLoop(None)

        def run():
            events.emit(type("Event", (), {"type": "first"})())
            events.emit(type("Event", (), {"type": "second"})())

        streamed = [event.type async for event in events.astream(run)]
        assert streamed == ["first", "second"]

    asyncio.run(test())


def test_async_stream_does_not_lose_final_event_from_async_run():
    async def test():
        events = EventLoop(None)

        async def run():
            events.emit(type("Event", (), {"type": "final"})())

        streamed = [event.type async for event in events.astream(run)]
        assert streamed == ["final"]

    asyncio.run(test())


def test_async_stream_propagates_producer_error():
    import pytest

    async def scenario():
        events = EventLoop(None)

        async def run():
            events.emit(type("Event", (), {"type": "first"})())
            raise ValueError("producer failed")

        with pytest.raises(ValueError, match="producer failed"):
            async for _ in events.astream(run):
                pass
        assert events.subscribers["*"] == []
    asyncio.run(scenario())


def test_async_stream_cancellation_awaits_producer_cleanup():
    async def scenario():
        events = EventLoop(None)
        started, stopped = asyncio.Event(), asyncio.Event()
        tasks = set(asyncio.all_tasks())

        async def run():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                stopped.set()

        async def consume():
            async for _ in events.astream(run):
                pass

        consumer = asyncio.create_task(consume())
        await started.wait()
        consumer.cancel()
        import pytest
        with pytest.raises(asyncio.CancelledError):
            await consumer
        assert stopped.is_set()
        assert events.subscribers["*"] == []
        assert not (set(asyncio.all_tasks()) - tasks)
    asyncio.run(scenario())


def test_async_stream_early_close_awaits_producer_and_retrieves_errors(caplog):
    from contextlib import aclosing

    async def scenario():
        events = EventLoop(None)
        stopped = asyncio.Event()
        unhandled = []
        asyncio.get_running_loop().set_exception_handler(lambda loop, context: unhandled.append(context))
        tasks = set(asyncio.all_tasks())

        async def run():
            try:
                events.emit(type("Event", (), {"type": "first"})())
                await asyncio.Event().wait()
            finally:
                stopped.set()
                raise ValueError("cleanup failure")

        async with aclosing(events.astream(run)) as stream:
            async for _ in stream:
                break
        assert stopped.is_set()
        assert events.subscribers["*"] == []
        assert not (set(asyncio.all_tasks()) - tasks)
        assert not unhandled
        assert "cleanup failure" in caplog.text
    asyncio.run(scenario())


def test_async_stream_close_disconnects_but_cannot_stop_sync_thread():
    from threading import Event

    async def scenario():
        events = EventLoop(None)
        release, finished = Event(), Event()

        def run():
            try:
                events.emit(type("Event", (), {"type": "first"})())
                release.wait(3)
                events.emit(type("Event", (), {"type": "late"})())
                raise ValueError("late thread failure")
            finally:
                finished.set()

        stream = events.astream(run)
        try:
            assert (await anext(stream)).type == "first"
            await asyncio.wait_for(stream.aclose(), 1)
            assert not finished.is_set()
            assert events.subscribers["*"] == []
        finally:
            release.set()
            await asyncio.to_thread(finished.wait, 3)
        assert finished.is_set()
    asyncio.run(scenario())
