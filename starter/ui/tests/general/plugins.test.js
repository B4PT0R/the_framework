import test from "node:test";
import assert from "node:assert/strict";

import { pluginBindingEnabled } from "../../src/plugins.js";

test("optional interface controls require a running service and an enabled binding", () => {
  assert.equal(pluginBindingEnabled([], "realtime"), false);
  assert.equal(pluginBindingEnabled([{ name: "realtime", running: false, binding_enabled: true }], "realtime"), false);
  assert.equal(pluginBindingEnabled([{ name: "realtime", running: true, binding_enabled: true }], "realtime"), true);
});

test("an installed voice runtime does not expose a disabled agent binding", () => {
  assert.equal(pluginBindingEnabled([
    { name: "realtime", running: true, binding_enabled: false },
  ], "realtime"), false);
});
