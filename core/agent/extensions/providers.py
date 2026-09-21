import asyncio
import inspect

from modict import modict

from ..models.responses import Image, ProviderOutput
from ..models.base import Base
from .schema import parse_function_specs


def provider(func=None, *, channels=None):
    """Expose ephemeral typed context from a provider.

    Return ``None`` for no context, a tuple for multiple outputs, a
    ``ProviderOutput`` or ``Image`` explicitly, or any JSON-compatible value
    for one automatically wrapped ``ProviderOutput``. Lists remain one JSON
    value.
    """
    def decorate(function):
        function.agent_provider = True
        function.agent_provider_channels = (
            tuple(channels) if channels is not None else None
        )
        return function
    return decorate(func) if func is not None else decorate


class Provider(Base):
    name: str
    description: str | None = None
    channels: list[str] = modict.factory(lambda: ["text", "realtime"])

    @classmethod
    def from_function(cls, func, *, description=None, channels=None):
        specs = parse_function_specs(func)
        provider = cls(
            name=specs["name"],
            description=description or specs.get("description"),
            channels=channels or ["text", "realtime"],
        )
        provider.set_attr("function", func)
        return provider

    def __call__(self):
        if not self.has_attr("function"):
            raise ValueError("No function associated with this provider")
        return self.function()

    def _outputs(self, result):
        if result is None:
            return []
        values = result if isinstance(result, tuple) else (result,)
        outputs = []
        for value in values:
            if value is None:
                continue
            if isinstance(value, ProviderOutput):
                output = value.bind(name=self.name, description=self.description)
            elif isinstance(value, Image):
                output = value
            else:
                output = ProviderOutput.from_output(
                    name=self.name,
                    description=self.description,
                    output=value,
                )
            outputs.append(output)
        return outputs

    def outputs(self):
        result = self()
        if inspect.isawaitable(result):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                result = asyncio.run(result)
            else:
                if inspect.iscoroutine(result):
                    result.close()
                raise RuntimeError(
                    f"provider {self.name} is asynchronous; use aoutputs()"
                )
        return self._outputs(result)

    async def aoutputs(self):
        result = self()
        if inspect.isawaitable(result):
            result = await result
        return self._outputs(result)


def _build_provider(provider_or_func, *, description=None, channels=None):
    if isinstance(provider_or_func, Provider):
        if description is not None:
            provider_or_func.description = description
        if channels is not None:
            provider_or_func.channels = list(channels)
        return provider_or_func
    if callable(provider_or_func):
        return Provider.from_function(
            provider_or_func,
            description=description,
            channels=channels,
        )
    raise ValueError("Must be a Provider instance or a callable")


class Providers(Base[str, Provider]):
    def add(self, provider_or_func=None, *, description=None, channels=None):
        if provider_or_func is None:
            def decorator(func):
                return self.add(func, description=description, channels=channels)
            return decorator
        provider = _build_provider(
            provider_or_func,
            description=description,
            channels=channels,
        )
        if provider.name in self:
            raise ValueError(f"duplicate provider: {provider.name}")
        self[provider.name] = provider
        return provider

    def outputs(self, channel=None):
        outputs = []
        for value in self.values():
            if channel is not None and channel not in value.channels:
                continue
            outputs.extend(value.outputs())
        return outputs

    async def aoutputs(self, channel=None):
        outputs = []
        for value in self.values():
            if channel is not None and channel not in value.channels:
                continue
            outputs.extend(await value.aoutputs())
        return outputs
