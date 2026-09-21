"""Crash-safe discovery and single-instance ownership for the local server."""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import time
from pathlib import Path

SERVER_DESCRIPTOR_VERSION = 1
DEFAULT_SERVER_PORT = 48_887


class ServerAlreadyRunning(RuntimeError):
    pass


class PersistentSecretStore:
    """Private stable credential for browser sessions spanning server launches."""

    def __init__(self, path):
        self.path = Path(path)

    def load_or_create(self):
        try:
            value = self.path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            value = ""
        else:
            if not value:
                raise ValueError(f"persistent secret is empty: {self.path}")
            os.chmod(self.path, 0o600)
            return value
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        value = secrets.token_urlsafe(32)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                os.chmod(temporary, 0o600)
                stream.write(value + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, self.path)
            except FileExistsError:
                return self.load_or_create()
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)
        return value


def descriptor_path(session_path):
    return Path(session_path).expanduser().resolve().parent / "runtime" / "server.json"


class ServerLease:
    """Hold an advisory lock for one server bound to one runtime directory."""

    def __init__(self, path):
        self.path = Path(path)
        self.file = None

    def acquire(self):
        if self.file is not None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        file = self.path.open("a+")
        os.chmod(self.path, 0o600)
        try:
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            file.close()
            raise ServerAlreadyRunning(
                f"another server owns {self.path}"
            ) from error
        self.file = file
        return self

    def release(self):
        if self.file is None:
            return
        try:
            fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        finally:
            self.file.close()
            self.file = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_args):
        self.release()


class ServerDescriptorStore:
    def __init__(self, path):
        self.path = Path(path)

    def payload(self, *, host, port, token):
        return {
            "version": SERVER_DESCRIPTOR_VERSION,
            "host": str(host),
            "port": int(port),
            "token": str(token),
            "pid": os.getpid(),
            "started_at": int(time.time()),
        }

    def write(self, payload):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                os.chmod(temporary, 0o600)
                json.dump(payload, stream, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        return payload

    def read(self):
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("version") != SERVER_DESCRIPTOR_VERSION:
            raise ValueError("unsupported Harness server descriptor")
        if payload.get("host") != "127.0.0.1":
            raise ValueError("Harness server descriptor is not loopback-bound")
        if not isinstance(payload.get("port"), int) or not 0 < payload["port"] < 65536:
            raise ValueError("Harness server descriptor has an invalid port")
        if not isinstance(payload.get("token"), str) or not payload["token"]:
            raise ValueError("Harness server descriptor has no authentication token")
        return payload

    def remove_if_owned(self, payload):
        try:
            current = self.read()
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            return False
        if (
            current.get("pid") != payload.get("pid")
            or current.get("token") != payload.get("token")
        ):
            return False
        self.path.unlink(missing_ok=True)
        return True
