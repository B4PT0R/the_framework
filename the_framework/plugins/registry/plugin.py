import json
import math
from copy import deepcopy
from pathlib import Path
from threading import RLock

from modict import Path as ModictPath
from modict import modict

from ...agent.models.config import Config
from ...agent.extensions.plugin import AgentPlugin
from ...utils.persistence import atomic_text_writer
from ...agent.extensions.providers import provider
from ...agent.models.responses import ToolOutput
from ...utils.tokens import token_count
from ...agent.extensions.tools import tool


class RegistryConfig(Config):
    max_tokens: int = 16_000
    max_depth: int = 32
    max_file_bytes: int = 1_000_000

    @staticmethod
    def _positive_limit(value):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("registry limits must be positive integers")
        return value

    @modict.validator("max_tokens", mode="after")
    def validate_max_tokens(self, value):
        return self._positive_limit(value)

    @modict.validator("max_depth", mode="after")
    def validate_max_depth(self, value):
        return self._positive_limit(value)

    @modict.validator("max_file_bytes", mode="after")
    def validate_max_file_bytes(self, value):
        return self._positive_limit(value)


class RegistryStore:
    """One atomically persisted, JSON-only modict document."""

    def __init__(self, path, *, max_tokens, max_depth, max_file_bytes, model):
        self.path = Path(path)
        self.max_tokens = max_tokens
        self.max_depth = max_depth
        self.max_file_bytes = max_file_bytes
        self.model = model
        self.lock = RLock()
        self.data = modict()
        self.load_error = None
        self.revision = 0
        self._load()

    def configure(self, *, max_tokens, max_depth, max_file_bytes, model):
        self.max_tokens = max_tokens
        self.max_depth = max_depth
        self.max_file_bytes = max_file_bytes
        self.model = model

    def _load(self):
        if not self.path.exists():
            return
        try:
            if self.path.is_symlink():
                raise ValueError("registry file must not be a symbolic link")
            if self.path.stat().st_size > self.max_file_bytes:
                raise ValueError(
                    f"registry file exceeds {self.max_file_bytes} bytes"
                )
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            self.data = self._validated(payload)
        except Exception as error:
            self.data = modict()
            self.load_error = f"could not load {self.path.name}: {error}"

    def _check_value(self, value, *, depth=0, ancestors=None):
        if depth > self.max_depth:
            raise ValueError(f"registry exceeds maximum depth {self.max_depth}")
        if value is None or isinstance(value, (str, bool, int)):
            return
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("registry numbers must be finite")
            return
        if not isinstance(value, (dict, list)):
            raise TypeError(
                f"registry contains a non-JSON value: {type(value).__name__}"
            )
        ancestors = set() if ancestors is None else ancestors
        identity = id(value)
        if identity in ancestors:
            raise ValueError("registry must not contain cyclic containers")
        nested_ancestors = {*ancestors, identity}
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise TypeError("registry object keys must be strings")
                self._check_value(
                    child,
                    depth=depth + 1,
                    ancestors=nested_ancestors,
                )
            return
        for child in value:
            self._check_value(
                child,
                depth=depth + 1,
                ancestors=nested_ancestors,
            )

    def _serialized(self, value, *, pretty=False):
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
        )

    def _validated(self, value):
        if not isinstance(value, dict):
            raise TypeError("registry root must be a JSON object")
        self._check_value(value)
        compact = self._serialized(value)
        size = len((self._serialized(value, pretty=True) + "\n").encode("utf-8"))
        if size > self.max_file_bytes:
            raise ValueError(
                f"registry requires {size} bytes; limit is {self.max_file_bytes}"
            )
        tokens = token_count(compact, model=self.model)
        if tokens > self.max_tokens:
            raise ValueError(
                f"registry requires {tokens} tokens; limit is {self.max_tokens}"
            )
        return modict(deepcopy(value))

    def _persist(self, candidate):
        candidate = self._validated(candidate)
        text = self._serialized(candidate, pretty=True) + "\n"
        with atomic_text_writer(self.path) as stream:
            stream.write(text)
        self.data = candidate
        self.load_error = None
        self.revision += 1
        return self.summary()

    def _require_available(self):
        if self.load_error is not None:
            raise RuntimeError(
                f"registry is unavailable ({self.load_error}); replace the whole "
                "document explicitly to recover it"
            )

    @staticmethod
    def _path(path):
        path = str(path or "").strip()
        if not path:
            raise ValueError("registry path must not be empty")
        if len(path) > 512:
            raise ValueError("registry path must not exceed 512 characters")
        return ModictPath(path)

    @staticmethod
    def _container_factory(full_path):
        keys = tuple(full_path)

        def factory(current_path):
            child_index = len(current_path)
            return [] if isinstance(keys[child_index], int) else {}

        return factory

    def snapshot(self):
        with self.lock:
            return deepcopy(self.data)

    def summary(self):
        compact = self._serialized(self.data)
        return {
            "status": "ready",
            "path": str(self.path),
            "tokens": token_count(compact, model=self.model),
            "max_tokens": self.max_tokens,
            "revision": self.revision,
        }

    def get(self, path):
        with self.lock:
            self._require_available()
            parsed = self._path(path)
            value = self.data if not parsed else self.data.get_nested(parsed)
            return deepcopy(value)

    def _set(self, candidate, path, value, *, create_missing):
        parsed = self._path(path)
        if not parsed:
            if not isinstance(value, dict):
                raise TypeError("registry root must be replaced with a JSON object")
            return modict(deepcopy(value))
        candidate.set_nested(
            parsed,
            deepcopy(value),
            create_missing=create_missing,
            container_factory=self._container_factory(parsed),
        )
        return candidate

    def set(self, path, value, *, create_missing=True):
        with self.lock:
            self._require_available()
            candidate = self._set(
                deepcopy(self.data),
                path,
                value,
                create_missing=create_missing,
            )
            return self._persist(candidate)

    def append(self, path, value):
        with self.lock:
            self._require_available()
            candidate = deepcopy(self.data)
            parsed = self._path(path)
            target = candidate if not parsed else candidate.get_nested(parsed)
            if not isinstance(target, list):
                raise TypeError(f"registry path {parsed} does not contain a list")
            target.append(deepcopy(value))
            return self._persist(candidate)

    def delete(self, path):
        with self.lock:
            self._require_available()
            candidate = deepcopy(self.data)
            parsed = self._path(path)
            if not parsed:
                raise ValueError(
                    "cannot delete the registry root; explicitly replace it with {}"
                )
            removed = candidate.pop_nested(parsed)
            result = self._persist(candidate)
            return {**result, "removed_type": type(removed).__name__}

    def apply(self, edits):
        if not isinstance(edits, list) or not edits:
            raise ValueError("registry edits must be a non-empty list")
        with self.lock:
            self._require_available()
            candidate = deepcopy(self.data)
            for index, edit in enumerate(edits):
                if not isinstance(edit, dict):
                    raise TypeError(f"registry edit {index} must be an object")
                operation = edit.get("op")
                path = edit.get("path")
                if operation == "set":
                    if "value" not in edit:
                        raise ValueError(f"registry set edit {index} requires value")
                    candidate = self._set(
                        candidate,
                        path,
                        edit["value"],
                        create_missing=edit.get("create_missing", True),
                    )
                elif operation == "append":
                    if "value" not in edit:
                        raise ValueError(f"registry append edit {index} requires value")
                    parsed = self._path(path)
                    target = candidate if not parsed else candidate.get_nested(parsed)
                    if not isinstance(target, list):
                        raise TypeError(
                            f"registry path {parsed} does not contain a list"
                        )
                    target.append(deepcopy(edit["value"]))
                elif operation == "delete":
                    parsed = self._path(path)
                    if not parsed:
                        raise ValueError("registry batch cannot delete the root")
                    candidate.pop_nested(parsed)
                else:
                    raise ValueError(
                        f"registry edit {index} has unsupported op: {operation!r}"
                    )
            result = self._persist(candidate)
            return {**result, "edits_applied": len(edits)}

    def replace(self, content):
        with self.lock:
            return self._persist(content)


class RegistryPlugin(AgentPlugin):
    name = "registry"
    description = "Persistent structured notes with atomic JSONPath mutations."
    config = RegistryConfig
    instruction_scope = "agentic"
    provider_channels = ("text", "realtime")
    instructions_file = "instructions.md"

    def __init__(self, agent, *, runtime_root=None):
        super().__init__(agent)
        session_path = getattr(getattr(agent, "session", None), "path", None)
        self.runtime_root = Path(
            runtime_root or (Path(session_path).parent if session_path else Path.cwd())
        ).expanduser().resolve()
        self.store = None

    def load(self):
        if self.loaded:
            return self
        super().load()
        self.store = RegistryStore(
            self.runtime_root / "registry.json",
            max_tokens=self.config.max_tokens,
            max_depth=self.config.max_depth,
            max_file_bytes=self.config.max_file_bytes,
            model=self.agent.configs.model,
        )
        return self

    def _store(self):
        if self.store is None:
            raise RuntimeError("registry plugin has not been loaded")
        self.store.configure(
            max_tokens=self.config.max_tokens,
            max_depth=self.config.max_depth,
            max_file_bytes=self.config.max_file_bytes,
            model=self.agent.configs.model,
        )
        return self.store

    @staticmethod
    def _provider_text(payload):
        # Keep arbitrary registry strings inside the provider's XML boundary.
        return json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).replace(
            "&", "\\u0026"
        ).replace(
            "<", "\\u003c"
        ).replace(
            ">", "\\u003e"
        )

    @staticmethod
    def _decode_json(value, *, label):
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError(f"{label} must contain valid JSON: {error}") from error

    @staticmethod
    def _mutation_output(result, operations):
        return ToolOutput(
            output=result,
            registry_delta={
                "revision": result["revision"],
                "operations": deepcopy(operations),
            },
        )

    @provider
    def persistent_registry(self):
        """Complete structured registry, refreshed before every agent step."""
        store = self._store()
        if store.load_error is not None:
            return self._provider_text({
                "status": "unavailable",
                "error": store.load_error,
                "recovery": "Use registry.replace_registry to replace the document.",
            })
        return self._provider_text({
            **store.summary(),
            "content": store.snapshot(),
        })

    @tool
    def set_value(
        self,
        path: str,
        value_json: str,
        create_missing: bool = True,
    ):
        """Set one JSON-encoded value at an exact JSONPath and persist atomically."""
        value = self._decode_json(value_json, label="value_json")
        canonical_path = str(ModictPath(path))
        result = {
            **self._store().set(path, value, create_missing=create_missing),
            "updated_path": canonical_path,
        }
        return self._mutation_output(result, [{
            "op": "set",
            "path": canonical_path,
            "value": value,
        }])

    @tool
    def append_value(self, path: str, value_json: str):
        """Append one JSON-encoded value to the list at an exact JSONPath."""
        value = self._decode_json(value_json, label="value_json")
        canonical_path = str(ModictPath(path))
        result = {
            **self._store().append(path, value),
            "updated_path": canonical_path,
        }
        return self._mutation_output(result, [{
            "op": "append",
            "path": canonical_path,
            "value": value,
        }])

    @tool
    def delete_value(self, path: str):
        """Delete one exact JSONPath and persist the remaining registry atomically."""
        canonical_path = str(ModictPath(path))
        result = {
            **self._store().delete(path),
            "deleted_path": canonical_path,
        }
        return self._mutation_output(result, [{
            "op": "delete",
            "path": canonical_path,
        }])

    @tool
    def apply_edits(self, edits_json: str):
        """Apply one JSON-encoded atomic batch of JSONPath edits.

        The decoded value must be an array of objects with `op` (`set`, `append`,
        or `delete`), `path`, and a `value` for set/append. A set may include
        `create_missing`.
        """
        edits = self._decode_json(edits_json, label="edits_json")
        result = self._store().apply(edits)
        operations = []
        for edit in edits:
            operation = {
                "op": edit["op"],
                "path": str(ModictPath(edit["path"])),
            }
            if edit["op"] in {"set", "append"}:
                operation["value"] = edit["value"]
            operations.append(operation)
        return self._mutation_output(result, operations)

    @tool
    def replace_registry(self, content_json: str):
        """Explicitly replace the registry with one JSON-encoded object."""
        content = self._decode_json(content_json, label="content_json")
        result = self._store().replace(content)
        return self._mutation_output(result, [{
            "op": "replace",
            "path": "$",
            "value": content,
        }])
