"""One running Application per process; inert imports and explicit session scopes."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from the_framework import Application, BuildContext, ServerExtension


def test_singleton_is_owned_by_lifespan_and_visible_during_service_startup():
    observed = []

    class Service:
        async def start(self):
            observed.append(('start', Application.current()))

        async def stop(self):
            observed.append(('stop', Application.current()))

    def construct():
        assert context.application is application
        with pytest.raises(RuntimeError, match='no application is running'):
            Application.current()
        return Service()

    application = Application(name='Singleton', version='1', server_extensions=(
        ServerExtension(name='service', service_factory=construct),
    ))
    context = BuildContext()
    application.compile()
    with pytest.raises(RuntimeError, match='no application is running'):
        Application.current()
    server = application.build(context=context)
    assert server.state.application is application
    assert application.context is context
    assert dict(application)['server_extensions'][0].service is None
    with pytest.raises(RuntimeError, match='no application is running'):
        Application.current()
    with TestClient(server):
        assert Application.current() is application
        assert observed == [('start', application)]
        with pytest.raises(RuntimeError, match='cannot rebuild the running application'):
            application.build()
        assert application.context is context
    assert observed == [('start', application), ('stop', application)]
    with pytest.raises(RuntimeError, match='no application is running'):
        Application.current()


def test_second_active_application_is_rejected_without_displacing_the_first():
    first = Application(name='First', version='1')
    second = Application(name='Second', version='1')
    a, b = first.build(), second.build()
    with TestClient(a):
        with pytest.raises(RuntimeError, match='already running in this process'):
            with TestClient(b):
                pytest.fail('a second application started')
        assert Application.current() is first
    with TestClient(b):
        assert Application.current() is second
    with pytest.raises(RuntimeError, match='no application is running'):
        Application.current()


def test_failed_startup_releases_the_global_application():
    class Broken:
        async def start(self):
            assert Application.current() is failed
            raise OSError('offline')

    failed = Application(name='Broken', version='1', server_extensions=(
        ServerExtension(name='broken', service_factory=Broken),
    ))
    with pytest.raises(OSError, match='offline'):
        with TestClient(failed.build()):
            pytest.fail('startup should fail')
    with pytest.raises(RuntimeError, match='no application is running'):
        Application.current()
    recovered = Application(name='Recovered', version='1')
    with TestClient(recovered.build()):
        assert Application.current() is recovered


def test_cancelled_startup_releases_the_global_application():
    async def run():
        entered = asyncio.Event()

        class Waiting:
            async def start(self):
                assert Application.current() is application
                entered.set()
                await asyncio.Event().wait()

        application = Application(name='Waiting', version='1', server_extensions=(
            ServerExtension(name='waiting', service_factory=Waiting),
        ))
        server = application.build()
        lifespan = server.router.lifespan_context(server)
        task = asyncio.create_task(lifespan.__aenter__())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(RuntimeError, match='no application is running'):
            Application.current()

    asyncio.run(run())


def test_simultaneous_startup_claims_only_one_process_global_application():
    from concurrent.futures import ThreadPoolExecutor
    from queue import Queue
    from threading import Barrier, Event

    gate, release, outcomes = Barrier(2), Event(), Queue()

    def run(name):
        application = Application(name=name, version='1')
        server = application.build()
        gate.wait(timeout=3)
        try:
            with TestClient(server):
                outcomes.put('started')
                assert Application.current() is application
                assert release.wait(timeout=3)
        except RuntimeError as error:
            assert 'already running in this process' in str(error)
            outcomes.put('rejected')
            release.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        tasks = [executor.submit(run, name) for name in ('First', 'Second')]
        for task in tasks:
            task.result(timeout=5)
    assert sorted([outcomes.get_nowait(), outcomes.get_nowait()]) == ['rejected', 'started']
    with pytest.raises(RuntimeError, match='no application is running'):
        Application.current()
