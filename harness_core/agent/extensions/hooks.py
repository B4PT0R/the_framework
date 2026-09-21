from ..models.base import Base


def hook(func):
    func.agent_hook = True
    return func


class Hook(Base):
    name: str

    @classmethod
    def from_function(cls, func):
        hook = cls(name=func.__name__)
        hook.set_attr("function", func)
        return hook

    def __call__(self, payload):
        if not self.has_attr("function"):
            raise ValueError("No function associated with this hook")
        return self.function(payload)


def _build_hook(hook_or_func):
    if isinstance(hook_or_func, Hook):
        return hook_or_func
    if callable(hook_or_func):
        return Hook.from_function(hook_or_func)
    raise ValueError("Must be a Hook instance or a callable")


class Hooks(Base[str, Hook]):
    def add(self, hook_or_func):
        hook = _build_hook(hook_or_func)
        if hook.name in self:
            raise ValueError(f"duplicate hook: {hook.name}")
        self[hook.name] = hook
        return hook

    def run(self, name, payload):
        hook = self.get(name)
        if hook is None:
            return payload
        result = hook(payload)
        return payload if result is None else result
