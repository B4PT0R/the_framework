import time
from pathlib import Path

from modict import modict

from ..models.base import Base


class Instruction(Base):
    name: str
    content: str
    scope: str = "general"
    expires_at: float | None = None

    @staticmethod
    def _accepts(scope, context):
        return scope == "general" or scope == context

    @classmethod
    def from_file(cls, path, name=None, scope="general", expires_at=None):
        return cls(
            name=name or Path(path).stem,
            content=Path(path).read_text().strip(),
            scope=scope,
            expires_at=expires_at,
        )

    @modict.validator("scope", mode="after")
    def validate_scope(self, value):
        if value not in {"general", "vocal", "agentic"}:
            raise ValueError(f"invalid instruction scope: {value}")
        return value


class Instructions(Base[str, Instruction]):
    def add(self, instruction):
        if isinstance(instruction, str):
            raise ValueError("an instruction name is required")
        if not isinstance(instruction, Instruction):
            instruction = Instruction(**instruction)
        if instruction.name in self:
            raise ValueError(f"duplicate instruction: {instruction.name}")
        self[instruction.name] = instruction
        return instruction

    def render(self, scope=None):
        return "\n\n".join(
            instruction.content
            for instruction in self.values()
            if instruction.content
            and (instruction.expires_at is None or time.time() < instruction.expires_at)
            and (scope is None or instruction._accepts(instruction.scope, scope))
        )
