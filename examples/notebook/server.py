"""Server-side SQLite storage, shared by authenticated HTTP and worker calls."""

import asyncio
import sqlite3
from threading import RLock

from the_framework import endpoint

from .models import RecentNotes, SaveNote


class NotebookService:
    def __init__(self, context, runtime):
        self.path = context.require("session_path").parent / "notebook.sqlite3"
        self.lock = RLock()
        self.database = None
        runtime.bridge.register("notebook", self.handle)

    def start(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        database = sqlite3.connect(self.path, check_same_thread=False)
        try:
            database.execute("""
                CREATE TABLE IF NOT EXISTS notes (
                    project TEXT NOT NULL, key TEXT NOT NULL,
                    title TEXT NOT NULL, body TEXT NOT NULL,
                    revision INTEGER NOT NULL, PRIMARY KEY (project, key)
                )
            """)
            database.commit()
        except BaseException:
            database.close()
            raise
        self.database = database

    def stop(self):
        with self.lock:
            if self.database is not None:
                self.database.close()
                self.database = None

    def health(self):
        return {"status": "ready" if self.database is not None else "unhealthy"}

    @endpoint("put", "/api/v1/notebook/notes", request=SaveNote)
    def save(self, project, key, title, body):
        note = SaveNote(project=project, key=key, title=title, body=body)
        with self.lock, self.database:
            revision = self.database.execute(
                "SELECT COALESCE(MAX(revision), 0) + 1 FROM notes"
            ).fetchone()[0]
            self.database.execute("""
                INSERT INTO notes VALUES (:project, :key, :title, :body, :revision)
                ON CONFLICT(project, key) DO UPDATE SET
                    title=excluded.title, body=excluded.body, revision=excluded.revision
            """, {**note, "revision": revision})
        return {"project": project, "key": key, "revision": revision}

    @endpoint("get", "/api/v1/notebook/notes", request=RecentNotes)
    def recent(self, project, limit=5):
        query = RecentNotes(project=project, limit=limit)
        with self.lock:
            rows = self.database.execute(
                "SELECT key, title, body, revision FROM notes "
                "WHERE project = ? ORDER BY revision DESC LIMIT ?",
                (query.project, query.limit),
            ).fetchall()
        return {"project": project, "notes": [
            dict(zip(("key", "title", "body", "revision"), row)) for row in rows
        ]}

    async def handle(self, method, payload):
        if method == "save":
            values, handler = SaveNote(**payload), self.save
        elif method == "recent":
            values, handler = RecentNotes(**payload), self.recent
        else:
            raise ValueError(f"unknown notebook operation: {method}")
        return await asyncio.to_thread(handler, **values)
