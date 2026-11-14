import { useEffect, useRef, useState, type ReactNode } from "react";
import type { MusicConfig } from "../api";
import { useDeviceApi } from "../DeviceApiContext";

export function MusicControls({ fail, onEnabledChange, inline = false, label }: {
  fail: (error: unknown) => void;
  onEnabledChange: (config: MusicConfig) => void;
  inline?: boolean;
  label?: ReactNode;
}) {
  const api = useDeviceApi();
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
  }, [api, busy, fail]);

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

  async function like() {
    const ratingKey = config?.status.rating_key;
    if (busy || !ratingKey) return;
    setBusy(true);
    try {
      setConfig(await api.likeMusic(ratingKey));
      setConnection("ready");
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  }

  const sleeping = config?.status.state === "sleeping";
  const available = connection === "ready";
  const paused = config?.status.paused_by_user === true;
  const liked = config?.status.liked === true;
  const action = !config?.enabled ? "play" : paused ? "resume" : "pause";
  const state = !available ? (connection === "unavailable" ? "Player unavailable" : "Loading music player...") :
    sleeping ? "Music is sleeping" : !config?.enabled ? "Music stopped" :
      paused ? "Music paused" : config.status.state === "paused" ? "Paused for video audio" :
        config.status.state === "playing" ? "Playing" : config.status.state === "error" ? "Music interrupted" : "Loading music...";

  return <>
    {!inline && <div className="music-controls-space" aria-hidden="true" />}
    <section className={`music-controls${inline ? " music-controls-inline" : ""}`} aria-label="Audio player">
      <div className="music-controls-track">
        {label}
        <strong title={config?.status.title}>{config?.status.title || "Background music"}</strong>
        {config?.status.title && config.status.artist &&
          <span className="music-controls-artist" title={config.status.artist}>{config.status.artist}</span>}
        <span>{state}</span>
        {available && !config?.station_key && <small>Choose a music source in Global settings.</small>}
      </div>
      <div className="music-controls-buttons">
        <button type="button" className={`music-like${liked ? " liked" : " ghost"}`}
          aria-label="Like song in Plex" aria-pressed={liked}
          title={liked ? "Liked in Plex (5 stars)" : "Rate this song 5 stars in Plex"}
          disabled={busy || !available || sleeping || !config?.enabled || !config.status.rating_key || liked}
          onClick={() => void like()}>
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M12 21 3.5 12.5C-3 6 6 0 12 6c6-6 15 0 8.5 6.5Z" />
          </svg>
        </button>
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
