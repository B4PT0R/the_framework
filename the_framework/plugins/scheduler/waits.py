"""Persistent response deadlines; the scheduler owns timing, the agent owns policy."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone


def date(value):
    if not isinstance(value, str):
        raise ValueError("date must be an offset ISO datetime")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("date must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def text(value, field, maximum=16000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{field} must be nonempty text, at most {maximum} characters")
    return value.strip()


class WaitBook:
    def __init__(self, records=()):
        self.records = {}
        for record in records:
            item = deepcopy(record)
            if item["id"] in self.records or item["status"] not in {"waiting", "answered", "acknowledged", "timed_out", "cancelled"}:
                raise ValueError("invalid or duplicate scheduler wait")
            date(item["deadline_at"])
            self.records[item["id"]] = item

    def snapshot(self):
        return deepcopy(list(self.records.values()))

    def get(self, wait_id):
        if wait_id not in self.records:
            raise ValueError(f"unknown scheduler wait: {wait_id}")
        return self.records[wait_id]

    def create(self, wait_id, payload, now):
        allowed = {"name", "context", "timeout_seconds", "response_kind", "criticality", "alert"}
        if set(payload) - allowed:
            raise ValueError("unknown wait fields")
        if wait_id in self.records:
            raise ValueError("wait already exists")
        seconds = payload.get("timeout_seconds")
        if type(seconds) is not int or not 1 <= seconds <= 31 * 86400:
            raise ValueError("timeout_seconds must be 1..2678400")
        kind = payload.get("response_kind", "reply")
        if kind not in {"reply", "ack"}:
            raise ValueError("response_kind must be reply or ack")
        if kind == "reply" and any(r["status"] == "waiting" and r["response_kind"] == "reply" for r in self.records.values()):
            raise ValueError("only one next-reply wait may be active; resolve or cancel it first")
        criticality = payload.get("criticality", "normal")
        if criticality not in {"low", "normal", "critical"}:
            raise ValueError("criticality must be low, normal or critical")
        alert = payload.get("alert")
        start = now
        if alert is not None:
            if not isinstance(alert, dict) or set(alert) - {"at", "kind", "title", "body"}:
                raise ValueError("alert must contain at, kind, title and optional body")
            if alert.get("kind") not in {"message", "alarm"} or kind != "ack":
                raise ValueError("native alerts require response_kind ack and kind message or alarm")
            start = date(alert.get("at"))
            if start < now:
                raise ValueError("alert.at must be in the future")
            alert = {"at": stamp(start), "kind": alert["kind"],
                     "title": text(alert.get("title"), "alert.title", 200),
                     "body": text(alert.get("body", "Open your conversation for details."), "alert.body", 1000)}
        # Retain recent outcomes for late receipts, without an unbounded permanent log.
        cutoff = now - timedelta(days=30)
        self.records = {k: r for k, r in self.records.items()
                        if r["status"] == "waiting" or r.get("delivery_state") == "pending" or date(r["deadline_at"]) >= cutoff}
        if len(self.records) >= 512:
            raise ValueError("scheduler wait limit reached (512)")
        item = {"id": text(wait_id, "id", 200), "name": text(payload.get("name"), "name", 160),
                "context": text(payload.get("context"), "context"), "response_kind": kind,
                "criticality": criticality, "created_at": stamp(now), "starts_at": stamp(start),
                "deadline_at": stamp(start + timedelta(seconds=seconds)), "status": "waiting",
                "alert": alert, "receipts": {}, "response": None, "resolved_at": None,
                "delivery_state": None, "delivery_attempt": 0, "last_error": None}
        self.records[wait_id] = item
        return deepcopy(item)

    def resolve(self, wait_id, action, now, *, response=None, resume=True):
        item = self.get(wait_id)
        if action not in {"answered", "acknowledged", "cancelled"}:
            raise ValueError("action must be answered, acknowledged or cancelled")
        if action == "acknowledged" and item["response_kind"] != "ack":
            raise ValueError("this wait expects a reply, not an acknowledgement")
        if action == "answered" and item["response_kind"] != "reply":
            raise ValueError("this wait expects an acknowledgement")
        if action != "cancelled" and now < date(item["starts_at"]):
            raise ValueError("cannot answer an alert before its scheduled start")
        if action != "cancelled" and item["status"] == "waiting" and date(item["deadline_at"]) <= now:
            item.update(status="timed_out", resolved_at=stamp(now), delivery_state="pending")
        if item["status"] != "waiting":
            if action != "cancelled" and response is not None:
                item["late_response"] = {"at": stamp(now), "value": str(response)[:16000]}
            return deepcopy(item)
        item.update(status=action, resolved_at=stamp(now), response=str(response)[:16000] if response is not None else None,
                    delivery_state="pending" if resume and action != "cancelled" else "done")
        return deepcopy(item)

    def reply(self, response, now):
        for item in self.records.values():
            if item["status"] == "waiting" and item["response_kind"] == "reply":
                # The human prompt itself already resumes the agent; do not enqueue another turn.
                return self.resolve(item["id"], "answered", now, response=response, resume=False)

    def receipt(self, wait_id, client_id, event, now):
        if event not in {"armed", "fired", "acknowledged", "permission_denied", "error", "auto_stopped"}:
            raise ValueError("invalid alert receipt")
        item = self.get(wait_id)
        if item["alert"] is None:
            raise ValueError("wait has no native alert")
        client_id = text(client_id, "client_id", 200)
        if event in {"fired", "acknowledged", "auto_stopped"} and now < date(item["starts_at"]):
            raise ValueError("alert has not started")
        if client_id not in item["receipts"] and len(item["receipts"]) >= 32:
            raise ValueError("alert device limit reached")
        receipts = item["receipts"].setdefault(client_id, {})
        receipts.setdefault(event, stamp(now))
        if event == "acknowledged":
            self.resolve(wait_id, "acknowledged", now, response=f"Device {client_id}: user acknowledged alert")
        return deepcopy(item)

    def due(self, now):
        for item in self.records.values():
            if item["status"] == "waiting" and date(item["deadline_at"]) <= now:
                item.update(status="timed_out", resolved_at=stamp(now), delivery_state="pending")
        return [deepcopy(r) for r in self.records.values() if r["delivery_state"] == "pending"]

    @staticmethod
    def command_id(item):
        return f"scheduler-wait-{item['id']}-{item['status']}-{item['delivery_attempt']}"

    def observe(self, command_id, error):
        for item in self.records.values():
            if item["delivery_state"] == "pending" and self.command_id(item) == command_id:
                item["last_error"] = error
                if error == "interrupted" and item["delivery_attempt"] < 2:
                    item["delivery_attempt"] += 1
                else:
                    item["delivery_state"] = "failed" if error else "done"
                return True
        return False

    def alerts(self, now):
        return [{"id": r["id"], **r["alert"], "deadline_at": r["deadline_at"],
                 "criticality": r["criticality"], "cancelled": r["status"] != "waiting"}
                for r in self.records.values() if r["alert"] is not None
                and date(r["deadline_at"]) >= now - timedelta(days=1)]
