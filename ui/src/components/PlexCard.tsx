import { useEffect, useState, type FormEvent } from "react";
import { api, type PlexConfig } from "../api";
import type { Notice } from "./Toast";

interface Props {
  notify: (text: string, kind?: Notice["kind"]) => void;
  fail: (error: unknown) => void;
}

export function PlexCard({ notify, fail }: Props) {
  const [config, setConfig] = useState<PlexConfig | null>(null);
  const [serverUrl, setServerUrl] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.plexConfig().then((value) => {
      setConfig(value);
      setServerUrl(value.server_url);
    }).catch(fail);
  }, [fail]);

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const saved = await api.savePlexConfig({
        server_url: serverUrl,
        ...(token ? { token } : {}),
      });
      setConfig(saved);
      setServerUrl(saved.server_url);
      setToken("");
      notify(`Verified ${saved.server_name || "Plex Media Server"} and saved Plex settings.`, "success");
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  }

  async function clear() {
    if (!window.confirm("Remove the saved Plex server and token?")) return;
    setBusy(true);
    try {
      const saved = await api.savePlexConfig({ server_url: "", clear_token: true });
      setConfig(saved);
      setServerUrl("");
      setToken("");
      notify("Plex settings removed.", "success");
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card wide" onSubmit={save}>
      <h2>Plex Media Server</h2>
      <p className="muted">
        Configure a server address and Plex token to browse video libraries.
        The token is stored privately on this device and is never returned to the browser.
      </p>
      {config?.configured && <p role="status">Configured server: {config.server_url}</p>}
      <label>
        Server address
        <input type="url" required value={serverUrl}
          placeholder="http://plex-server:32400"
          onChange={(event) => setServerUrl(event.target.value)} />
      </label>
      <label>
        Plex token
        <input type="password" autoComplete="new-password" maxLength={512}
          required={!config?.configured} value={token}
          placeholder={config?.configured ? "Leave blank to keep saved token" : "Enter token"}
          onChange={(event) => setToken(event.target.value)} />
      </label>
      <div className="actions">
        <button className="primary" disabled={busy || !serverUrl}>
          {busy ? "Connecting…" : config?.configured ? "Save and test connection" : "Connect"}
        </button>
        {config?.configured && (
          <button type="button" className="danger" disabled={busy} onClick={() => void clear()}>
            Remove Plex settings
          </button>
        )}
      </div>
    </form>
  );
}
