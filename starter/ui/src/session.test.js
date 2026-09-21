import { test } from "node:test";
import assert from "node:assert/strict";
import {
  conversationBusy,
  conversationCommand,
  eventMessages,
  snapshot,
  voiceCaptions,
} from "./session.js";

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
const delta = (text, sequence_number = 1) => ({
  type: "response.output_text.delta",
  item_id: "answer",
  delta: text,
  sequence_number,
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
    type: "response.output_item.done",
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
