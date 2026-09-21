const record = (value) => value && typeof value === "object" && !Array.isArray(value) ? value : {};

// Ephemeral captions only. Completed turns are rendered from the canonical
// server session, not copied from this browser-side assembler.
export class RemoteRealtimeTranscriptAssembler {
  sequence = 0;
  active = {};

  begin(role) {
    const turn = { id: `voice-${role}-${++this.sequence}`, text: "" };
    this.active[role] = turn;
    return turn;
  }

  ingest(value) {
    const outer = record(value);
    const payload = record(outer.payload);
    const envelope = payload.type === "realtime_event" ? payload : outer;
    if (envelope.type !== "realtime_event") return null;
    const event = record(envelope.event);
    if (event.type === "turn.done") {
      const turn = record(event.turn);
      const { role } = turn;
      if (role !== "user" && role !== "assistant") return null;
      const current = this.active[role] ?? this.begin(role);
      delete this.active[role];
      const text = typeof turn.transcript === "string" ? turn.transcript.trim() : "";
      if (!text) return null;
      return {
        id: current.id, role, text, completeText: text, mode: "replace", final: true,
        realtimeTurnKey: typeof envelope.call_id === "string" && typeof turn.id === "string" && turn.id
          ? `realtime:${envelope.call_id}:${turn.id}:${role}` : undefined,
      };
    }
    const role = event.type === "input_transcript.added" ? "user"
      : event.type === "output_transcript.added" ? "assistant" : null;
    if (role === null) return null;
    const item = record(event.item);
    if (typeof item.text !== "string" || !item.text) return null;
    const current = this.active[role] ?? this.begin(role);
    const incoming = item.text;
    let delta = incoming;
    if (incoming.startsWith(current.text)) delta = incoming.slice(current.text.length);
    else if (current.text.endsWith(incoming)) delta = "";
    current.text += delta;
    if (!delta) return null;
    return { id: current.id, role, text: delta, completeText: current.text, mode: "append", final: false };
  }

  reset() { this.active = {}; }
}
