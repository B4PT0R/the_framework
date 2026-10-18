from pathlib import Path
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
        raise TypeError("application reference did not resolve to Application")
    return application


async def _run(
    session_path,
    *,
    application_reference=None,
    agent_name=None,
    profile_path=None,
    application_root=None,
    session_mode=None,
    session_id=None,
):
    transport = JsonLines()
    spec = None
    plan = None
    identity = None
    if application_reference is not None:
        plan = load_application(application_reference).compile()
        public = {spec.name for spec in plan.application.agents}
        if agent_name is None and Path(session_path).exists():
            from ..context.session import Session
            agent_name = Session.open(session_path).agent_name
        if agent_name is None:
            if len(public) != 1:
                raise ValueError("select a declared agent explicitly")
            agent_name = next(iter(public))
        identity = agent_name
        try:
            spec = plan.agents[identity]
        except KeyError as error:
            raise ValueError(f"unknown application agent: {identity}") from error
    profile = None
    if profile_path is not None:
        from ..models.worker import WorkerProfile
        from ..spec import AgentSpec

        with open(profile_path, encoding="utf-8") as file:
            profile = WorkerProfile(**json.load(file))
        if spec is None:
            spec = AgentSpec.from_profile(profile)
    if spec is not None:
        if session_mode is not None:
            if profile is not None:
                profile = type(profile)({**profile, "session_mode": session_mode})
            else:
                spec = type(spec)({**spec, "session_mode": session_mode})
        mode = profile.session_mode if profile is not None else spec.session_mode
        if session_id is not None and mode == "durable" and not Path(session_path).exists():
            from ..context.session import Session
            initial = Session(id=session_id, agent_name=spec.name)
            initial.set_attr("path", Path(session_path))
            initial.save()
        agent = spec.build_agent(session_path, application_root=application_root, profile=profile)
        specialists = ()
        if plan is not None and identity in {value.name for value in plan.application.agents}:
            specialists = plan.specialists
        worker = spec.build_worker(agent, transport.receive, transport.send, specialists=specialists)
    else:
        agent = Agent(session_path=session_path)
        agent.session.update_agent(configuration=agent.configs, state=agent.states.snapshot())
        worker = Worker(agent, transport.receive, transport.send)
    if session_id is not None and agent.session.path is None:
        agent.session.id = session_id
    await worker.run()


async def run(session_path, **options):
    from ..context.session import Session
    with Session.writer(session_path):
        await _run(session_path, **options)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True)
    parser.add_argument("--application")
    parser.add_argument("--agent")
    parser.add_argument("--profile")
    parser.add_argument("--application-root")
    parser.add_argument("--session-mode", choices=("durable", "resident", "ephemeral"))
    parser.add_argument("--session-id")
    args = parser.parse_args()
    asyncio.run(run(
        args.session,
        application_reference=args.application,
        agent_name=args.agent,
        profile_path=args.profile,
        application_root=args.application_root,
        session_mode=args.session_mode,
        session_id=args.session_id,
    ))


if __name__ == "__main__":
    main()
