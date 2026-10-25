// Public per-page identity, not a credential. Reloads never restore media work.
let instance;
export function clientInstance() {
  return instance ||= crypto.randomUUID();
}

export function clientEventsUrl(value) {
  const url = new URL(value);
  url.searchParams.set("client_instance", clientInstance());
  return url.href;
}

function audioSource(src) {
  if (typeof src !== "string" || !src.startsWith("/api/v1/bucket/") || /[\\\s?#%]/u.test(src)) return null;
  const url = new URL(src, "https://client.invalid");
  return url.pathname.startsWith("/api/v1/bucket/") && url.pathname === src ? src : null;
}

// No widget, history playback, OS player, cross-client fallback or permission bypass.
export function clientOutput({ send, onError, createAudio = () => new Audio() }) {
  let current = null;
  const seen = new Set();
  const reply = (id, status) => send({ type: "client_operation_result", id, status });
  function stop() {
    const item = current;
    current = null;
    if (!item) return;
    item.audio.pause();
    item.audio.removeAttribute("src");
    item.audio.load();
  }
  async function handle(event) {
    if (event.type === "client_deactivated") { stop(); return; }
    if (event.type !== "client_operation" || event.operation !== "audio.play") return;
    if (typeof event.id !== "string" || seen.has(event.id)) return;
    seen.add(event.id);
    // Bounded duplicate suppression for this live connection only.
    if (seen.size > 256) seen.delete(seen.values().next().value);
    const src = audioSource(event.payload?.src);
    if (!src) { reply(event.id, "failed"); return; }
    if (current) { reply(event.id, "busy"); return; }
    const audio = createAudio();
    const item = { audio, id: event.id };
    current = item;
    audio.preload = "none";
    audio.src = src;
    audio.onended = () => { if (current === item) stop(); };
    audio.onerror = () => {
      if (current !== item) return;
      stop();
      if (!item.started) reply(event.id, "failed");
      onError("Audio playback failed.");
    };
    try {
      await audio.play();
      if (current === item) { item.started = true; reply(event.id, "playing"); }
      else { audio.pause(); reply(event.id, "cancelled"); }
    } catch (error) {
      if (current !== item) return;
      stop();
      const blocked = error.name === "NotAllowedError";
      reply(event.id, blocked ? "blocked" : "failed");
      onError(blocked ? "Direct audio playback was blocked by this browser. Use the audio player if available." : "Audio playback failed.");
    }
  }
  return { handle, stop };
}
