"""Minimal runtime double for HTTP application contract tests."""


class FakeRuntime:
    def health(self):
        return {"status": "ok" if self.running else "degraded"}

    fleet = None
    running = False

    def __init__(self):
        from the_framework.server.runtime.bridge import ApplicationBridge
        self.application = ApplicationBridge(None)
        self.submitted = []
        from types import SimpleNamespace
        self.supervisor = SimpleNamespace(send=self.submit)
        self.reset_hooks = []

    def register_reset_hooks(self, *, suspend=None, resume=None):
        self.reset_hooks.append((suspend, resume))

    def register_observer(self, observer):
        pass

    def plugin_bindings(self):
        return {}

    async def start(self):
        self.running = True

    async def stop(self):
        self.running = False

    async def submit(self, command):
        self.submitted.append(command)

    async def publish(self, event):
        pass
