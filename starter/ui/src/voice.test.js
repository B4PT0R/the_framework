import { test } from "node:test";
import assert from "node:assert/strict";
import { voiceSession } from "./voice.js";

test("start, ready and cleanup target the same unique call attempt", async () => {
  const requests = [];
  const voice = voiceSession(
    async (path, options) => { requests.push([path, options.body]); return {}; },
    {}, () => {}, assert.fail,
    async (transport) => {
      await transport.startRealtime("offer");
      await transport.readyRealtime();
      return { close() {} };
    },
  );
  await voice.start();
  await voice.stop();
  const id = requests[0][1].call_id;
  assert.ok(id);
  assert.equal(requests[1][1].call_id, id);
  assert.equal(requests[2][0], `realtime/calls/current?call_id=${id}`);
  await voice.start();
  assert.notEqual(requests[3][1].call_id, id);
  await voice.stop();
});

test("idle disposal never stops another server session", async () => {
  const voice = voiceSession(
    () => assert.fail("unexpected request"),
    {},
    () => assert.fail("unexpected state change"),
    assert.fail,
  );
  await voice.stop();
});

test("peer disconnect releases microphone and server exactly once", async () => {
  let disconnected,
    closed = 0;
  const requests = [],
    states = [];
  const voice = voiceSession(
    async (path) => requests.push(path),
    {},
    (state) => states.push(state),
    assert.fail,
    async (_transport, _audio, onDisconnect) => {
      disconnected = onDisconnect;
      return { close: () => closed++ };
    },
  );
  await voice.start();
  disconnected();
  await voice.stop();
  assert.equal(closed, 1);
  assert.equal(requests.length, 1);
  assert.match(requests[0], /^realtime\/calls\/current\?call_id=.+/);
  assert.deepEqual(states, ["connecting", "live", "stopping", "idle"]);
});

test("stopping during a delayed handshake closes the late peer and server call", async () => {
  let resolve,
    closed = 0;
  const states = [],
    requests = [];
  const pending = new Promise((done) => {
    resolve = done;
  });
  const voice = voiceSession(
    async (path, options) => requests.push([path, options.method]),
    {},
    (state) => states.push(state),
    (message) => assert.fail(message),
    () => pending,
  );
  voice.start();
  const stopping = voice.stop();
  assert.equal(requests.length, 0);
  resolve({ close: () => closed++ });
  await stopping;
  assert.equal(closed, 1);
  assert.equal(requests.length, 1);
  assert.match(requests[0][0], /^realtime\/calls\/current\?call_id=.+/);
  assert.equal(requests[0][1], "DELETE");
  assert.deepEqual(states, ["connecting", "stopping", "idle"]);
});

test("failed connection releases server state and reports the failure", async () => {
  const errors = [],
    states = [],
    requests = [];
  const voice = voiceSession(
    async (path) => requests.push(path),
    {},
    (state) => states.push(state),
    (error) => errors.push(error),
    async () => {
      throw new Error("Microphone unavailable");
    },
  );
  await voice.start();
  assert.deepEqual(errors, ["Microphone unavailable"]);
  assert.deepEqual(states, ["connecting", "idle"]);
  assert.equal(requests.length, 1);
  assert.match(requests[0], /^realtime\/calls\/current\?call_id=.+/);
});
