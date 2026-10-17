import test from "node:test";
import assert from "node:assert/strict";

import { pluginRunning } from "../../src/plugins.js";

test("optional interface controls follow installed plugin runtimes", () => {
  assert.equal(pluginRunning([], "realtime"), false);
  assert.equal(pluginRunning([{ name: "realtime", running: false }], "realtime"), false);
  assert.equal(pluginRunning([{ name: "realtime", running: true }], "realtime"), true);
});
