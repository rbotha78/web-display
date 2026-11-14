import { useEffect, useState, type FormEvent } from "react";
import { useDeviceApi } from "../DeviceApiContext";
import type { Notice } from "./Toast";

interface Props {
  notify: (text: string, kind?: Notice["kind"]) => void;
  fail: (error: unknown) => void;
}

export function DisplayCard({ notify, fail }: Props) {
  const api = useDeviceApi();
  const [url, setUrl] = useState("");
  const [autoAp, setAutoAp] = useState(false);
  const [safeArea, setSafeArea] = useState(5);
  const [audioOutput, setAudioOutput] = useState("alsa/hdmi:CARD=vc4hdmi,DEV=0");
  const [outputs, setOutputs] = useState<{ id: string; label: string }[]>([]);
  const [outputsLoaded, setOutputsLoaded] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .config()
      .then((config) => {
        setUrl(config.url);
        setAutoAp(config.auto_ap);
        setSafeArea(config.safe_area_percent ?? 5);
        setAudioOutput(config.audio_output ?? "alsa/hdmi:CARD=vc4hdmi,DEV=0");
        setLoaded(true);
      })
      .catch(fail);
    api.audioOutputs().then(({ items }) => {
      setOutputs(items);
      setOutputsLoaded(true);
    }).catch(fail);
  }, [fail]);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      const config = await api.saveConfig({ url, auto_ap: autoAp, safe_area_percent: safeArea,
        audio_output: audioOutput });
      setUrl(config.url);
      setAutoAp(config.auto_ap);
      setSafeArea(config.safe_area_percent ?? 5);
      setAudioOutput(config.audio_output ?? "alsa/hdmi:CARD=vc4hdmi,DEV=0");
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
        Audio output
        <select value={audioOutput} onChange={(event) => setAudioOutput(event.target.value)}
          disabled={!loaded || !outputsLoaded}>
          {!outputs.some((output) => output.id === audioOutput) &&
            <option value={audioOutput}>{audioOutput} (unavailable)</option>}
          {outputs.map((output) => <option key={output.id} value={output.id}>{output.label}</option>)}
        </select>
      </label>
      <p className="hint">Applies to video audio and background music. HDMI is the default.
        Changing output restarts playback. Unavailable outputs are not replaced automatically.
        Reload this page after connecting a USB audio device.</p>
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
