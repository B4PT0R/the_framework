from the_framework.agent import Config, Plugin, WebSearchTool


class WebSearchConfig(Config):
    search_context_size: str = "medium"
    external_web_access: bool = True


class WebSearchPlugin(Plugin):
    """Expose the backend-hosted Responses web search tool."""

    name = "web_search"
    description = "Current web research through the backend-hosted Responses tool."
    config = WebSearchConfig
    instructions_file = "instructions.md"

    def load(self):
        if self.loaded:
            return self
        super().load()
        if self.config.search_context_size not in {"low", "medium", "high"}:
            raise ValueError(
                "web_search.search_context_size must be low, medium, or high"
            )
        if not isinstance(self.config.external_web_access, bool):
            raise ValueError("web_search.external_web_access must be a boolean")
        tool = WebSearchTool(
            search_context_size=self.config.search_context_size,
            external_web_access=self.config.external_web_access,
        )
        for optional in ("user_location", "filters"):
            tool.pop(optional, None)
        self._contributions.append(("tools", tool))
        return self
