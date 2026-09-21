import { test } from "node:test";
import assert from "node:assert/strict";
import { RemoteRealtimeTranscriptAssembler } from "./realtime-transcript.js";

test("live deltas remain captions and final turn has canonical correlation", () => {
  const assembler = new RemoteRealtimeTranscriptAssembler();
  const partial = assembler.ingest({
    type: "realtime_event", call_id: "call-1",
    event: { type: "input_transcript.added", item: { text: "Hello" } },
  });
  assert.equal(partial.completeText, "Hello");
  assert.equal(partial.final, false);
  assert.equal(assembler.ingest({
    type: "realtime_event", event: { type: "input_transcript.added", item: { text: "Hello" } },
  }), null);
  const final = assembler.ingest({
    type: "realtime_event", call_id: "call-1",
    event: { type: "turn.done", turn: { id: "turn-1", role: "user", transcript: "Hello there" } },
  });
  assert.equal(final.id, partial.id);
  assert.equal(final.completeText, "Hello there");
  assert.equal(final.realtimeTurnKey, "realtime:call-1:turn-1:user");
  assert.equal(final.final, true);
});
