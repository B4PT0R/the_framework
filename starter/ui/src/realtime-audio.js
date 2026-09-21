// Browser-owned WebRTC transport. The Python server remains the authority for
// starting and ending the call; this module only owns media tracks and the peer.
const aborted = () => new DOMException("Realtime connection cancelled", "AbortError");
const ensureActive = (signal) => {
  if (signal?.aborted) throw aborted();
};

const waitForIce = (peer, signal, timeoutMs = 4_000) => new Promise((resolve, reject) => {
  if (peer.iceGatheringState === "complete") return resolve();
  const timeout = window.setTimeout(done, timeoutMs);
  function cleanup() {
    window.clearTimeout(timeout);
    peer.removeEventListener("icegatheringstatechange", changed);
    signal?.removeEventListener("abort", cancelled);
  }
  function done() { cleanup(); resolve(); }
  function cancelled() { cleanup(); reject(aborted()); }
  function changed() { if (peer.iceGatheringState === "complete") done(); }
  signal?.addEventListener("abort", cancelled, { once: true });
  peer.addEventListener("icegatheringstatechange", changed);
});

const waitForPeer = (peer, signal, timeoutMs = 12_000) => new Promise((resolve, reject) => {
  if (peer.connectionState === "connected") return resolve();
  const timeout = window.setTimeout(() => done(new Error("Realtime audio connection timed out")), timeoutMs);
  function cleanup() {
    window.clearTimeout(timeout);
    peer.removeEventListener("connectionstatechange", changed);
    signal?.removeEventListener("abort", cancelled);
  }
  function done(error) { cleanup(); if (error) reject(error); else resolve(); }
  function cancelled() { done(aborted()); }
  function changed() {
    if (peer.connectionState === "connected") done();
    else if (["failed", "closed"].includes(peer.connectionState))
      done(new Error("Realtime audio connection failed"));
  }
  signal?.addEventListener("abort", cancelled, { once: true });
  peer.addEventListener("connectionstatechange", changed);
});

export async function connectRealtimeAudio(transport, audio, onDisconnected, options = {}) {
  const { signal } = options;
  ensureActive(signal);
  const microphone = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  let peer, remote, disconnectTimer, closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    if (disconnectTimer !== undefined) window.clearTimeout(disconnectTimer);
    disconnectTimer = undefined;
    if (peer) {
      peer.onconnectionstatechange = null;
      peer.ontrack = null;
      for (const sender of peer.getSenders()) void sender.replaceTrack(null).catch(() => undefined);
      peer.close();
    }
    for (const track of microphone.getTracks()) track.stop();
    for (const track of remote?.getTracks() ?? []) track.stop();
    audio.pause();
    audio.srcObject = null;
  };
  try {
    ensureActive(signal);
    peer = new RTCPeerConnection({ iceServers: [], bundlePolicy: "max-bundle" });
    remote = new MediaStream();
    audio.autoplay = true;
    audio.setAttribute("playsinline", "");
    audio.srcObject = remote;
    peer.ontrack = (event) => {
      if (closed || !remote) return;
      const tracks = event.streams[0]?.getTracks() ?? [event.track];
      for (const track of tracks) {
        if (!remote.getTracks().some((current) => current.id === track.id)) remote.addTrack(track);
      }
      void audio.play().catch(() => undefined);
    };
    peer.createDataChannel("oai-events", { ordered: true });
    for (const track of microphone.getTracks()) peer.addTrack(track, microphone);
    await peer.setLocalDescription(await peer.createOffer());
    await waitForIce(peer, signal, options.iceTimeoutMs);
    ensureActive(signal);
    const offerSdp = peer.localDescription?.sdp;
    if (!offerSdp) throw new Error("Unable to create Realtime audio offer");
    const answer = await transport.startRealtime(offerSdp);
    ensureActive(signal);
    await peer.setRemoteDescription({ type: "answer", sdp: answer.answer_sdp });
    await waitForPeer(peer, signal, options.peerTimeoutMs);
    ensureActive(signal);
    await transport.readyRealtime();
    ensureActive(signal);
    const activePeer = peer;
    peer.onconnectionstatechange = () => {
      if (closed) return;
      if (activePeer.connectionState === "connected") {
        if (disconnectTimer !== undefined) window.clearTimeout(disconnectTimer);
        disconnectTimer = undefined;
      } else if (activePeer.connectionState === "disconnected") {
        if (disconnectTimer !== undefined) return;
        disconnectTimer = window.setTimeout(() => {
          disconnectTimer = undefined;
          if (!closed && activePeer.connectionState === "disconnected") onDisconnected();
        }, options.disconnectGraceMs ?? 2_500);
      } else if (["failed", "closed"].includes(activePeer.connectionState)) onDisconnected();
    };
    return { peer, microphone, close };
  } catch (error) {
    close();
    throw error;
  }
}
