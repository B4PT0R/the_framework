import React, { useEffect, useState } from "react";
import { api } from "./api";

export function Settings({ onClose, report }) {
  const [config, setConfig] = useState("");
  const [plugins, setPlugins] = useState([]);
  const [saving, setSaving] = useState(false);
  let parsed;
  try {
    parsed = JSON.parse(config);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) parsed = null;
  } catch {
    parsed = null;
  }
  function change(key, value) {
    setConfig(JSON.stringify({ ...parsed, [key]: value }, null, 2));
  }
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      api("config", { signal: controller.signal }),
      api("plugins", { signal: controller.signal }),
    ])
      .then(([settings, installed]) => {
        setConfig(JSON.stringify(settings.config, null, 2));
        setPlugins(installed.plugins);
      })
      .catch((error) => {
        if (error.name !== "AbortError") report(error.message);
      });
    return () => controller.abort();
  }, []);
  async function save() {
    setSaving(true);
    try {
      await api("config", { method: "PATCH", body: JSON.parse(config) });
      onClose();
    } catch (error) {
      report(error.message);
    } finally {
      setSaving(false);
    }
  }
  async function toggle(plugin) {
    try {
      await api(`plugins/${plugin.name}/binding`, {
        method: "PUT",
        body: { enabled: !plugin.binding_enabled },
      });
      setPlugins((await api("plugins")).plugins);
    } catch (error) {
      report(error.message);
    }
  }
  return (
    <aside className="settings" aria-label="Settings">
      <header>
        <h2>Settings</h2>
        <button onClick={onClose}>Close</button>
      </header>
      <p>Configure the model and each capability. Changes persist locally.</p>
      <h3>Conversation</h3>
      <label className="setting-field">
        Model
        <input
          value={typeof parsed?.model === "string" ? parsed.model : ""}
          disabled={!parsed}
          onChange={(event) => change("model", event.target.value)}
          spellCheck={false}
        />
      </label>
      <p>
        Use a model available to your backend account. Credentials stay outside
        the interface.
      </p>
      <h3>Plugins</h3>
      <div className="plugins">
        {plugins.map((plugin) => (
          <div className="plugin-row" key={plugin.name}>
            {plugin.binding_available && <input
              type="checkbox"
              aria-label={`Expose ${plugin.name} to the agent`}
              checked={plugin.binding_enabled}
              disabled={plugin.binding_required || !plugin.running}
              onChange={() => toggle(plugin)}
            />}
            <span>{plugin.name}</span>
            <small>{plugin.binding_available
              ? (plugin.running ? "Loaded" : "Stopped")
              : (plugin.running ? "Server only · Loaded" : "Server only · Stopped")}</small>
          </div>
        ))}
      </div>
      <details>
        <summary>Advanced configuration</summary>
        <p>
          Agent and plugin parameters. Loading a plugin does not guarantee that
          its external services are configured.
        </p>
        <label className="sr-only" htmlFor="config">
          Agent configuration JSON
        </label>
        <textarea
          id="config"
          className="config"
          value={config}
          onChange={(e) => setConfig(e.target.value)}
          spellCheck={false}
        />
      </details>
      <button
        className="primary"
        disabled={saving || typeof parsed?.model !== "string" || !parsed.model.trim()}
        onClick={save}
      >
        {saving ? "Saving…" : "Save configuration"}
      </button>
    </aside>
  );
}
