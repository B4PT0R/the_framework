import { connectRealtimeAudio } from "./realtime-audio.js";

// One explicit voice attempt. Stop waits for an in-flight handshake before
// deleting its server call, so a late answer cannot leave an orphan call.
export function voiceSession(
  api,
  audio,
  changed,
  report,
  connect = connectRealtimeAudio,
  { storage, storageKey = "voice-session" } = {},
) {
  let connection, attempt, stopping, callId, reconnecting, pendingReconnect;
  let saved;
  try { saved = JSON.parse(storage?.getItem(storageKey) || "null"); } catch { /* Storage may be unavailable. */ }
  let wanted = saved?.wanted === true;
  let suspended = wanted, suspension = 0, resuming;
  callId = saved?.callId;
  const remember = () => {
    try {
      if (wanted) storage?.setItem(storageKey, JSON.stringify({ wanted, callId }));
      else storage?.removeItem(storageKey);
    } catch { /* Voice still works when browser storage is disabled. */ }
  };
  const release = (id = callId) => id && api(`realtime/calls/current?call_id=${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  const close = () => {
    const peer = connection;
    connection = null;
    peer?.close();
  };
  async function stopCurrent({ releaseCall = true } = {}) {
    if (stopping) return stopping;
    if (!attempt && !connection) return;
    const previous = callId;
    attempt?.controller.abort();
    close();
    changed("stopping");
    stopping = (async () => {
      await attempt?.task;
      try {
        if (releaseCall) await release(previous);
      } catch (error) {
        report(error.message);
      } finally {
        stopping = null;
        changed(wanted && suspended ? "connecting" : "idle");
      }
    })();
    return stopping;
  }
  function startCurrent() {
    if (suspended || attempt || connection || stopping) return;
    const controller = new AbortController();
    callId = crypto.randomUUID();
    remember();
    changed("connecting");
    const current = { controller };
    attempt = current;
    current.task = (async () => {
      try {
        const result = await connect(
          {
            startRealtime: (offer_sdp) =>
              api("realtime/calls", { method: "POST", body: { offer_sdp, call_id: callId }, signal: controller.signal }),
            readyRealtime: () =>
              api("realtime/calls/current/ready", { method: "POST", body: { call_id: callId }, signal: controller.signal }),
          },
          audio,
          () => {
            void stopCurrent();
          },
          { signal: controller.signal },
        );
        if (controller.signal.aborted) result.close();
        else {
          connection = result;
          changed("live");
        }
      } catch (error) {
        if (["text_turn_active", "server_restarting"].includes(error.code)) suspended = true;
        if (error.name !== "AbortError" && !suspended) report(error.message);
        // Cleanup after failure, including failures after server call creation.
        if (!controller.signal.aborted) {
          try {
            await release();
          } catch (failure) {
            report(failure.message);
          }
          changed(wanted && suspended ? "connecting" : "idle");
        }
      } finally {
        if (attempt === current) attempt = null;
      }
    })();
    return current.task;
  }
  function start() {
    wanted = true;
    suspended = false;
    remember();
    return startCurrent();
  }
  function stop() {
    const wasWanted = wanted;
    wanted = false;
    remember();
    pendingReconnect = null;
    if (wasWanted && !attempt && !connection && !stopping) changed("idle");
    return stopCurrent({ releaseCall: !suspended });
  }
  function suspend(request) {
    if (request && request.call_id !== callId) return;
    suspended = true;
    suspension++;
    if (wanted) changed("connecting");
    return stopCurrent({ releaseCall: false });
  }
  function resume() {
    if (!wanted || !suspended) return;
    if (resuming) return resuming;
    const version = suspension;
    resuming = (async () => {
      await stopping;
      if (!wanted || version !== suspension) return;
      // On a simple network loss the old server may still own this call.
      // The correlation id makes deletion harmless on a replacement server.
      await release();
      if (!wanted || version !== suspension) return;
      suspended = false;
      await startCurrent();
    })().catch(error => { report(error.message); }).finally(() => { resuming = null; });
    return resuming;
  }
  async function prepareReload() {
    if (!wanted) return;
    await api("realtime/calls/current/suspend", { method: "POST", body: { call_id: callId } });
    await suspend();
  }
  function dispose() {
    return stopCurrent({ releaseCall: !suspended });
  }
  function reconnect(request) {
    if (!wanted || request.call_id !== callId) return;
    if (request.error) {
      report(request.error);
      return stop();
    }
    pendingReconnect = request.call_id;
    if (reconnecting) return reconnecting;
    reconnecting = (async () => {
      while (pendingReconnect) {
        const previous = pendingReconnect;
        pendingReconnect = null;
        if (!wanted || previous !== callId) continue;
        await stopCurrent();
        if (wanted && !suspended) await startCurrent();
      }
    })().finally(() => { reconnecting = null; });
    return reconnecting;
  }
  return { start, stop, reconnect, suspend, resume, prepareReload, dispose };
}
