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
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .config()
      .then((config) => {
        setUrl(config.url);
        setAutoAp(config.auto_ap);
        setLoaded(true);
      })
      .catch(fail);
  }, [fail]);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      const config = await api.saveConfig({ url, auto_ap: autoAp });
      setUrl(config.url);
      setAutoAp(config.auto_ap);
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
