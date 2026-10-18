import { connectRealtimeAudio } from "./realtime-audio.js";

// One explicit voice attempt. Stop waits for an in-flight handshake before
// deleting its server call, so a late answer cannot leave an orphan call.
export function voiceSession(
  api,
  audio,
  changed,
  report,
  connect = connectRealtimeAudio,
) {
  let connection, attempt, stopping, callId, reconnecting, pendingReconnect;
  let wanted = false;
  const release = () => api(`realtime/calls/current?call_id=${encodeURIComponent(callId)}`, {
    method: "DELETE",
  });
  const close = () => {
    const peer = connection;
    connection = null;
    peer?.close();
  };
  async function stopCurrent() {
    if (stopping) return stopping;
    if (!attempt && !connection) return;
    attempt?.controller.abort();
    close();
    changed("stopping");
    stopping = (async () => {
      await attempt?.task;
      try {
        await release();
      } catch (error) {
        report(error.message);
      } finally {
        stopping = null;
        changed("idle");
      }
    })();
    return stopping;
  }
  function startCurrent() {
    if (attempt || connection || stopping) return;
    const controller = new AbortController();
    callId = crypto.randomUUID();
    changed("connecting");
    const current = { controller };
    attempt = current;
    current.task = (async () => {
      try {
        const result = await connect(
          {
            startRealtime: (offer_sdp) =>
              api("realtime/calls", { method: "POST", body: { offer_sdp, call_id: callId } }),
            readyRealtime: () =>
              api("realtime/calls/current/ready", { method: "POST", body: { call_id: callId } }),
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
        if (error.name !== "AbortError") report(error.message);
        // Cleanup after failure, including failures after server call creation.
        if (!controller.signal.aborted) {
          try {
            await release();
          } catch (failure) {
            report(failure.message);
          }
          changed("idle");
        }
      } finally {
        if (attempt === current) attempt = null;
      }
    })();
    return current.task;
  }
  function start() {
    wanted = true;
    return startCurrent();
  }
  function stop() {
    wanted = false;
    pendingReconnect = null;
    return stopCurrent();
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
        if (wanted) await startCurrent();
      }
    })().finally(() => { reconnecting = null; });
    return reconnecting;
  }
  return { start, stop, reconnect };
}
