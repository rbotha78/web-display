import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api";
import type { Notice } from "./Toast";

interface Props {
  notify: (text: string, kind?: Notice["kind"]) => void;
  fail: (error: unknown) => void;
}

export function DisplayCard({ notify, fail }: Props) {
  const [url, setUrl] = useState("");
  const [autoAp, setAutoAp] = useState(false);
  const [safeArea, setSafeArea] = useState(5);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .config()
      .then((config) => {
        setUrl(config.url);
        setAutoAp(config.auto_ap);
        setSafeArea(config.safe_area_percent ?? 5);
        setLoaded(true);
      })
      .catch(fail);
  }, [fail]);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      const config = await api.saveConfig({ url, auto_ap: autoAp, safe_area_percent: safeArea });
      setUrl(config.url);
      setAutoAp(config.auto_ap);
      setSafeArea(config.safe_area_percent ?? 5);
      notify("Settings saved. The display will update shortly.", "success");
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  };

  const reboot = async () => {
    if (!window.confirm("Reboot this display?")) return;
    try {
      await api.reboot();
      notify("Reboot requested.", "success");
    } catch (error) {
      fail(error);
    }
  };

  return (
    <form className="card" onSubmit={save}>
      <h2>Display</h2>
      <label>
        Web page URL
        <input
          type="url"
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          placeholder="https://example.com"
          disabled={!loaded}
        />
      </label>
      <label>
        Overscan safe area (% each edge)
        <input type="number" min="0" max="10" step="1" required
          value={safeArea} onChange={(event) => setSafeArea(event.target.valueAsNumber)}
          disabled={!loaded} />
      </label>
      <p className="hint">Default 5%. Applies to local train, weather, dashboard and recovery screens
        and the countdown, not web pages or video.</p>
      <label className="check">
        <input
          type="checkbox"
          checked={autoAp}
          onChange={(event) => setAutoAp(event.target.checked)}
          disabled={!loaded}
        />
        <span>
          Start the setup access point automatically after about two minutes without internet
        </span>
      </label>
      <div className="actions">
        <button className="primary" disabled={!loaded || busy}>
          Save settings
        </button>
        <button type="button" className="danger" onClick={reboot}>
          Reboot device
        </button>
      </div>
    </form>
  );
}
