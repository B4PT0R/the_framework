import { test } from "node:test";
import assert from "node:assert/strict";
import {
  conversationBusy,
  conversationCommand,
  eventMessages,
  providerArtifacts,
  snapshot,
  voiceCaptions,
  subagentActivities,
} from "../../src/session.js";

test("live voice captions replace drafts and disappear on canonical completion", () => {
  const user = voiceCaptions({}, { role: "user", completeText: "Hello", final: false });
  const both = voiceCaptions(user, { role: "assistant", completeText: "Hi", final: false });
  assert.deepEqual(both, { user: "Hello", assistant: "Hi" });
  const complete = voiceCaptions(both, { role: "assistant", completeText: "Hi there", final: true });
  assert.deepEqual(complete, { user: "Hello" });
  assert.deepEqual(both, { user: "Hello", assistant: "Hi" });
});

test("internal commands do not masquerade as conversational activity", () => {
  assert.equal(
    conversationBusy({
      active: { type: "config_update_request" },
      foreground_active: null,
      foreground_pending: 0,
    }),
    false,
  );
  assert.equal(conversationBusy({ foreground_pending: 1 }), true);
  assert.equal(
    conversationBusy({ foreground_active: { type: "prompt_request" } }),
    true,
  );
  assert.equal(
    conversationCommand({ command: { type: "plugin_binding_request" } }),
    false,
  );
  assert.equal(
    conversationCommand({ command: { type: "prompt_request" } }),
    true,
  );
});

test("older pages remain before the latest page after reconciliation", () => {
  let messages = snapshot([], page(item("latest", "Latest")));
  messages = snapshot(messages, page(item("older", "Older")));
  messages = snapshot(
    messages,
    page(item("latest", "Latest"), item("new", "New")),
  );
  assert.deepEqual(
    messages.map((value) => value.id),
    ["older", "latest", "new"],
  );
});

const item = (id, text, role = "assistant") => ({
  id,
  type: "message",
  role,
  content: [{ text }],
});
const page = (...items) => ({ turns: [{ items }] });
const delta = (text, sequence = 1) => ({
  type: "agent.text.delta",
  channel: "output",
  item_id: "answer",
  delta: text,
  sequence,
});

test("late history preserves streamed text and chronological order", () => {
  const live = eventMessages([], delta("Hello"));
  assert.deepEqual(
    snapshot(live, page(item("question", "Hi", "user"))).map((x) => x.text),
    ["Hi", "Hello"],
  );
});
test("completion replaces partial content exactly once", () => {
  let messages = eventMessages([], delta("Hello"));
  messages = eventMessages(messages, {
    type: "agent.response_item.added",
    item: item("answer", "Hello there"),
  });
  messages = eventMessages(messages, delta(" there", 2));
  messages = snapshot(messages, page(item("answer", "Hello there")));
  assert.equal(messages.length, 1);
  assert.equal(messages[0].text, "Hello there");
  assert.deepEqual(eventMessages(messages, delta(" there", 3)), messages);
});
test("duplicate deltas and provider messages do not pollute conversation", () => {
  const messages = eventMessages([], delta("Hello"));
  assert.deepEqual(eventMessages(messages, delta("Hello")), messages);
  assert.deepEqual(
    eventMessages(messages, {
      type: "agent.response_item.added",
      item: item("provider", "private", "developer"),
    }),
    messages,
  );
});
test("a stale snapshot cannot replace a newer committed message", () => {
  const messages = eventMessages([], {
    type: "agent.response_item.added",
    item: item("answer", "Complete answer"),
  });
  assert.deepEqual(
    snapshot(messages, page(item("answer", "Complete"))),
    messages,
  );
});
test("user commit and subsequent snapshot produce only one bubble", () => {
  const question = item("question", "Hello", "user");
  const messages = eventMessages([], {
    type: "agent.response_item.added",
    item: question,
  });
  assert.deepEqual(snapshot(messages, page(question)), messages);
});

test("artifacts keep their place through live events, replay and repeated snapshots", () => {
  const artifact = { type: "artifact", id: "figure", kind: "image", payload: { src: "/figure.png" } };
  let current = eventMessages([], { type: "agent.response_item.added", item: artifact });
  current = snapshot(current, page(item("question", "Show it", "user"), artifact));
  current = eventMessages(current, { type: "agent.response_item.added", item: artifact });
  current = snapshot(current, page(artifact, item("answer", "Here it is")));
  assert.deepEqual(current.map((value) => value.id), ["question", "figure", "answer"]);
  assert.equal(current[1].type, "artifact");
  assert.deepEqual(current[1].payload, artifact.payload);
  assert.deepEqual(snapshot([], page(artifact))[0], current[1]);
});

test("vision inputs never become chat presentations", () => {
  const vision = { type: "image", id: "private-screenshot", path: "/tmp/screenshot.png" };
  assert.deepEqual(snapshot([], page(vision)), []);
  assert.deepEqual(eventMessages([], { type: "agent.response_item.added", item: vision }), []);
});

test("provider artifacts replace this turn's view and never become replay history", () => {
  const first = { type: "artifact", id: "screen-1", kind: "image", payload: { src: "/1.png" } };
  const second = { ...first, id: "screen-2", payload: { src: "/2.png" } };
  let transient = providerArtifacts([], { type: "agent.provider_artifacts.updated", items: [first] });
  transient = providerArtifacts(transient, { type: "agent.provider_artifacts.updated", items: [second] });
  assert.deepEqual(transient.map((item) => item.id), ["screen-2"]);
  assert.deepEqual(eventMessages([], { type: "agent.provider_artifacts.updated", items: [first] }), []);
  for (const type of ["agent.turn.start", "agent.turn.end", "agent.interrupted", "agent.error"])
    assert.deepEqual(providerArtifacts(transient, { type }), []);
});


test("subagent activity is isolated by task and never becomes primary history", () => {
  const activity = (task_id, event) => ({type: "subagent_event", task_id, agent: "reviewer", session_id: "child", event});
  const first = activity("a", {type: "agent.tool_call.start", user_feedback: "Reading sources"});
  let state = subagentActivities({}, first);
  state = subagentActivities(state, activity("b", {type: "agent.turn.start"}));
  assert.equal(state.a.feedback, "Reading sources");
  assert.equal(state.a.sessionId, "child");
  assert.deepEqual(eventMessages([], first.event), []);
  state = subagentActivities(state, activity("a", {type: "agent.task.completed"}));
  assert.deepEqual(Object.keys(state), ["b"]);
  state = subagentActivities(state, activity("b", {type: "agent.task.failed"}));
  assert.deepEqual(state, {});
});
