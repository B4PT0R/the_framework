import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { RemoteRealtimeTranscriptAssembler } from "./realtime-transcript.js";
import { api } from "./api";
import { voiceSession } from "./voice";
import { pluginBindingEnabled } from "./plugins";
import { Settings } from "./settings";
import { Artifact } from "./artifact";
import {
  conversationBusy,
  conversationCommand,
  eventMessages,
  providerArtifacts,
  snapshot,
  voiceCaptions,
  subagentActivities,
} from "./session";
import "./style.css";

const preview = document.querySelector('meta[name="framework-client-role"]')?.content === 'preview';

function App({ artifactRenderers }) {
  const [messages, setMessages] = useState([]);
  const [artifacts, setArtifacts] = useState([]);
  const [before, setBefore] = useState(null);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const historyInitialized = useRef(false);
  const historyScroll = useRef(null);
  const [draft, setDraft] = useState("");
  const [files, setFiles] = useState([]);
  const [sending, setSending] = useState(false);
  const fileInput = useRef(null);
  const [connection, setConnection] = useState("Connecting");
  const [activity, setActivity] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [settings, setSettings] = useState(false);
  const [voiceState, setVoiceState] = useState("idle");
  const [voiceAvailable, setVoiceAvailable] = useState(false);
  const [captions, setCaptions] = useState({});
  const [subagents, setSubagents] = useState({});
  const voice = useRef(null);
  useEffect(() => {
    const controller = new AbortController();
    api("plugins", { signal: controller.signal })
      .then((result) => {
        setVoiceAvailable(pluginBindingEnabled(result.plugins, "realtime"));
      })
      .catch((cause) => {
        if (cause.name !== "AbortError") setError(cause.message);
      });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (!voiceAvailable || preview) return undefined;
    let mounted = true;
    const session = voiceSession(
      api,
      new Audio(),
      (state) => {
        if (mounted) {
          setVoiceState(state);
          if (state === "idle") setCaptions({});
        }
      },
      (message) => {
        if (mounted) setError(message);
      },
      undefined,
      { storage: window.sessionStorage, storageKey: "starter:voice" },
    );
    voice.current = session;
    if (connection === "Connected") {
      api("status").then(status => {
        if (mounted && !conversationBusy(status)) return session.resume();
      }).catch(cause => { if (mounted) setError(cause.message); });
    }
    return () => {
      mounted = false;
      void session.dispose();
      voice.current = null;
    };
  }, [voiceAvailable]);
  const scroll = useRef(null);
  const follow = useRef(true);
  const lastScrollTop = useRef(0);
  function followLatest() {
    const el = scroll.current;
    if (follow.current && el) {
      el.scrollTop = el.scrollHeight;
      lastScrollTop.current = el.scrollTop;
    }
  }
  useEffect(() => {
    let disposed = false,
      socket,
      transportReady = false,
      retry,
      voiceCallId,
      attempts = 0,
      revision = 0;
    const controller = new AbortController();
    const transcripts = new RemoteRealtimeTranscriptAssembler();
    async function reconcile() {
      const requested = ++revision;
      const [page, status] = await Promise.all([
        api("session", { signal: controller.signal }),
        api("status", { signal: controller.signal }),
      ]);
      if (!disposed && requested === revision) {
        setMessages((items) => snapshot(items, page));
        if (!historyInitialized.current) {
          setBefore(page.has_more ? page.next_before : null);
          historyInitialized.current = true;
        }
        setBusy(conversationBusy(status));
        if (transportReady && !conversationBusy(status)) void voice.current?.resume();
      }
    }
    function connect() {
      const active = new WebSocket(
        `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/v1/events`,
      );
      socket = active;
      const current = () => !disposed && socket === active;
      active.onopen = () => {
        if (!current()) return;
        attempts = 0;
        transportReady = true;
        setSubagents({});
        setConnection("Connected");
        reconcile().catch(fail);
      };
      active.onmessage = ({ data }) => {
        if (!current()) return;
        const output = JSON.parse(data);
        if (["worker_exited", "worker_transport_error"].includes(output.type)) setSubagents({});
        if (output.type === "subagent_event") setSubagents((items) => subagentActivities(items, output));
        if (output.type === "realtime_control_requested") {
          void voice.current?.control(output);
        }
        if (output.type === "realtime_control_failed") setError(output.error);
        if (output.type === "realtime_restarting") {
          transportReady = false;
          void voice.current?.suspend(output);
        }
        if (output.type === "realtime_reconnect_requested") {
          void voice.current?.reconnect(output);
        }
        if (output.type === "command_event" && output.event?.type === "agent.plugin.binding.updated" && output.event.plugin === "realtime") {
          if (!output.event.enabled) void voice.current?.stop();
          setVoiceAvailable(Boolean(output.event.enabled));
        }
        if (output.type === "realtime_event" && output.call_id !== voiceCallId) {
          voiceCallId = output.call_id;
          transcripts.reset();
          setCaptions({});
        }
        const transcript = transcripts.ingest(output);
        if (transcript) setCaptions((current) => voiceCaptions(current, transcript));
        if (output.type === "interface_refresh_requested") {
          Promise.resolve(voice.current?.prepareReload()).then(() => location.reload()).catch(fail);
          return;
        }
        if (output.type === "command_event") {
          const event = output.event;
          setArtifacts((items) => providerArtifacts(items, event));
          setMessages((items) => eventMessages(items, event));
          if (event.user_feedback) setActivity(event.user_feedback);
        }
        if (output.type === "worker_status") {
          setBusy(conversationBusy(output));
          if (transportReady && !conversationBusy(output)) void voice.current?.resume();
        }
        if (output.type === "command_accepted" && conversationCommand(output))
          setBusy(true);
        if (
          ["command_completed", "command_failed"].includes(output.type) &&
          conversationCommand(output)
        ) {
          setArtifacts([]);
          if (output.error) setError(output.error);
          setActivity("");
          reconcile().catch(fail);
        }
      };
      active.onclose = () => {
        if (!current()) return;
        transportReady = false;
        setArtifacts([]);
        void voice.current?.suspend();
        transcripts.reset();
        setCaptions({});
        revision++;
        setConnection("Reconnecting");
        retry = setTimeout(
          connect,
          Math.min(10000, 500 * 2 ** attempts++) + Math.random() * 300,
        );
      };
      active.onerror = () => active.close();
    }
    function fail(error) {
      if (!disposed && error.name !== "AbortError") setError(error.message);
    }
    connect();
    return () => {
      disposed = true;
      controller.abort();
      clearTimeout(retry);
      socket?.close();
    };
  }, []);
  useLayoutEffect(() => {
    followLatest();
  }, [messages, artifacts, activity, captions, subagents]);
  useLayoutEffect(() => {
    if (historyScroll.current && scroll.current) {
      const { height, top } = historyScroll.current;
      scroll.current.scrollTop = top + scroll.current.scrollHeight - height;
      historyScroll.current = null;
    }
  }, [messages]);
  async function loadHistory() {
    if (!before || loadingHistory) return;
    setLoadingHistory(true);
    try {
      const page = await api(`session?before=${encodeURIComponent(before)}`);
      follow.current = false;
      historyScroll.current = {
        height: scroll.current.scrollHeight,
        top: scroll.current.scrollTop,
      };
      setMessages((items) => snapshot(items, page));
      setBefore(page.has_more ? page.next_before : null);
    } catch (error) {
      setError(error.message);
    } finally {
      setLoadingHistory(false);
    }
  }
  async function send(event) {
    event.preventDefault();
    if (preview || sending || (!draft.trim() && !files.length)) return;
    const text = draft;
    setError("");
    if (voiceState === "idle") setBusy(true);
    setSending(true);
    try {
      const id = crypto.randomUUID();
      if (files.length) {
        const body = new FormData();
        body.append("id", id);
        body.append("prompt", text);
        files.forEach((file) => body.append("files", file));
        await api("agent/attachments", { method: "POST", body });
      } else {
        await api("agent/prompt", {
          method: "POST",
          body: { id, prompt: text },
        });
      }
      setDraft("");
      setFiles([]);
      follow.current = true;
    } catch (error) {
      setError(error.message);
      setBusy(false);
    } finally {
      setSending(false);
    }
  }
  return (
    <div className="app">
      <header className="topbar">
        <div>
          <strong>Pandora</strong>{preview && <span className="preview-badge">Preview · read only</span>}
          <span className="connection">{connection}</span>
        </div>
        <button onClick={() => setSettings((value) => !value)}>Settings</button>
      </header>
      <div className="workspace">
        <main>
          <section
            className="conversation"
            ref={scroll}
            aria-label="Conversation"
            onLoadCapture={followLatest}
            onScroll={() => {
              const el = scroll.current;
              // Layout growth alone is not a request to leave follow mode.
              if (el.scrollHeight - el.scrollTop - el.clientHeight < 80) follow.current = true;
              else if (el.scrollTop < lastScrollTop.current) follow.current = false;
              lastScrollTop.current = el.scrollTop;
            }}
          >
            {before && (
              <button onClick={loadHistory} disabled={loadingHistory}>
                {loadingHistory ? "Loading…" : "Earlier messages"}
              </button>
            )}
            {!messages.length && (
              <div className="welcome">
                <span className="eyebrow">YOUR LOCAL WORKSPACE</span>
                <h1>
                  Start with a conversation.
                  <br />
                  Build what comes next.
                </h1>
                <p>
                  A persistent assistant, an editable interface, and a toolkit
                  you can shape together.
                </p>
              </div>
            )}
            {messages.map((message) => message.type === "artifact" ? (
              <Artifact key={message.id} {...message} renderers={artifactRenderers} />
            ) : (
              <article key={message.id} className={`message ${message.role}`}>
                <small>{message.role === "user" ? "You" : "Assistant"}</small>
                <div>{message.text}</div>
              </article>
            ))}
            {artifacts.map((item) => <Artifact key={`provider-${item.id}`} {...item} temporary renderers={artifactRenderers} />)}
            {Object.entries(subagents).map(([task, value]) => <aside key={task} className="message" role="status">
              <small>Subagent · {value.agent}</small><p>{value.feedback || "Working…"}</p>
            </aside>)}
            {Object.entries(captions).map(([role, text]) => (
              <article key={role} className={`message ${role}`}>
                <small>{role === "user" ? "You" : "Assistant"} · Live</small>
                <div>{text}</div>
              </article>
            ))}
          </section>
          <div className="composer-area">
            {error && (
              <div className="error" role="alert">
                {error}
                <button onClick={() => setError("")}>Dismiss</button>
              </div>
            )}
            <div className="activity" role="status">
              {busy ? activity || "Working…" : "Ready when you are"}
            </div>
            <form className="composer" onSubmit={send}>
              {!!files.length && (
                <div className="attachments">
                  {files.map((file, index) => (
                    <button
                      type="button"
                      key={index}
                      disabled={sending}
                      aria-label={`Remove ${file.name}`}
                      onClick={() =>
                        setFiles((items) => items.filter((_, i) => i !== index))
                      }
                    >
                      {file.name} ×
                    </button>
                  ))}
                </div>
              )}
              <label className="sr-only" htmlFor="message">
                Message
              </label>
              <textarea
                id="message"
                placeholder="What would you like to work on?"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                rows={3}
                disabled={sending}
              />
              <div className="composer-actions">
                <span>Local · Persistent · Yours</span>
                <input
                  ref={fileInput}
                  type="file"
                  multiple
                  hidden
                  onChange={(event) => {
                    const selected = [...event.target.files];
                    setFiles((items) => [...items, ...selected]);
                    event.target.value = "";
                  }}
                />
                <button
                  type="button"
                  disabled={sending || voiceState !== "idle"}
                  onClick={() => fileInput.current.click()}
                >
                  Attach
                </button>
                {voiceAvailable && (
                  <button
                    type="button"
                    disabled={
                      preview || voiceState === "stopping" ||
                      (voiceState === "idle" &&
                        (busy ||
                          sending ||
                          files.length > 0 ||
                          connection !== "Connected"))
                    }
                    onClick={() =>
                      voiceState === "idle"
                        ? voice.current.start()
                        : voice.current.stop()
                    }
                  >
                    {voiceState === "idle"
                      ? "Voice"
                      : voiceState === "connecting"
                        ? "Cancel voice"
                        : voiceState === "stopping"
                          ? "Stopping…"
                          : "End voice"}
                  </button>
                )}
                {busy && (
                  <button
                    type="button"
                    disabled={preview}
                    onClick={() =>
                      api("agent/interrupt", {
                        method: "POST",
                        body: {},
                      }).catch((e) => setError(e.message))
                    }
                  >
                    Stop
                  </button>
                )}
                <button
                  className="primary"
                  disabled={
                    preview || sending ||
                    (!draft.trim() && !files.length) ||
                    connection !== "Connected"
                  }
                >
                  Send
                </button>
              </div>
            </form>
          </div>
        </main>
        {settings && (
          <Settings readOnly={preview} onClose={() => setSettings(false)} report={setError} />
        )}
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
