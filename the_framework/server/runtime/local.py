"""Own one local HTTP server, independently of its desktop supervisor."""

import asyncio
import secrets
import signal
import socket
from contextlib import ExitStack, contextmanager

from .discovery import PersistentSecretStore, ServerDescriptorStore, ServerLease

RESTART_EXIT_CODE = 75


def listening_socket(port=0):
    """The supervisor may retain this listener across server replacements."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen()
        return listener
    except BaseException:
        listener.close()
        raise


@contextmanager
def shutdown_event():
    """Receive process termination while preserving the caller's signal handlers."""
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        for sig in handlers:
            loop.add_signal_handler(sig, stopped.set)
        yield stopped
    finally:
        for sig, handler in handlers.items():
            loop.remove_signal_handler(sig)
            signal.signal(sig, handler)


class LocalServer:
    """Lease, discovery and credentials for one replaceable server process.

    The caller owns supervision and application composition. An inherited socket
    is closed in this process only; the supervisor's copy remains listening.
    Browser credentials persist while process-control credentials are transient.
    """

    def __init__(self, descriptor, *, port=0, listener=None):
        self.store = ServerDescriptorStore(descriptor)
        self.port = port
        self.listener = listener
        self.token = secrets.token_urlsafe(32)
        self.browser_token = None
        self.server = None
        self.restart_requested = False
        self._resources = ExitStack()

    def __enter__(self):
        try:
            self._resources.enter_context(ServerLease(self.store.path.with_name("server.lock")))
            self.listener = self._resources.enter_context(self.listener or listening_socket(self.port))
            self.browser_token = PersistentSecretStore(self.store.path.with_name("ui-session.key")).load_or_create()
            host, port = self.listener.getsockname()
            self.origin = f"http://{host}:{port}"
            self.descriptor = self.store.write(self.store.payload(host=host, port=port, token=self.token))
            self._resources.callback(self.store.remove_if_owned, self.descriptor)
            return self
        except BaseException:
            self._resources.close()
            raise

    def __exit__(self, *exc):
        return self._resources.__exit__(*exc)

    async def request_restart(self, _intent):
        self.restart_requested = True
        self.server.should_exit = True

    async def serve(self, app, *, shutdown_timeout=5, parent_stream=None):
        """Run until graceful stop or restart, cleaning parent monitoring on exit."""
        import uvicorn

        self.server = uvicorn.Server(uvicorn.Config(
            app, access_log=False, timeout_graceful_shutdown=shutdown_timeout,
        ))
        # Uvicorn replays signals to the previous handlers. Absorb that replay
        # until the caller has released discovery, socket and lease resources.
        handlers = {
            sig: signal.signal(sig, lambda *_: setattr(self.server, "should_exit", True))
            for sig in (signal.SIGINT, signal.SIGTERM)
        }
        loop = asyncio.get_running_loop()
        parent_fd = None
        try:
            if parent_stream is not None:
                parent_fd = parent_stream.fileno()

                def parent_ready():
                    import os
                    if not os.read(parent_fd, 4096):
                        loop.remove_reader(parent_fd)
                        self.server.should_exit = True

                loop.add_reader(parent_fd, parent_ready)
            await self.server.serve(sockets=[self.listener])
            return RESTART_EXIT_CODE if self.restart_requested else 0
        finally:
            if parent_fd is not None:
                loop.remove_reader(parent_fd)
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
