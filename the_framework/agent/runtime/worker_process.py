from pathlib import Path
import argparse
import asyncio
import json
import sys
from importlib import import_module

from .agent import Agent
from .protocol import ShutdownRequest
from .worker import Worker
from ..context.session import Session


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
    durable=None,
    retain_history=None,
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
    durable = (profile.durable if profile is not None else True) if durable is None else durable
    retain_history = ((profile.retain_history if profile is not None else True)
                      if retain_history is None else retain_history)
    if durable:
        # Saved session behavior wins over launch defaults on every resume.
        session = (Session.open(session_path) if Path(session_path).exists()
                   else Session.open(session_path, retain_history=retain_history))
    else:
        session = Session(retain_history=retain_history)
    if session_id is not None and (session.path is None or not session.path.exists()):
        session.id = session_id
    if spec is not None:
        agent = spec.build_agent(
            session, session_root=Path(session_path).parent,
            application_root=application_root, profile=profile,
        )
        specialists = ()
        if plan is not None and identity in {value.name for value in plan.application.agents}:
            specialists = plan.specialists
        worker = spec.build_worker(agent, transport.receive, transport.send, specialists=specialists)
    else:
        agent = Agent(session=session)
        agent.session.update_agent(configuration=agent.configs, state=agent.states.snapshot())
        worker = Worker(agent, transport.receive, transport.send)
    await worker.run()


async def run(session_path, **options):
    with Session.writer(session_path):
        await _run(session_path, **options)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True)
    parser.add_argument("--application")
    parser.add_argument("--agent")
    parser.add_argument("--profile")
    parser.add_argument("--application-root")
    parser.add_argument("--durable", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--retain-history", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--session-id")
    args = parser.parse_args()
    asyncio.run(run(
        args.session,
        application_reference=args.application,
        agent_name=args.agent,
        profile_path=args.profile,
        application_root=args.application_root,
        durable=args.durable,
        retain_history=args.retain_history,
        session_id=args.session_id,
    ))


if __name__ == "__main__":
    main()
