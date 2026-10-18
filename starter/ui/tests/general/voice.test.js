import { test } from "node:test";
import assert from "node:assert/strict";
import { voiceSession } from "../../src/voice.js";

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

test("reconnect is owned by one call and duplicate requests do not open extra peers", async () => {
  // Capture the client correlation IDs independently of the server transport.
  const requests = [];
  const client = voiceSession(async (path, options) => {
    if (path === "realtime/calls") requests.push(options.body.call_id);
    return {};
  }, {}, () => {}, assert.fail, async (transport) => {
    await transport.startRealtime("offer");
    return { close() {} };
  });
  await client.start();
  await client.reconnect({ call_id: "another-client" });
  assert.equal(requests.length, 1);
  const previous = requests[0];
  await Promise.all([client.reconnect({ call_id: previous }), client.reconnect({ call_id: previous })]);
  assert.equal(requests.length, 2);
  assert.notEqual(requests[1], previous);
  await client.stop();
});

test("manual stop wins over an automatic reconnect during cleanup", async () => {
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const calls = [];
  const voice = voiceSession(async (path, options) => {
    if (path === "realtime/calls") calls.push(options.body.call_id);
    if (options.method === "DELETE") await gate;
    return {};
  }, {}, () => {}, assert.fail, async (transport) => {
    await transport.startRealtime("offer");
    return { close() {} };
  });
  await voice.start();
  const reconnecting = voice.reconnect({ call_id: calls[0] });
  const stopped = voice.stop();
  release();
  await Promise.all([reconnecting, stopped]);
  assert.equal(calls.length, 1);
});

test("a server-closed peer can reconnect, but a manually stopped call cannot", async () => {
  let disconnected;
  const calls = [];
  const voice = voiceSession(async (path, options) => {
    if (path === "realtime/calls") calls.push(options.body.call_id);
    return {};
  }, {}, () => {}, assert.fail, async (transport, _audio, onDisconnect) => {
    disconnected = onDisconnect;
    await transport.startRealtime("offer");
    return { close() {} };
  });
  await voice.start();
  disconnected();
  await voice.reconnect({ call_id: calls[0] });
  assert.equal(calls.length, 2);
  await voice.stop();
  await voice.reconnect({ call_id: calls[1] });
  assert.equal(calls.length, 2);
});

test("a binding change during the next handshake is reconciled once that peer connects", async () => {
  let release, entered;
  const waiting = new Promise(resolve => { entered = resolve; });
  const gate = new Promise(resolve => { release = resolve; });
  const calls = [];
  const voice = voiceSession(async (path, options) => {
    if (path === "realtime/calls") calls.push(options.body.call_id);
    return {};
  }, {}, () => {}, assert.fail, async (transport) => {
    await transport.startRealtime("offer");
    if (calls.length === 2) { entered(); await gate; }
    return { close() {} };
  });
  await voice.start();
  const reconnecting = voice.reconnect({ call_id: calls[0] });
  await waiting;
  const next = voice.reconnect({ call_id: calls[1] });
  release();
  await Promise.all([reconnecting, next]);
  assert.equal(new Set(calls).size, 3);
  await voice.stop();
});

test("failed reconciliation reports an error and does not create another call", async () => {
  const errors = [], calls = [];
  const voice = voiceSession(async (path, options) => {
    if (path === "realtime/calls") calls.push(options.body.call_id);
    return {};
  }, {}, () => {}, error => errors.push(error), async (transport) => {
    await transport.startRealtime("offer");
    return { close() {} };
  });
  await voice.start();
  await voice.reconnect({ call_id: calls[0], error: "Worker unavailable" });
  assert.deepEqual(errors, ["Worker unavailable"]);
  assert.equal(calls.length, 1);
});


test("a server reset during a failed handshake can still request the next call", async () => {
  const calls = [], errors = [];
  const voice = voiceSession(async (path, options) => {
    if (path === "realtime/calls") calls.push(options.body.call_id);
    return {};
  }, {}, () => {}, message => errors.push(message), async (transport) => {
    await transport.startRealtime("offer");
    if (calls.length === 2) throw new Error("Call was replaced while connecting");
    return { close() {} };
  });
  await voice.start();
  await voice.reconnect({ call_id: calls[0] });
  assert.equal(calls.length, 2);
  await voice.reconnect({ call_id: calls[1] });
  assert.equal(calls.length, 3);
  assert.deepEqual(errors, ["Call was replaced while connecting"]);
  await voice.stop();
});

test("server loss preserves voice intent and resumes exactly once after reconciliation", async () => {
  const calls = [], states = [];
  let closed = 0;
  const voice = voiceSession(async (path, options) => {
    calls.push([path, options]); return {};
  }, {}, state => states.push(state), assert.fail, async transport => {
    await transport.startRealtime("offer");
    return { close() { closed++; } };
  });
  await voice.start();
  const previous = calls[0][1].body.call_id;
  await voice.suspend();
  assert.equal(closed, 1);
  assert.equal(calls.length, 1, "no HTTP cleanup against the unavailable server");
  await Promise.all([voice.resume(), voice.resume()]);
  assert.equal(calls[1][0], `realtime/calls/current?call_id=${previous}`);
  assert.equal(calls.filter(([path]) => path === "realtime/calls").length, 2);
  assert.equal(states.at(-1), "live");
  await voice.stop();
  await voice.suspend();
  await voice.resume();
  assert.equal(calls.filter(([path]) => path === "realtime/calls").length, 2);
});

test("stopping during recovery cancels the pending automatic call", async () => {
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  let count = 0;
  const voice = voiceSession(async (_path, options) => {
    if (options.method === "DELETE") await gate;
    return {};
  }, {}, () => {}, assert.fail, async () => { count++; return { close() {} }; });
  await voice.start();
  await voice.suspend();
  const recovery = voice.resume();
  await voice.stop();
  release();
  await recovery;
  assert.equal(count, 1);
});

test("UI publication drains voice and restores its owning tab after reload", async () => {
  const values = new Map(), calls = [];
  const storage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
  const create = () => voiceSession(async (path, options) => {
    calls.push([path, options]); return {};
  }, {}, () => {}, assert.fail, async transport => {
    await transport.startRealtime("offer"); return { close() {} };
  }, { storage });
  const first = create();
  await first.start();
  const original = calls[0][1].body.call_id;
  await first.prepareReload();
  assert.deepEqual(calls[1], ["realtime/calls/current/suspend", { method: "POST", body: { call_id: original } }]);
  await first.dispose();
  const next = create();
  await Promise.all([next.resume(), next.resume()]);
  assert.equal(calls.filter(([path]) => path === "realtime/calls").length, 2);
  await next.stop();
  const stopped = create();
  await stopped.resume();
  assert.equal(calls.filter(([path]) => path === "realtime/calls").length, 2);
  assert.equal(values.size, 0);
});

test("a failed graceful suspension keeps the old call available instead of forcing reload", async () => {
  let closed = 0;
  const voice = voiceSession(async path => {
    if (path.endsWith('/suspend')) throw new Error('Turn still active');
    return {};
  }, {}, () => {}, assert.fail, async () => ({ close() { closed++; } }));
  await voice.start();
  await assert.rejects(voice.prepareReload(), /Turn still active/);
  assert.equal(closed, 0);
  await voice.stop();
});


test("agent controls start once and only stop the matching call", async () => {
  let opened = 0, closed = 0;
  const requests = [];
  const voice = voiceSession(
    async (path, options) => { requests.push([path, options]); return {}; },
    {}, () => {}, assert.fail,
    async (transport) => {
      opened++;
      await transport.startRealtime("offer");
      return { close() { closed++; } };
    },
  );
  await voice.control({ action: "start" });
  await voice.control({ action: "start" });
  assert.equal(opened, 1);
  const id = requests[0][1].body.call_id;
  await voice.control({ action: "stop", call_id: "stale" });
  assert.equal(closed, 0);
  await voice.control({ action: "stop", call_id: id });
  assert.equal(closed, 1);
  await voice.control({ action: "start" });
  await voice.control({ action: "stop", call_id: id });
  assert.equal(closed, 1);
  await voice.stop();
  assert.equal(closed, 2);
});
