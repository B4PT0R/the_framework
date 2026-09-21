// A display projection only: the server remains the conversation owner.
export function voiceCaptions(current, transcript) {
  if (!transcript) return current;
  const next = { ...current };
  if (transcript.final) delete next[transcript.role];
  else next[transcript.role] = transcript.completeText;
  return next;
}

export function conversationBusy(status) {
  return Boolean(status.foreground_active || status.foreground_pending > 0);
}

export function conversationCommand(output) {
  return output.command?.type === "prompt_request";
}

export function message(item) {
  if (item?.type !== "message" || !["user", "assistant"].includes(item.role))
    return null;
  return {
    id: item.id,
    role: item.role,
    text: item.content.map((part) => part.text || "").join(""),
    final: true,
  };
}

export function snapshot(current, page) {
  const live = new Map(current.map((item) => [item.id, item]));
  const committed = page.turns
    .flatMap((turn) => turn.items)
    .map(message)
    .filter(Boolean);
  const seen = new Set(committed.map((item) => item.id));
  const overlap = current.findIndex((item) => seen.has(item.id));
  const prefix = overlap < 0 ? [] : current.slice(0, overlap);
  return [
    ...prefix,
    ...committed.map((item) => {
      const pending = live.get(item.id);
      return pending &&
        (pending.final || pending.text.length > item.text.length)
        ? pending
        : item;
    }),
    ...current.slice(prefix.length).filter((item) => !seen.has(item.id)),
  ];
}

export function eventMessages(current, event) {
  if (
    ["agent.response_item.added", "response.output_item.done"].includes(
      event.type,
    )
  ) {
    const item = message(event.item);
    if (!item) return current;
    const found = current.some((value) => value.id === item.id);
    return found
      ? current.map((value) =>
          value.id === item.id ? { ...item, final: true } : value,
        )
      : [...current, { ...item, final: true }];
  }
  if (event.type !== "response.output_text.delta") return current;
  const found = current.find((item) => item.id === event.item_id);
  if (
    found?.final ||
    (found?.sequence !== undefined && event.sequence_number <= found.sequence)
  )
    return current;
  const item = {
    id: event.item_id,
    role: "assistant",
    text: (found?.text || "") + event.delta,
    sequence: event.sequence_number,
    final: false,
  };
  return found
    ? current.map((value) => (value.id === item.id ? item : value))
    : [...current, item];
}
