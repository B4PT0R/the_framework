import json
import shlex

from .schema import Parameters
from ..models.base import Base
from .schema import parse_function_specs


def command(func):
    """Expose a slash command with typed, optionally multiple outputs.

    Return ``None`` for no contextual output, a tuple for multiple outputs, a
    ``CommandOutput`` or ``Image`` explicitly, or any JSON-compatible value
    for one automatically wrapped ``CommandOutput``. Lists remain one JSON
    value.
    """
    func.agent_command = True
    return func


class Command(Base):
    name: str
    description: str = ""
    parameters: Parameters

    @classmethod
    def from_function(cls, func):
        specs = parse_function_specs(func)
        command = cls(
            name=specs["name"],
            description=specs["description"],
            parameters=Parameters.from_dict(specs["parameters"]),
        )
        command.set_attr("function", func)
        return command

    def __call__(self, *args, **kwargs):
        if not self.has_attr("function"):
            raise ValueError("No function associated with this command")
        return self.function(*args, **kwargs)

    def argument_value(self, parameter, value):
        if parameter is None:
            return value
        if parameter.type == "integer":
            return int(value)
        if parameter.type == "number":
            return float(value)
        if parameter.type == "boolean":
            return value.lower() in {"1", "true", "yes", "on"}
        return value

    def arguments(self, string):
        if not string:
            return {}
        if string.startswith("{"):
            return json.loads(string)
        args = []
        kwargs = {}
        for token in shlex.split(string):
            if "=" in token:
                name, value = token.split("=", 1)
                kwargs[name] = self.argument_value(
                    self.parameters.properties.get(name),
                    value,
                )
            else:
                args.append(token)
        positional = [
            name
            for name in self.parameters.properties
            if name not in kwargs
        ][:len(args)]
        return {
            **{
                name: self.argument_value(self.parameters.properties.get(name), value)
                for name, value in zip(positional, args)
            },
            **kwargs,
        }

    def resolve(self, string=""):
        return self(**self.arguments(string.strip()))


def _build_command(command_or_func):
    if isinstance(command_or_func, Command):
        return command_or_func
    if callable(command_or_func):
        return Command.from_function(command_or_func)
    raise ValueError("Must be a Command instance or a callable")


class Commands(Base[str, Command]):
    def add(self, command_or_func):
        command = _build_command(command_or_func)
        if command.name in self:
            raise ValueError(f"duplicate command: {command.name}")
        self[command.name] = command
        return command

    def parse(self, prompt):
        if not isinstance(prompt, str) or not prompt.startswith("/"):
            return None, None
        name, _, arguments = prompt[1:].partition(" ")
        return name, arguments.strip()

    def resolve(self, prompt):
        name, arguments = self.parse(prompt)
        if name is None:
            return None
        if name not in self:
            raise ValueError("unknown command")
        return self[name].resolve(arguments)
