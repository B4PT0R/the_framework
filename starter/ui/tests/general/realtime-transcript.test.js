import { test } from "node:test";
import assert from "node:assert/strict";
import { RemoteRealtimeTranscriptAssembler } from "../../src/realtime-transcript.js";

const caption = (text, role = "user") => ({ type: "realtime_event", event: {
  type: "voice.transcript.updated", role, text,
} });
const done = (role, transcript = "") => ({ type: "realtime_event", event: {
  type: "voice.turn.completed", role, transcript,
} });

test("captions consume cumulative Framework text without duplicate rendering", () => {
  const assembler = new RemoteRealtimeTranscriptAssembler();
  assert.deepEqual(assembler.ingest(caption("Hello")), { role: "user", completeText: "Hello", final: false });
  assert.equal(assembler.ingest(caption("Hello")), null);
  assert.equal(assembler.ingest(caption("Hello there")).completeText, "Hello there");
  assert.equal(assembler.ingest(caption("Hello there!")).completeText, "Hello there!");
  assert.equal(assembler.ingest(caption("Hi", "assistant")).completeText, "Hi");
  assert.deepEqual(assembler.ingest(done("user", "Hello there!")), { role: "user", final: true });
  assert.equal(assembler.ingest(caption("Next")).completeText, "Next");
  assert.equal(assembler.ingest(caption("Hi again", "assistant")).completeText, "Hi again");
});

test("empty completion still clears a caption; reset clears both speakers", () => {
  const assembler = new RemoteRealtimeTranscriptAssembler();
  assembler.ingest(caption("Unfinished"));
  assert.deepEqual(assembler.ingest(done("user")), { role: "user", final: true });
  assert.equal(assembler.ingest(caption("New")).completeText, "New");
  assembler.ingest(caption("Answer", "assistant"));
  assembler.reset();
  assert.equal(assembler.ingest(caption("Fresh", "assistant")).completeText, "Fresh");
});

test("only valid realtime transcript events affect captions, including wrapped events", () => {
  const assembler = new RemoteRealtimeTranscriptAssembler();
  for (const value of [null, [], {}, caption(42), caption(""), done("system"), { type: "command_event" }])
    assert.equal(assembler.ingest(value), null);
  assert.equal(assembler.ingest({ payload: caption("Wrapped") }).completeText, "Wrapped");
});
