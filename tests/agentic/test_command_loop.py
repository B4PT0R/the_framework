import asyncio

from the_framework.agent.runtime.command_loop import CommandLoop
from the_framework.agent.runtime.protocol import PromptRequest


class Executor:
    def __init__(self):
        self.commands = []
        self.interruptions = []

    async def execute(self, command):
        self.commands.append(command)
        await asyncio.sleep(0.02)
        yield command.prompt

    def interrupt(self, reason=None):
        self.interruptions.append(reason)


def test_commands_are_accepted_while_busy_and_run_sequentially():
    async def test():
        executor = Executor()
        commands = CommandLoop(executor.execute, executor.interrupt)
        worker = asyncio.create_task(commands.run())

        first = await commands.enqueue(PromptRequest(id="first", prompt="first"))
        await asyncio.sleep(0.005)
        second = await commands.enqueue(PromptRequest(id="second", prompt="second"))

        assert first.status == "active"
        assert second.status == "queued"
        assert commands.queue.qsize() == 1
        assert await first.future == ["first"]
        assert await second.future == ["second"]
        assert [command.id for command in executor.commands] == ["first", "second"]

        await commands.close()
        await worker

    asyncio.run(test())


def test_interrupt_marks_the_active_command():
    async def test():
        executor = Executor()
        commands = CommandLoop(executor.execute, executor.interrupt)
        worker = asyncio.create_task(commands.run())
        command = await commands.enqueue(PromptRequest(id="first", prompt="first"))
        await asyncio.sleep(0.005)

        assert await commands.interrupt("stop") is True
        assert command.status == "interrupted"
        assert executor.interruptions == ["stop"]

        await command.future
        await commands.close()
        await worker

    asyncio.run(test())
