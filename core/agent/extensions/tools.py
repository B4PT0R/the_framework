import re

from modict import modict

from .schema import Param, Parameters
from ..models.base import Base
from .schema import parse_function_specs

USER_FEEDBACK_PARAMETER = "user_feedback"
USER_FEEDBACK_DESCRIPTION = (
    "Brief user-facing sentence of roughly ten words explaining the immediate purpose "
    "of this call in the first person, for example: 'Je vérifie la configuration du projet'."
)


def with_user_feedback(func):
    """Mark a function tool as requiring user-facing activity feedback."""
    func.agent_user_feedback = True
    return func


def tool(func):
    """Expose a function tool with typed, optionally multiple outputs.

    Return ``None`` for no contextual output, a tuple for multiple outputs, a
    ``ToolOutput`` or ``Image`` explicitly, or any JSON-compatible value for
    one automatically wrapped ``ToolOutput``. Lists are JSON values, not
    multiple-output containers.
    """
    with_user_feedback(func)
    func.agent_tool = True
    return func


class Tool(Base):
    type: str


class FunctionTool(Tool):
    type = "function"
    name: str
    description: str
    parameters: Parameters
    strict: bool = True
    defer_loading: bool | None = None

    @classmethod
    def from_function(cls, func, *, defer_loading=None):
        specs = parse_function_specs(func)
        parameters = Parameters.from_dict(specs["parameters"])
        if USER_FEEDBACK_PARAMETER in parameters.properties:
            raise ValueError(
                f"{USER_FEEDBACK_PARAMETER} is reserved for function-tool activity feedback"
            )
        parameters.properties[USER_FEEDBACK_PARAMETER] = Param(
            type="string",
            description=USER_FEEDBACK_DESCRIPTION,
        )
        parameters.required = list(parameters.properties)
        tool = cls(
            name=specs["name"],
            description=specs["description"],
            parameters=parameters,
            defer_loading=defer_loading,
        )
        # Keep the callable out of the payload, since it cannot be serialized.
        tool.set_attr("function", func)
        return tool

    def __call__(self, *args, **kwargs):
        if not self.has_attr("function"):
            raise ValueError("No function associated with this tool")
        return self.function(*args, **kwargs)


class ServerTool(Tool):
    type: str


class HybridTool(Tool):
    type: str


class WebSearchTool(ServerTool):
    type = "web_search"
    search_context_size: str | None = None
    user_location: object | None = None
    filters: object | None = None
    external_web_access: bool | None = None


class FileSearchTool(ServerTool):
    type = "file_search"
    vector_store_ids: list[str]
    max_num_results: int | None = None
    filters: object | None = None


class ImageGenerationTool(ServerTool):
    type = "image_generation"
    size: str | None = None
    quality: str | None = None
    format: str | None = None
    compression: int | None = None
    background: str | None = None
    action: str | None = None


class CodeInterpreterTool(ServerTool):
    type = "code_interpreter"
    container: object | None = None


class McpTool(ServerTool):
    type = "mcp"
    server_label: str
    server_url: str | None = None
    server_description: str | None = None
    connector_id: str | None = None
    authorization: str | None = None
    require_approval: object | None = None
    allowed_tools: list[str] | None = None


class ShellTool(HybridTool):
    type = "shell"
    environment: object


class ComputerUseTool(HybridTool):
    type = "computer_use_preview"
    display_width: int
    display_height: int
    environment: str


class ApplyPatchTool(HybridTool):
    type = "apply_patch"


class LocalShellTool(HybridTool):
    type = "local_shell"


class NamespaceTool(Tool):
    type = "namespace"
    name: str
    description: str
    tools: list[Tool] = modict.factory(list)

    @modict.validator("name", mode="after")
    def validate_name(self, value):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError("namespace must match [A-Za-z0-9_-]+")
        return value

    def add(self, tool_or_func):
        tool = _build_tool(tool_or_func)
        name = tool.get("name", tool.type)
        if self.find(name) is not None:
            raise ValueError(f"duplicate tool in namespace {self.name}: {name}")
        self.tools.append(tool)
        return tool

    def find(self, name):
        return next(
            (
                tool
                for tool in self.tools
                if tool.get("name", tool.type) == name
            ),
            None,
        )


class ToolSearchTool(HybridTool):
    type = "tool_search"
    description: str | None = None
    execution: str | None = None
    parameters: object | None = None


def _build_tool(tool_or_func):
    if isinstance(tool_or_func, Tool):
        return tool_or_func
    if callable(tool_or_func):
        return FunctionTool.from_function(tool_or_func)
    raise ValueError("Must be a Tool instance or a callable")


class Tools(Base[str, Tool]):
    def add(self, tool_or_func):
        tool = _build_tool(tool_or_func)
        name = tool.get("name", tool.type)
        if name in self:
            raise ValueError(f"duplicate tool or namespace: {name}")
        self[name] = tool
        return tool

    def list(self):
        return list(self.values())

    def search(self, arguments=None):
        query = (arguments or {}).get("query")
        if query is None:
            return [
                tool
                for tool in self.values()
                if not isinstance(tool, ToolSearchTool)
            ]
        query = query.lower()
        results = []
        for tool in self.values():
            if isinstance(tool, ToolSearchTool):
                continue
            if not isinstance(tool, NamespaceTool):
                if self.matches(tool, query):
                    results.append(tool)
                continue
            matches = tool.tools if self.matches(tool, query) else [
                nested
                for nested in tool.tools
                if self.matches(nested, query)
            ]
            if matches:
                results.append(NamespaceTool(
                    name=tool.name,
                    description=tool.description,
                    tools=matches,
                ))
        return results

    def matches(self, tool, query):
        return (
            query in tool.get("name", tool.type).lower()
            or query in tool.get("description", "").lower()
        )

    def resolve(self, name, namespace=None):
        if namespace is not None:
            container = self.get(namespace)
            if not isinstance(container, NamespaceTool):
                raise ValueError(f"unknown tool namespace: {namespace}")
            tool = container.find(name)
            if tool is None:
                raise ValueError(f"unknown tool: {namespace}.{name}")
            return tool
        if name in self and not isinstance(self[name], NamespaceTool):
            return self[name]
        matches = [
            tool.find(name)
            for tool in self.values()
            if isinstance(tool, NamespaceTool) and tool.find(name) is not None
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"ambiguous tool without namespace: {name}")
        raise ValueError(f"unknown tool: {name}")
