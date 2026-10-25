import test from "node:test";
import assert from "node:assert/strict";
import { clientOutput, clientInstance, clientEventsUrl } from "../../src/client-output.js";

function setup(play = async () => {}) {
  const sent = [], errors = [], audio = {
    play, pause() { this.paused = true; }, removeAttribute() { this.src = ""; }, load() {},
  };
  let creations = 0;
  const output = clientOutput({ send: (v) => sent.push(v), onError: (v) => errors.push(v), createAudio: () => { creations++; return audio; } });
  const event = { type: "client_operation", operation: "audio.play", id: "one", payload: { src: "/api/v1/bucket/audio.wav" } };
  return { output, event, sent, errors, audio, creations: () => creations };
}

test("page identity is stable and event URLs preserve existing parameters", () => {
  assert.equal(clientInstance(), clientInstance());
  const url = new URL(clientEventsUrl("wss://host/remote/v1/ui-events?other=1"));
  assert.equal(url.searchParams.get("client_instance"), clientInstance());
  assert.equal(url.searchParams.get("other"), "1");
});

test("only a direct live operation plays; duplicates/history/artefacts do not", async () => {
  const s = setup();
  await s.output.handle({ type: "artifact", kind: "audio", payload: s.event.payload });
  assert.equal(s.creations(), 0);
  await s.output.handle(s.event);
  assert.equal(s.creations(), 1);
  assert.equal(s.audio.src, s.event.payload.src);
  assert.equal(s.sent[0].status, "playing");
  await s.output.handle(s.event);
  assert.equal(s.creations(), 1);
  await s.output.handle({ ...s.event, id: "two" });
  assert.equal(s.sent.at(-1).status, "busy");
  await s.output.handle({ type: "client_deactivated" });
  assert.equal(s.audio.paused, true);
  assert.equal(s.audio.src, "");
});

test("browser block is reported without fallback or retry", async () => {
  const s = setup(async () => { throw Object.assign(new Error("blocked"), { name: "NotAllowedError" }); });
  await s.output.handle(s.event);
  assert.equal(s.sent[0].status, "blocked");
  assert.equal(s.errors.length, 1);
  assert.equal(s.audio.paused, true);
  assert.equal(s.creations(), 1);
});

test("unsafe sources fail before creating audio", async () => {
  for (const src of ["https://elsewhere/audio.wav", "//elsewhere/x", "javascript:x", "/api/v1/bucket/../x", "/api/v1/bucket/%2e%2e/x", "/api/v1/bucket/x?x=y"]) {
    const s = setup();
    await s.output.handle({ ...s.event, payload: { src } });
    assert.equal(s.creations(), 0, src);
    assert.equal(s.sent[0].status, "failed");
  }
});

test("disconnect or handoff cancels an in-flight play without a late playing receipt", async () => {
  let finish;
  const s = setup(() => new Promise((resolve) => { finish = resolve; }));
  const pending = s.output.handle(s.event);
  s.output.stop();
  finish();
  await pending;
  assert.deepEqual(s.sent.map(v => v.status), ["cancelled"]);
  assert.equal(s.audio.paused, true);
});
