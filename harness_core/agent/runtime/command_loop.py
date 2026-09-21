import asyncio
import inspect
from itertools import count


class CommandLoop:
    def __init__(
        self,
        execute,
        interrupt=None,
        maxsize=32,
        on_event=None,
        on_status=None,
    ):
        self.execute = execute
        self.interrupt_callback = interrupt
        self.queue = asyncio.PriorityQueue(maxsize)
        self.sequence = count()
        self.active = None
        self.closed = False
        self.on_event = on_event
        self.on_status = on_status

    async def set_status(self, command, status, error=None):
        command.status = status
        if self.on_status:
            result = self.on_status(command, status, error)
            if inspect.isawaitable(result):
                await result

    async def enqueue(self, command):
        if self.closed:
            raise RuntimeError("command loop is closed")
        command.set_attr("future", asyncio.get_running_loop().create_future())
        await self.queue.put((command.priority, next(self.sequence), command))
        return command

    def prepare_pending(self, command):
        if self.closed:
            raise RuntimeError("command loop is closed")
        command.set_attr("future", asyncio.get_running_loop().create_future())
        return command

    async def interrupt(self, reason=None):
        if self.active is None:
            return False
        await self.set_status(self.active, "interrupted")
        if self.interrupt_callback:
            self.interrupt_callback(reason)
        return True

    async def close(self):
        self.closed = True
        await self.queue.put((10**9, next(self.sequence), None))

    async def run(self):
        while True:
            priority, sequence, command = await self.queue.get()
            if command is None:
                self.queue.task_done()
                return
            self.active = command
            await self.set_status(command, "active")
            events = []
            try:
                async for event in self.execute(command):
                    events.append(event)
                    if self.on_event:
                        result = self.on_event(command, event)
                        if inspect.isawaitable(result):
                            await result
                if command.status != "interrupted":
                    await self.set_status(command, "completed")
                if not command.future.done():
                    command.future.set_result(events)
            except Exception as error:
                await self.set_status(command, "failed", str(error))
                if not command.future.done():
                    command.future.set_exception(error)
            finally:
                self.active = None
                self.queue.task_done()
