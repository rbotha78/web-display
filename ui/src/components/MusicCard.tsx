import { useEffect, useState, type FormEvent } from "react";
import { api, type MusicConfig, type MusicStation } from "../api";
import type { Notice } from "./Toast";

export function MusicCard({ configured, notify, fail }: {
  configured: boolean;
  notify: (text: string, kind?: Notice["kind"]) => void;
  fail: (error: unknown) => void;
}) {
  const [config, setConfig] = useState<MusicConfig | null>(null);
  const [stations, setStations] = useState<MusicStation[]>([]);
  const [station, setStation] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [volume, setVolume] = useState(30);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    api.musicConfig().then((value) => {
      if (!active) return;
      setConfig(value);
      setStation(value.station_key);
      setEnabled(value.enabled);
      setVolume(value.volume);
    }).catch(fail);
    if (configured) {
      api.musicStations().then((value) => { if (active) setStations(value.items); }).catch(fail);
    } else {
      setStations([]);
    }
    return () => { active = false; };
  }, [configured, fail]);

  useEffect(() => {
    if (!config?.enabled) return;
    const timer = window.setInterval(() => {
      api.musicConfig().then(setConfig).catch(fail);
    }, 10000);
    return () => window.clearInterval(timer);
  }, [config?.enabled, fail]);

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const saved = await api.saveMusicConfig({ enabled, station_key: station, volume });
      setConfig(saved);
      notify(saved.enabled ? "Background station enabled." : "Background music disabled.", "success");
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  }

  return <form className="card wide" onSubmit={save}>
    <h2>Background music</h2>
    <p className="muted">Library radio plays across scene changes, pauses for videos with audio,
      and resumes afterwards. Music is off until you select a station and enable it.</p>
    <label>Music station
      <select value={station} disabled={!configured || busy} onChange={(event) => setStation(event.target.value)}>
        <option value="">Select a station</option>
        {station && !stations.some((item) => item.key === station) &&
          <option value={station}>Saved station (currently unavailable)</option>}
        {stations.map((item) => <option key={item.key} value={item.key}>{item.library}: {item.title}</option>)}
      </select>
    </label>
    <label><input type="checkbox" checked={enabled} disabled={busy || (!enabled && (!configured || !station))}
      onChange={(event) => setEnabled(event.target.checked)} />Enable background music</label>
    <label>Music volume
      <input aria-label="Music volume" type="range" min={0} max={100} step={1} value={volume}
        onChange={(event) => setVolume(Number(event.target.value))} />
      <span>{volume}%</span>
    </label>
    {config && <p role="status">Music: {config.status.state}{config.status.title ? ` — ${config.status.title}` : ""}</p>}
    {config?.status.error && <p role="alert">{config.status.error}</p>}
    {!configured && <p className="muted">Connect Plex above to discover music stations.</p>}
    <button className="primary" disabled={busy || !config || (enabled && (!configured || !station))}>
      Save music settings
    </button>
  </form>;
}
