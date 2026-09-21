import asyncio
import inspect
import json
import os
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from modict import modict

from the_framework.agent.runtime.protocol import CommandCompleted, CommandFailed, PromptRequest
from the_framework.agent.models.base import Base


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class ServerRestartIntent(Base):
    action_id: str = modict.field(required="always")
    source_command_id: str = modict.field(required="always")
    resume_instruction: str = modict.field(required="always")
    status: str = "awaiting_turn_completion"
    created_at: str = modict.field(required="always")
    committed_at: str | None = None
    resume_command_id: str | None = None
    resume_route: str = "agent"


class SystemControlService:
    """Durable turn-boundary system actions owned by the application server."""

    version = 1

    def __init__(
        self,
        path,
        deliver,
        restart=None,
        resume_realtime=None,
    ):
        self.path = Path(path)
        self.deliver = deliver
        self.restart = restart
        self.resume_realtime = resume_realtime
        self.intent = None
        self.error = None
        self.lock = asyncio.Lock()
        self.restart_task = None

    def _load(self):
        if not self.path.exists():
            return None
        document = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("version") != self.version:
            raise ValueError("unsupported system-control document")
        payload = dict(document.get("restart", {}))
        if "resume_route" not in payload:
            payload["resume_route"] = (
                "realtime_delegation"
                if str(payload.get("source_command_id") or "").startswith(
                    "realtime-delegation:"
                )
                else "agent"
            )
        intent = ServerRestartIntent(**payload)
        self._validate_intent(intent)
        return intent

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        document = {"version": self.version, "restart": dict(self.intent)}
        with temporary.open("w", encoding="utf-8") as file:
            os.chmod(temporary, 0o600)
            json.dump(document, file, indent=2, ensure_ascii=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, self.path)
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def _clear(self):
        self.intent = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            return
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    @staticmethod
    def _required_text(value, field, maximum):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must not be empty")
        value = value.strip()
        if len(value) > maximum:
            raise ValueError(f"{field} must contain at most {maximum} characters")
        return value

    @classmethod
    def _validate_intent(cls, intent):
        cls._required_text(intent.action_id, "action_id", 200)
        cls._required_text(intent.source_command_id, "source_command_id", 200)
        cls._required_text(intent.resume_instruction, "resume_instruction", 16_000)
        if intent.status not in {"awaiting_turn_completion", "restart_committed"}:
            raise ValueError("invalid server restart intent status")
        if intent.resume_route not in {"agent", "realtime_delegation"}:
            raise ValueError("invalid server restart resume route")

    async def start(self):
        try:
            intent = await asyncio.to_thread(self._load)
        except Exception as error:
            self.error = f"could not load system-control state: {error}"
            return
        self.intent = intent
        if intent is None:
            return
        if intent.status == "awaiting_turn_completion":
            # The originating turn never crossed its durable completion boundary.
            # Executing this stale request after a crash would recreate an orphan.
            await asyncio.to_thread(self._clear)
            return
        await self._deliver_resume(intent)

    async def handle(self, method, payload, *, command_id=None):
        if method != "reboot_server":
            raise ValueError(f"unsupported system method: {method}")
        if self.error is not None:
            raise RuntimeError(self.error)
        if self.restart is None:
            raise RuntimeError("server restart is unavailable without a process supervisor")
        if command_id is None:
            raise ValueError("server reboot requires an active agent command")
        payload = payload or {}
        action_id = self._required_text(payload.get("action_id"), "action_id", 200)
        instruction = payload.get("resume_instruction")
        if instruction is None:
            instruction = (
                "Le redémarrage demandé est terminé. Reprends naturellement le travail "
                "interrompu à partir du contexte de la conversation."
            )
        instruction = self._required_text(
            instruction,
            "resume_instruction",
            16_000,
        )
        intent = ServerRestartIntent(
            action_id=action_id,
            source_command_id=self._required_text(command_id, "command_id", 200),
            resume_instruction=instruction,
            resume_route=(
                "realtime_delegation"
                if command_id.startswith("realtime-delegation:")
                else "agent"
            ),
            created_at=utc_timestamp(),
        )
        async with self.lock:
            if self.intent is not None:
                replaceable_realtime_resume = (
                    self.intent.status == "restart_committed"
                    and self.intent.resume_route == "realtime_delegation"
                )
                if not replaceable_realtime_resume:
                    raise RuntimeError("a server restart is already pending")
                # A committed voice restart can outlive its ephemeral Realtime
                # call, either before binding or after a bound delegation loses
                # its terminal event. A new explicit restart supersedes that stale
                # resume instead of being blocked by transport-specific history.
                await asyncio.to_thread(self._clear)
            self.intent = intent
            try:
                await asyncio.to_thread(self._save)
            except Exception:
                self.intent = None
                raise
        return {
            "status": "server_restart_requested",
            "action_id": action_id,
            "restart_after_command_id": command_id,
        }

    async def observe(self, output):
        if not isinstance(output, (CommandCompleted, CommandFailed)):
            return
        async with self.lock:
            intent = self.intent
            if intent is None:
                return
            if intent.status == "restart_committed":
                if output.command.id == intent.resume_command_id:
                    await asyncio.to_thread(self._clear)
                return
            if output.command.id != intent.source_command_id:
                return
            completed = (
                isinstance(output, CommandCompleted)
                and output.command.status == "completed"
            )
            if not completed:
                await asyncio.to_thread(self._clear)
                return
            intent.status = "restart_committed"
            intent.committed_at = utc_timestamp()
            intent.resume_command_id = (
                None
                if intent.resume_route == "realtime_delegation"
                else f"system-resume-{intent.action_id}"
            )
            await asyncio.to_thread(self._save)
        self.restart_task = asyncio.create_task(self._perform_restart(dict(intent)))

    async def _perform_restart(self, intent):
        try:
            result = self.restart(intent)
            if inspect.isawaitable(result):
                await result
        except Exception as error:
            self.error = f"server restart failed: {error}"
            raise

    async def realtime_recovery_status(self):
        async with self.lock:
            intent = self.intent
            required = bool(
                intent is not None
                and intent.status == "restart_committed"
                and intent.resume_route == "realtime_delegation"
            )
            return {
                "resume_required": required,
                "action_id": intent.action_id if required else None,
                "resume_bound": bool(required and intent.resume_command_id),
            }

    async def bind_realtime_resume(self, action_id, command_id):
        async with self.lock:
            intent = self.intent
            if (
                intent is None
                or intent.status != "restart_committed"
                or intent.resume_route != "realtime_delegation"
                or intent.action_id != action_id
            ):
                return False
            intent.resume_command_id = self._required_text(command_id, "command_id", 200)
            await asyncio.to_thread(self._save)
            return True

    async def _deliver_resume(self, intent):
        if intent.resume_route == "realtime_delegation":
            if self.resume_realtime is None:
                self.error = "Realtime delegation resume is unavailable"
                return
            result = self.resume_realtime(dict(intent))
            if inspect.isawaitable(result):
                await result
            return
        body = (
            f'<server_restart action_id="{escape(intent.action_id)}" '
            f'committed_at="{escape(intent.committed_at or "")}">'
            f'<instruction>{escape(intent.resume_instruction)}</instruction>'
            "</server_restart>"
        )
        await self.deliver(PromptRequest(
            id=intent.resume_command_id,
            prompt=body,
            prompt_role="developer",
            prompt_kind="system_resume",
        ))
