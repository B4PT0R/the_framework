const record = (value) => value && typeof value === "object" && !Array.isArray(value) ? value : {};

// Transient captions only. Completed turns come from the canonical session.
export class RemoteRealtimeTranscriptAssembler {
  active = {};
  ingest(value) {
    const outer = record(value);
    const payload = record(outer.payload);
    const envelope = payload.type === "realtime_event" ? payload : outer;
    if (envelope.type !== "realtime_event") return null;
    const event = record(envelope.event);
    const { role } = event;
    if (!["user", "assistant"].includes(role)) return null;
    if (event.type === "voice.turn.completed") {
      delete this.active[role];
      return { role, final: true };
    }
    if (event.type !== "voice.transcript.updated" || typeof event.text !== "string" || !event.text || this.active[role] === event.text) return null;
    this.active[role] = event.text;
    return { role, completeText: event.text, final: false };
  }

  reset() { this.active = {}; }
}
