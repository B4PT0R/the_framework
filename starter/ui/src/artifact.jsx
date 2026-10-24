import React, { Component, useState } from "react";
const copy = { artifactTemporary: "Current turn view", artifact: "Artifact", artifactImage: "Shared image", artifactFile: "File", artifactAudio: "Audio",
  artifactUnavailable: "This content is unavailable.", artifactDownload: "Download {name}",
  artifactDetails: "{kind} content — show data" };
const useLocale = () => ({ t: (key, values = {}) => copy[key].replace(/\{(\w+)\}/g, (_, name) => values[name]) });

// Explicit presentation is data, never an instruction to execute supplied HTML.
export function artifactSource(value) {
  if (typeof value !== "string") return null;
  if (/^\/(?!\/)/.test(value) && !value.includes("\\")) return value;
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) ? url.href : null;
  } catch { return null; }
}

function Description({ payload }) {
  return payload.description && <p className="artifact-description">{String(payload.description)}</p>;
}

function ImageArtifact({ payload }) {
  const { t } = useLocale();
  const [failedSource, setFailedSource] = useState(null);
  const src = artifactSource(payload.src);
  return <>
    {src && failedSource !== src
      ? <img src={src} alt={String(payload.alt || payload.title || payload.name || t("artifactImage"))}
          loading="lazy" onError={() => setFailedSource(src)} />
      : <p role="status">{t("artifactUnavailable")}</p>}
    <Description payload={payload} />
  </>;
}

function FileArtifact({ payload }) {
  const { t } = useLocale();
  const src = artifactSource(payload.src);
  return <>
    {src ? <a className="artifact-download" href={src} download={payload.name || true}
        target="_blank" rel="noopener noreferrer">{t("artifactDownload", { name: payload.name || payload.title || t("artifactFile") })}</a>
      : <p role="status">{t("artifactUnavailable")}</p>}
    <Description payload={payload} />
  </>;
}

function AudioArtifact({ payload }) {
  const { t } = useLocale();
  const [failedSource, setFailedSource] = useState(null);
  const src = artifactSource(payload.src);
  return <>
    {src && failedSource !== src
      ? <audio key={src} controls preload="none"
          aria-label={String(payload.title || payload.name || t("artifactAudio"))}
          src={src} onError={() => setFailedSource(src)} />
      : src && <p role="status">{t("artifactUnavailable")}</p>}
    <FileArtifact payload={payload} />
  </>;
}

function Fallback({ kind, payload }) {
  const { t } = useLocale();
  return <details className="artifact-fallback"><summary>{t("artifactDetails", { kind })}</summary>
    <pre>{JSON.stringify(payload, null, 2)}</pre>
  </details>;
}

class RenderBoundary extends Component {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidUpdate(previous) {
    if (this.state.failed && previous.payload !== this.props.payload) this.setState({ failed: false });
  }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

const builtins = { image: ImageArtifact, audio: AudioArtifact, file: FileArtifact };

export function Artifact({ kind, payload = {}, renderers = {}, temporary = false }) {
  const { t } = useLocale();
  const Renderer = Object.hasOwn(renderers, kind) ? renderers[kind]
    : Object.hasOwn(builtins, kind) ? builtins[kind] : null;
  const fallback = <Fallback kind={kind} payload={payload} />;
  return <article className={`message artifact artifact-${kind}`} aria-label={t("artifact")}>
    {temporary && <small>{t("artifactTemporary")}</small>}
    {payload.title && <h3>{String(payload.title)}</h3>}
    <RenderBoundary fallback={fallback} payload={payload}>
      {Renderer ? <Renderer kind={kind} payload={payload} /> : fallback}
    </RenderBoundary>
  </article>;
}
