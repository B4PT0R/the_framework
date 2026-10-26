import asyncio
import inspect
import logging
from queue import Queue
from threading import Thread, get_ident


class EventLoop:
    done = object()

    def __init__(self, agent):
        self.agent = agent
        self.queues = []
        self.subscribers = {}

    def subscribe(self, event_type: str, callback):
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        self.subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: str, callback):
        self.subscribers.get(event_type, []).remove(callback)

    def publish(self, event):
        for event_type in (event.type, "*"):
            for callback in self.subscribers.get(event_type, []):
                callback(event)

    def emit(self, event):
        self.publish(event)
        for queue in self.queues:
            queue.put(event)
        return event

    def stream(self, run, *args, **kwargs):
        queue = Queue()
        errors = []
        self.queues.append(queue)

        def target():
            try:
                run(*args, **kwargs)
            except Exception as error:  # noqa: BLE001 - forwards worker failures
                errors.append(error)
            finally:
                queue.put(self.done)

        Thread(target=target, daemon=True).start()
        try:
            while True:
                event = queue.get()
                if event is self.done:
                    break
                yield event
        finally:
            self.queues.remove(queue)
        if errors:
            raise errors[0]

    async def astream(self, run, *args, **kwargs):
        """Own the producer task until exhaustion, cancellation or explicit aclose.

        Use contextlib.aclosing for a loop that may break early. Closing cancels
        and awaits the async producer. A synchronous producer runs in a thread:
        cancelling its await cannot stop that thread or undo its side effects,
        but disconnects its events and retrieves the task's terminal exception.
        """
        queue = asyncio.Queue()
        active = True
        loop = asyncio.get_running_loop()
        loop_thread_id = get_ident()

        def enqueue(event):
            if active:
                queue.put_nowait(event)

        def receive(event):
            if not active:
                return
            if get_ident() == loop_thread_id:
                enqueue(event)
            else:
                loop.call_soon_threadsafe(enqueue, event)

        async def target():
            try:
                if inspect.iscoroutinefunction(run):
                    await run(*args, **kwargs)
                else:
                    await asyncio.to_thread(run, *args, **kwargs)
            finally:
                # Flush callbacks scheduled by the worker thread before the
                # terminal marker so the last emitted events cannot be lost.
                await asyncio.sleep(0)
                await queue.put(self.done)

        self.subscribe("*", receive)
        task = asyncio.create_task(target())
        producer_awaited = False
        try:
            while True:
                event = await queue.get()
                if event is self.done:
                    break
                yield event
            producer_awaited = True
            await task
        finally:
            active = False
            self.unsubscribe("*", receive)
            if not task.done():
                task.cancel()
            # Also retrieve failures when the consumer closes at a yielded event.
            # Normal exhaustion above still propagates the producer's exception.
            outcome, = await asyncio.gather(task, return_exceptions=True)
            if not producer_awaited and isinstance(outcome, Exception):
                logging.getLogger(__name__).error(
                    "Event stream producer failed during consumer close",
                    exc_info=(type(outcome), outcome, outcome.__traceback__),
                )
