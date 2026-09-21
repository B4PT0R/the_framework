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
