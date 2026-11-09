import { useEffect, useRef, useState } from "react";
import type { RemoteView } from "../api";
import { WeatherContent } from "../components/WeatherDisplay";
import { RecentServiceContent } from "../components/RecentServiceContent";
import { ServiceStationTimes, nextServiceStops } from "../components/ServiceStationTimes";
import { api } from "./api";

function deviceClock(value: RemoteView, elapsed: number) {
  const date = new Date(Date.parse(value.device_time) + elapsed);
  try {
    // IANA names and UTC work directly. Abbreviations (BST/GMT etc.) use the
    // explicit device offset rather than silently adopting the manager's zone.
    new Intl.DateTimeFormat("en", { timeZone: value.timezone });
    return { date, timeZone: value.timezone, offsetMinutes: 0 };
  } catch {
    const offset = value.device_time.match(/([+-])(\d{2}):(\d{2})$/);
    const minutes = offset ? (Number(offset[2]) * 60 + Number(offset[3])) * (offset[1] === "-" ? -1 : 1) : 0;
    return { date: new Date(date.getTime() + minutes * 60000), timeZone: "UTC", offsetMinutes: minutes };
  }
}

export function RemotePreview({ deviceId, deviceName, close }: {
  deviceId: string; deviceName: string; close: () => void;
}) {
  const [value, setValue] = useState<RemoteView | null>(null);
  const [error, setError] = useState("");
  const [now, setNow] = useState(Date.now());
  const received = useRef(0);
  const opener = useRef(document.activeElement);
  const closeButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeButton.current?.focus();
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") close(); };
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("keydown", escape);
      if (opener.current instanceof HTMLElement) opener.current.focus();
    };
  }, [close]);

  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    setValue(null);
    setError("");
    const poll = async () => {
      const request = new AbortController();
      const abort = () => request.abort();
      controller.signal.addEventListener("abort", abort, { once: true });
      const timeout = window.setTimeout(abort, 12000);
      try {
        const next = await api.remoteView(deviceId, request.signal);
        if (!next.available || !["carousel", "single", "recovery"].includes(next.mode) ||
            !Number.isFinite(Date.parse(next.device_time)) || !Number.isFinite(next.updated)) {
          throw new Error("The device returned an invalid preview");
        }
        if (!controller.signal.aborted) {
          received.current = Date.now();
          setNow(Date.now());
          setValue(next);
          setError("");
        }
      } catch (failure) {
        if (!controller.signal.aborted) setError(request.signal.aborted ?
          "Preview connection timed out; scene is not current." :
          failure instanceof Error ? failure.message : "Preview unavailable");
      } finally {
        window.clearTimeout(timeout);
        controller.signal.removeEventListener("abort", abort);
        if (!controller.signal.aborted) timer = window.setTimeout(poll, 5000);
      }
    };
    void poll();
    const clock = window.setInterval(() => setNow(Date.now()), 1000);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
      window.clearInterval(clock);
    };
  }, [deviceId]);

  const stale = value !== null && now - received.current + (value.heartbeat_age_seconds ?? 0) * 1000 > 15000;
  const scene = value?.scene;
  const clock = value ? deviceClock(value, now - received.current + (value.heartbeat_age_seconds ?? 0) * 1000) : null;
  const train = value?.train;
  return (
    <div className="remote-preview-backdrop">
      <section className="remote-preview card" role="dialog" aria-modal="true" aria-labelledby="remote-preview-title"
        onKeyDown={(event) => {
          if (event.key === "Tab") { event.preventDefault(); closeButton.current?.focus(); }
        }}>
        <header className="actions">
          <h2 id="remote-preview-title">{deviceName}: reconstructed preview</h2>
          <button ref={closeButton} type="button" onClick={close}>Close preview</button>
        </header>
        <p className="muted">Reconstructed scene, not a live screen or screenshot. Websites and videos are never loaded here. Refreshes every 5 seconds while open.</p>
        {(error || stale) && <p role="alert">{error || "Preview heartbeat is stale; scene is not current."}</p>}
        {!value && !error && <p role="status">Loading preview…</p>}
        {value && !error && !stale && (
          <div className="remote-preview-scene">
            <p className="muted">{value.mode} · {value.timezone} · Device snapshot {value.device_time}</p>
            {value.sleep.sleeping && <p role="status">Sleep schedule active — videos and music paused; non-video scenes continue.</p>}
            {value.message && <p role="status">{value.message}</p>}
            {scene && <h3>{scene.title || scene.kind}</h3>}
            {clock && scene && ["dashboard", "train", "weather"].includes(scene.kind) &&
              (scene.kind !== "dashboard" || scene.layout !== "notice") && (
              <div className="remote-preview-clock">
                <time dateTime={clock.date.toISOString()}>{clock.date.toLocaleTimeString([], {
                  timeZone: clock.timeZone, hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
                })}</time>
                <p>{clock.date.toLocaleDateString([], {
                  timeZone: clock.timeZone, weekday: "long", year: "numeric", month: "long", day: "numeric",
                })}</p>
              </div>
            )}
            {scene?.kind === "dashboard" && scene.layout !== "clock" && <p className="remote-preview-notice">{scene.text}</p>}
            {(scene?.kind === "web" || scene?.kind === "video") && (
              <section aria-label={scene.kind === "web" ? "Website details" : "Video metadata"}>
                <p>{scene.kind === "web" ? "Website details only — page content is not fetched." : "Video metadata only — no streaming, playback or media fetching."}</p>
                <dl>
                  {scene.url && <><dt>Address (credentials and query removed)</dt><dd>{scene.url}</dd></>}
                  {scene.source && <><dt>Source</dt><dd>{scene.source === "plex" ? "Plex" : scene.source}</dd></>}
                  {scene.plex_rating_key && <><dt>Plex item</dt><dd>{scene.plex_rating_key}</dd></>}
                  {scene.duration != null && <><dt>Duration</dt><dd>{scene.duration} seconds</dd></>}
                  {scene.kind === "video" && <><dt>Audio</dt><dd>{scene.audio_enabled ? "Enabled" : "Muted"}</dd></>}
                </dl>
              </section>
            )}
            {scene?.kind === "train" && (
              <section aria-label="Train departures">
                <h3>{train?.station || scene.station_crs}</h3>
                {(!train || train.error) && <p role="status">{train?.error || "Waiting for departure information"}</p>}
                {train?.stale && <p role="status">Departure data may be out of date.</p>}
                {train?.details_available === false && <p>{train.details_error || "Detailed status unavailable"}</p>}
                {train?.services_available === false && <p>Services are currently unavailable at this station.</p>}
                {train && train.services_available !== false && !train.error && !train.rows?.length &&
                  <p>No upcoming departures were returned.</p>}
                <table>
                  <thead><tr><th>Time</th><th>Destination</th>{train?.platform_available && <th>Platform</th>}<th>Status</th></tr></thead>
                  <tbody>{train?.rows?.map((row, index) => (
                    <tr key={index}><td>{row.scheduled}</td><td>{row.destination}
                      {row.service_type === "bus" && <p>Rail replacement bus</p>}
                      {row.alerts?.map((text, i) => <p key={i}>{text}</p>)}
                    </td>{train?.platform_available && <td>{row.service_type === "bus" ? "Bus stop" : row.platform || "TBC"}</td>}
                      <td>{row.status || (row.cancelled ? "Cancelled" : row.estimated)}</td></tr>
                  ))}</tbody>
                </table>
                {train?.next && <div role="group" aria-label="Next service station times">
                  <ServiceStationTimes stops={nextServiceStops(train, train.station || scene.station_crs || "")}
                    station={train.station || scene.station_crs} lastSeen={train.next.last_seen} />
                </div>}
                {train?.alerts?.map((text, i) => <p key={i}>{text}</p>)}
                <section aria-label={train?.recent && !train.recent.actual
                  ? "Previous scheduled service" : "Most recent departure"}>
                  <h4>{train?.recent && !train.recent.actual
                    ? "Previous scheduled service" : "Most recent departure"}</h4>
                  {train?.recent ? (
                    <RecentServiceContent recent={train.recent} station={train.station} />
                  ) : <p>{train?.recent_error || "Waiting for previous-service information."}</p>}
                </section>
              </section>
            )}
            {scene && (scene.kind === "weather" || scene.kind === "train" || scene.show_weather) &&
              <WeatherContent weather={value.weather} compact={scene.kind !== "weather"} currentOnly={scene.kind === "train"}
                timeZone={clock?.timeZone} timeOffsetMinutes={clock?.offsetMinutes} />}
          </div>
        )}
      </section>
    </div>
  );
}
