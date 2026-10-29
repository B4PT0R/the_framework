"""Contracts shared by the HTTP and worker-bridge entry points."""

from modict import modict


def identifier(value):
    if not value or len(value) > 64 or not all(
        char.isascii() and (char.isalnum() or char in "-_") for char in value
    ):
        raise ValueError("use 1–64 ASCII letters, digits, hyphens or underscores")
    return value


class SaveNote(modict):
    _config = modict.config(strict=True, extra="forbid")
    project: str
    key: str
    title: str
    body: str

    @modict.model_validator(mode="after")
    def validate_note(self):
        identifier(self.project)
        identifier(self.key)
        if not self.title.strip() or len(self.title) > 200:
            raise ValueError("title must contain 1–200 characters and not be blank")
        if len(self.body) > 20_000:
            raise ValueError("body must not exceed 20000 characters")


class RecentNotes(modict):
    _config = modict.config(strict=True, extra="forbid")
    project: str
    limit: int = 5

    @modict.validator("project", mode="after")
    def valid_project(self, value):
        return identifier(value)

    @modict.validator("limit", mode="after")
    def bounded_limit(self, value):
        if not 1 <= value <= 20:
            raise ValueError("limit must be between 1 and 20")
        return value
