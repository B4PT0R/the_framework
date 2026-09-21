import argparse
import asyncio
import json
import sys
from importlib import import_module

from .agent import Agent
from .protocol import ShutdownRequest
from .worker import Worker


class JsonLines:
    def __init__(self, input=sys.stdin, output=sys.stdout):
        self.input = input
        self.output = output
        self.lock = asyncio.Lock()

    async def receive(self):
        line = await asyncio.to_thread(self.input.readline)
        if not line:
            return ShutdownRequest(id="eof")
        return json.loads(line)

    async def send(self, message):
        line = json.dumps(message, separators=(",", ":")) + "\n"
        async with self.lock:
            await asyncio.to_thread(self.output.write, line)
            await asyncio.to_thread(self.output.flush)


def load_application(reference):
    try:
        module_name, attribute = reference.split(":", 1)
    except ValueError as error:
        raise ValueError("application reference must use module:attribute") from error
    application = getattr(import_module(module_name), attribute)
    if not callable(getattr(application, "compile", None)):
        raise TypeError("application reference did not resolve to AgentApplication")
    return application


async def run(
    session_path,
    *,
    application_reference=None,
    agent_name=None,
    profile_path=None,
):
    transport = JsonLines()
    spec = None
    if application_reference is not None:
        plan = load_application(application_reference).compile()
        identity = agent_name or plan.primary_agent.name
        try:
            spec = plan.agents[identity]
        except KeyError as error:
            raise ValueError(f"unknown application agent: {identity}") from error
    if profile_path is not None:
        from ..models.worker import WorkerProfile
        from ..spec import AgentSpec

        with open(profile_path, encoding="utf-8") as file:
            configured = AgentSpec.from_profile(WorkerProfile(**json.load(file)))
        # The application supplies executable composition (factories, hooks,
        # resources); the primary worker supplies the resolved specialist policy.
        # Recompiling alone would lose plugin-derived paths and user settings.
        spec = configured if spec is None else AgentSpec({
            **spec,
            **{key: configured[key] for key in (
                "instructions", "session", "queue", "idle_timeout_seconds",
                "completion_tool", "completion_retry_limit",
            )},
            "configuration": {**spec.configuration, **configured.configuration},
        })
    if spec is not None:
        agent = spec.build_agent(session_path)
        worker = spec.build_worker(agent, transport.receive, transport.send)
    else:
        agent = Agent(session_path=session_path)
        worker = Worker(agent, transport.receive, transport.send)
    await worker.run()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True)
    parser.add_argument("--application")
    parser.add_argument("--agent")
    parser.add_argument("--profile")
    args = parser.parse_args()
    asyncio.run(run(
        args.session,
        application_reference=args.application,
        agent_name=args.agent,
        profile_path=args.profile,
    ))


if __name__ == "__main__":
    main()
