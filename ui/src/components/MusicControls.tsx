import { useEffect, useRef, useState } from "react";
import { api, type MusicConfig } from "../api";

export function MusicControls({ fail, onEnabledChange }: {
  fail: (error: unknown) => void;
  onEnabledChange: (config: MusicConfig) => void;
}) {
  const [config, setConfig] = useState<MusicConfig | null>(null);
  const [busy, setBusy] = useState(false);
  const [connection, setConnection] = useState<"loading" | "ready" | "unavailable">("loading");
  const reportedFailure = useRef(false);

  useEffect(() => {
    if (busy) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function refresh() {
      try {
        const value = await api.musicConfig(controller.signal);
        if (!active) return;
        setConfig(value);
        setConnection("ready");
        reportedFailure.current = false;
      } catch (error) {
        if (!active) return;
        setConnection("unavailable");
        if (!reportedFailure.current) {
          reportedFailure.current = true;
          fail(error);
        }
      } finally {
        if (active) timer = setTimeout(() => void refresh(), 2000);
      }
    }
    void refresh();
    return () => { active = false; controller.abort(); clearTimeout(timer); };
  }, [busy, fail]);

  async function control(action: Parameters<typeof api.controlMusic>[0]) {
    if (busy) return;
    setBusy(true);
    try {
      const saved = await api.controlMusic(action);
      setConfig(saved);
      setConnection("ready");
      if (action === "play" || action === "stop") onEnabledChange(saved);
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  }

  const sleeping = config?.status.state === "sleeping";
  const available = connection === "ready";
  const paused = config?.status.paused_by_user === true;
  const action = !config?.enabled ? "play" : paused ? "resume" : "pause";
  const state = !available ? (connection === "unavailable" ? "Player unavailable" : "Loading music player...") :
    sleeping ? "Music is sleeping" : !config?.enabled ? "Music stopped" :
      paused ? "Music paused" : config.status.state === "paused" ? "Paused for video audio" :
        config.status.state === "playing" ? "Playing" : config.status.state === "error" ? "Music interrupted" : "Loading music...";

  return <>
    <div className="music-controls-space" aria-hidden="true" />
    <section className="music-controls" aria-label="Audio player">
      <div className="music-controls-track">
        <strong title={config?.status.title}>{config?.status.title || "Background music"}</strong>
        <span>{state}</span>
        {available && !config?.station_key && <small>Choose a music source in Global settings.</small>}
      </div>
      <div className="music-controls-buttons">
        <button type="button" disabled={busy || !available || sleeping || !config?.station_key}
          onClick={() => void control(action)}>{action === "play" ? "Play" : action === "resume" ? "Resume" : "Pause"}</button>
        <button type="button" disabled={busy || !available || !config?.enabled}
          onClick={() => void control("stop")}>Stop</button>
        <button type="button" disabled={busy || !available || sleeping || !config?.enabled || !config.status.title}
          onClick={() => void control("skip")}>Skip</button>
      </div>
    </section>
  </>;
}
