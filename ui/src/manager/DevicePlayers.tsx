import { useCallback, useEffect, useState } from "react";
import { DeviceApiContext, useDeviceApi } from "../DeviceApiContext";
import type { PlaybackStatus } from "../api";
import { MusicControls } from "../components/MusicControls";
import { settingsApi } from "./api";

const enabledChanged = () => undefined;

function CarouselControls({ fail }: { fail: (error: unknown) => void }) {
  const api = useDeviceApi();
  const [status, setStatus] = useState<PlaybackStatus | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (busy) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const reply = await api.playlistStatus(controller.signal);
        if (!controller.signal.aborted) {
          setStatus(reply);
          setError("");
        }
      } catch (reason) {
        if (!controller.signal.aborted) {
          setStatus(null);
          setError(reason instanceof Error ? reason.message : "Carousel unavailable");
        }
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(() => void refresh(), 2000);
      }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [api, busy]);

  async function control(action: "pause" | "resume" | "next") {
    if (busy) return;
    setBusy(true);
    try {
      await api.controlCarousel(action);
    } catch (reason) {
      fail(reason);
    } finally {
      setBusy(false);
    }
  }

  const supported = status?.transport_supported === true;
  const available = status?.transport_available === true;
  const enabled = status?.enabled === true;
  const paused = status?.paused === true;
  return <section className="carousel-controls" aria-label="Carousel player">
    <strong>Carousel</strong>
    <span className="muted">{error || (!status ? "Loading carousel..." :
      !supported ? "Update device firmware for carousel controls." :
      !enabled ? "Carousel disabled" : !available ? "Carousel unavailable" :
      paused ? "Carousel paused" : "Carousel playing")}</span>
    {enabled && available && status?.scene_title && <span>{status.scene_title}</span>}
    <div className="actions">
      <button type="button" disabled={busy || !supported || !enabled || !available}
        onClick={() => void control(paused ? "resume" : "pause")}>
        {paused ? "Resume carousel" : "Pause carousel"}
      </button>
      <button type="button" disabled={busy || !supported || !enabled || !available}
        onClick={() => void control("next")}>Next scene</button>
    </div>
  </section>;
}

export function DevicePlayers({ deviceId, notify }: {
  deviceId: string;
  notify: (text: string, kind?: "info" | "success" | "error") => void;
}) {
  const [connection, setConnection] = useState<{
    api: ReturnType<typeof settingsApi>; lifetime: AbortController;
  } | null>(null);
  useEffect(() => {
    const lifetime = new AbortController();
    setConnection({ api: settingsApi(deviceId, lifetime.signal), lifetime });
    return () => lifetime.abort();
  }, [deviceId]);
  const fail = useCallback((reason: unknown) => {
    if (!connection?.lifetime.signal.aborted) {
      notify(reason instanceof Error ? reason.message : "Player request failed", "error");
    }
  }, [connection, notify]);
  return connection ? <DeviceApiContext.Provider value={connection.api}>
    <MusicControls inline fail={fail} onEnabledChange={enabledChanged} />
    <CarouselControls fail={fail} />
  </DeviceApiContext.Provider> : <p>Loading players...</p>;
}
