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
  let connection, attempt, stopping, callId;
  const release = () => api(`realtime/calls/current?call_id=${encodeURIComponent(callId)}`, {
    method: "DELETE",
  });
  const close = () => {
    connection?.close();
    connection = null;
  };
  async function stop() {
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
  function start() {
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
            void stop();
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
  return { start, stop };
}
