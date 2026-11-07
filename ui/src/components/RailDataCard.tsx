import { useEffect, useState, type FormEvent } from "react";
import { api, type RailDataConfig } from "../api";
import type { Notice } from "./Toast";

interface Props {
  notify: (text: string, kind?: Notice["kind"]) => void;
  fail: (error: unknown) => void;
}

export function RailDataCard({ notify, fail }: Props) {
  const [config, setConfig] = useState<RailDataConfig | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [detailsApiKey, setDetailsApiKey] = useState("");
  const [refreshSeconds, setRefreshSeconds] = useState(60);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.railDataConfig().then((saved) => {
      setConfig(saved);
      setRefreshSeconds(saved.refresh_seconds);
    }).catch(fail);
  }, [fail]);

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const saved = await api.saveRailDataConfig({
        api_key: apiKey,
        service_details_api_key: detailsApiKey,
        refresh_seconds: refreshSeconds,
      });
      setConfig(saved);
      setRefreshSeconds(saved.refresh_seconds);
      setApiKey("");
      setDetailsApiKey("");
      notify("National Rail settings saved on this device.", "success");
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  }

  async function clear() {
    if (!window.confirm("Remove the saved National Rail API keys?")) return;
    setBusy(true);
    try {
      setConfig(await api.saveRailDataConfig({ clear_key: true, clear_details_key: true }));
      setApiKey("");
      setDetailsApiKey("");
      notify("National Rail API keys removed.", "success");
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card wide" onSubmit={save}>
      <h2>National Rail departure boards</h2>
      <p className="muted">
        The two API keys are stored privately on this device and are never sent to the display browser.
        One station is queried per refresh interval, followed by one service-details request for its next departure.
        Multiple stations take turns.
      </p>
      <label>
        Departure Board API key
        <input type="password" autoComplete="new-password" maxLength={512}
          required={!config?.board_configured} value={apiKey}
          placeholder={config?.board_configured ? "Leave blank to keep saved key" : "Enter API key"}
          onChange={(event) => setApiKey(event.target.value)} />
      </label>
      <label>
        Service Details API key
        <input type="password" autoComplete="new-password" maxLength={512}
          required={!config?.details_configured} value={detailsApiKey}
          placeholder={config?.details_configured ? "Leave blank to keep saved key" : "Enter API key"}
          onChange={(event) => setDetailsApiKey(event.target.value)} />
      </label>
      <label>
        Refresh interval (seconds)
        <input type="number" min={30} step={1} required value={refreshSeconds}
          onChange={(event) => setRefreshSeconds(event.target.valueAsNumber)} />
      </label>
      <div className="actions">
        <button className="primary" disabled={busy || !config ||
          (!apiKey && !detailsApiKey && refreshSeconds === config.refresh_seconds)}>
          Save National Rail settings
        </button>
        {(config?.board_configured || config?.details_configured) && (
          <button type="button" className="danger" disabled={busy} onClick={() => void clear()}>
            Remove API keys
          </button>
        )}
        {config && <span role="status">
          Departure Board {config.board_configured ? "configured" : "not configured"}; Service Details {config.details_configured ? "configured" : "not configured"}
        </span>}
      </div>
    </form>
  );
}
