import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { Scene } from "./api";
import { WeatherDisplay } from "./components/WeatherDisplay";
import { NowPlayingDisplay } from "./components/NowPlayingDisplay";
import { RecentServiceContent } from "./components/RecentServiceContent";
import { ServiceStationTimes, nextServiceStops } from "./components/ServiceStationTimes";

interface DisplayScene {
  kind: "dashboard" | "train" | "weather" | "music" | "recovery";
  revision: string;
  layout: Scene["layout"];
  text: string;
  scene_id: string;
  title: string;
  station_crs: string;
  show_previous_departure?: boolean;
  show_weather?: boolean;
  safe_area_percent?: number;
}

interface TrainRow {
  scheduled: string;
  estimated: string;
  destination: string;
  platform: string;
  operator: string;
  cancelled: boolean;
  status?: string;
  state?: "cancelled" | "delayed" | "on-time" | "";
  service_type?: "train" | "bus";
  alerts?: string[];
}

interface CallingPoint {
  station: string;
  scheduled: string;
  estimated: string;
  actual: string;
  cancelled: boolean;
}

interface RecentStop extends CallingPoint {
  delay_minutes: number | null;
  timing: "departure" | "arrival" | "reported";
}

interface RecentDeparture {
  scheduled: string;
  actual: string;
  destination: string;
  platform: string;
  operator: string;
  status: string;
  delay_minutes: number | null;
  last_seen: { station: string; actual: string } | null;
  stops: RecentStop[];
  display_stops?: RecentStop[];
  updated_at: number;
  stale: boolean;
  details_error: string;
}

export interface TrainBoard {
  crs?: string;
  destination_crs?: string;
  filter_station_crs?: string;
  station?: string;
  generated_at?: string;
  services_available?: boolean;
  platform_available?: boolean;
  updated_at?: number;
  rows?: TrainRow[];
  alerts?: string[];
  details_available?: boolean;
  details_error?: string;
  recent?: RecentDeparture | null;
  recent_error?: string;
  next?: {
    scheduled?: string;
    estimated?: string;
    actual?: string;
    status: string;
    delay_minutes: number | null;
    calling_at?: CallingPoint[][];
    previous_stops?: CallingPoint[][];
    last_seen?: { station: string; actual: string } | null;
  } | null;
  stale?: boolean;
  error?: string;
}

function DepartureTable({ rows, platformAvailable = false, subsequent = false, children }: {
  rows: TrainRow[];
  platformAvailable?: boolean;
  subsequent?: boolean;
  children?: ReactNode;
}) {
  return (
    <table className={subsequent ? "train-subsequent" : undefined}
      aria-label={subsequent ? "Later departures" : "Next departure"}>
      <colgroup>
        <col /><col />{platformAvailable && <col />}<col />
      </colgroup>
      <thead className={subsequent ? "visually-hidden" : undefined}>
        <tr>
          <th scope="col">Time</th><th scope="col">Destination</th>
          {platformAvailable && <th scope="col">Platform</th>}
          <th scope="col">Status</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, index) => (
          <tr key={`${row.scheduled}-${index}`} className={subsequent ? "" : "train-next"}>
            <td>{row.scheduled}</td>
            <td>
              {row.destination || "Destination unavailable"}
              {row.service_type === "bus" && (
                <span className="train-bus-label">Rail replacement bus</span>
              )}
              {!!row.alerts?.length && (
                <div className="train-service-notices" aria-label={`Service notices for ${row.scheduled}`}>
                  {row.alerts.map((notice, noticeIndex) => (
                    <p key={noticeIndex}><strong>Service notice: </strong>{notice}</p>
                  ))}
                </div>
              )}
            </td>
            {platformAvailable && (
              <td>{row.service_type === "bus" ? "Bus stop" : row.platform || "TBC"}</td>
            )}
            <td className={row.state === "cancelled" || row.cancelled ? "train-cancelled" :
              row.state === "delayed" ? "train-delayed" :
              row.state === "on-time" ? "train-on-time" : ""}>
              {row.status || (row.cancelled ? "Cancelled" : row.estimated || "Status unavailable")}
            </td>
          </tr>
        ))}
        {children}
      </tbody>
    </table>
  );
}

export function SceneScreen() {
  const [scene, setScene] = useState<DisplayScene | null>(null);
  const [now, setNow] = useState(() => new Date());
  const [error, setError] = useState("");
  const [trainBoard, setTrainBoard] = useState<TrainBoard | null>(null);
  const [trainError, setTrainError] = useState("");
  const screenRef = useRef<HTMLElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const [scrolling, setScrolling] = useState(false);

  useEffect(() => {
    let direction = 1;
    let pause = 40;
    setScrolling(false);
    const timer = window.setInterval(() => {
      const element = contentRef.current ?? screenRef.current;
      if (!element) return;
      const maximum = element.scrollHeight - element.clientHeight;
      setScrolling(maximum > 1);
      if (maximum <= 1 || pause-- > 0) return;
      element.scrollTop += direction * 4;
      if (element.scrollTop >= maximum || element.scrollTop <= 0) {
        direction *= -1;
        pause = 40;
      }
    }, 100);
    return () => window.clearInterval(timer);
  }, [scene?.revision]);

  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    const poll = async () => {
      try {
        const response = await fetch("/api/scene", { signal: controller.signal });
        if (!response.ok) throw new Error("Could not load the dashboard");
        const value: DisplayScene = await response.json();
        if (!controller.signal.aborted) {
          setScene((previous) => previous?.revision === value.revision ? previous : value);
          setError("");
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setError(error instanceof Error ? error.message : "Could not load the dashboard");
        }
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(poll, 500);
      }
    };
    void poll();
    const clock = window.setInterval(() => setNow(new Date()), 1000);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
      window.clearInterval(clock);
    };
  }, []);

  useEffect(() => {
    if (scene?.kind !== "train") {
      setTrainBoard(null);
      setTrainError("");
      return;
    }
    const controller = new AbortController();
    let timer: number | undefined;
    setTrainBoard(null);
    const poll = async () => {
      try {
        const response = await fetch(
          `/api/train?scene_id=${encodeURIComponent(scene.scene_id)}`,
          { signal: controller.signal },
        );
        if (!response.ok) throw new Error("Could not load departure information");
        const value: TrainBoard = await response.json();
        if (!controller.signal.aborted) {
          setTrainBoard(value);
          setTrainError("");
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setTrainError(error instanceof Error ? error.message : "Could not load departure information");
        }
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(poll, 15000);
      }
    };
    void poll();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [scene]);

  useEffect(() => {
    if (!scene) return;
    const controller = new AbortController();
    let timer: number | undefined;
    const acknowledge = async () => {
      const request = new AbortController();
      const abort = () => request.abort();
      controller.signal.addEventListener("abort", abort, { once: true });
      const timeout = window.setTimeout(abort, 2000);
      try {
        const response = await fetch(
          `/api/scene/ready?revision=${encodeURIComponent(scene.revision)}`,
          { signal: request.signal },
        );
        if (!response.ok) throw new Error("Could not start the scene timer");
        if (!controller.signal.aborted) setError("");
      } catch (error: unknown) {
        if (!controller.signal.aborted) {
          if (!request.signal.aborted) {
            setError(error instanceof Error ? error.message : "Could not start the scene timer");
          }
          timer = window.setTimeout(acknowledge, 1000);
        }
      } finally {
        window.clearTimeout(timeout);
        controller.signal.removeEventListener("abort", abort);
      }
    };
    void acknowledge();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [scene]);

  const size = (scene?.text.length ?? 0) > 500 ? "small" : (scene?.text.length ?? 0) > 200 ? "medium" : "large";
  const station = trainBoard?.station || scene?.title || scene?.station_crs || "";
  const publishedSafeArea = scene?.safe_area_percent ?? 5;
  const safeArea = Number.isInteger(publishedSafeArea) &&
    publishedSafeArea >= 0 && publishedSafeArea <= 10 ? publishedSafeArea : 5;
  return (
    <main ref={screenRef} className={`scene-screen safe-area-${safeArea} ${scene?.layout ?? ""}${scene?.show_weather ? " weather-dashboard" : ""}${scene?.kind === "train" ? " train-screen" : ""}`}>
      {error ? <p role="alert">{error}</p> : !scene ? <p>Loading scene...</p> :
        scene.kind === "train" ? (
          <section className={`train-board${(trainBoard?.rows?.length ?? 0) > 5 ? " train-board-dense" : ""}${scene.show_previous_departure === false ? " train-board-no-previous" : ""}`}>
            <header>
              <p className="train-eyebrow">Live departures</p>
              <h1>{station}</h1>
            </header>
            {!!trainBoard?.rows?.length && (
              <section className="train-next-service" aria-label="Next service">
                <DepartureTable rows={trainBoard.rows.slice(0, 1)} platformAvailable={trainBoard.platform_available}>
                  {trainBoard.next && (
                    <tr className="train-next train-detail-row">
                      <td colSpan={trainBoard.platform_available ? 4 : 3}>
                        <div className="train-calling" role="group" aria-label="Next service station times">
                          <ServiceStationTimes stops={nextServiceStops(trainBoard, station)}
                            station={station} lastSeen={trainBoard.next.last_seen} />
                        </div>
                      </td>
                    </tr>
                  )}
                </DepartureTable>
              </section>
            )}
            <div ref={contentRef} className="train-content" tabIndex={0} aria-label="Departures and notices">
            {(trainBoard?.stale || trainError) && (
              <p className="train-warning" role="status">
                Data may be out of date{trainBoard?.error ? `: ${trainBoard.error}` : ""}
              </p>
            )}
            {trainBoard && trainBoard.details_available === false && (
              <p className="train-warning" role="status">
                {trainBoard.details_error || "Detailed status is unavailable; showing the board estimate."}
              </p>
            )}
            {trainError && !trainBoard && <p role="alert">{trainError}</p>}
            {trainBoard?.error && !trainBoard.rows?.length && <p role="status">{trainBoard.error}</p>}
            {trainBoard?.services_available === false && (
              <p role="status">Services are currently unavailable at this station.</p>
            )}
            {trainBoard && trainBoard.services_available !== false &&
              !trainBoard.rows?.length && !trainBoard.error && (
                <p>No upcoming departures were returned.</p>
              )}
            {trainBoard?.rows && trainBoard.rows.length > 1 && (
              <DepartureTable rows={trainBoard.rows.slice(1)}
                platformAvailable={trainBoard.platform_available} subsequent />
            )}
            {!!trainBoard?.alerts?.length && (
              <div className="train-alerts" aria-label="Station messages">
                <strong>Station messages</strong>
                {trainBoard.alerts.map((alert, index) => <p key={index}>{alert}</p>)}
              </div>
            )}
            {trainBoard?.updated_at && (
              <footer>Live data checked {new Date(trainBoard.updated_at * 1000).toLocaleTimeString()}</footer>
            )}
            </div>
            {scrolling && <p className="train-scroll-hint">More departures and notices — scrolling automatically</p>}
            {scene.show_previous_departure !== false && (
              <section className="train-recent" aria-label={trainBoard?.recent && !trainBoard.recent.actual
                ? "Previous scheduled service" : "Most recent departure"}>
                <h2>{trainBoard?.recent && !trainBoard.recent.actual
                  ? "Previous scheduled service" : "Most recent departure"}</h2>
                {trainBoard?.recent ? (
                  <RecentServiceContent recent={trainBoard.recent} station={station} />
                ) : (
                  <p role="status">{trainBoard?.recent_error ||
                    "Waiting for previous-service information; scheduled times are not departures."}</p>
                )}
              </section>
            )}
            <WeatherDisplay key={scene.revision} compact currentOnly />
            <time className="train-current-time" dateTime={now.toISOString()}>
              {now.toLocaleTimeString([], {
                hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
              })}
            </time>
          </section>
        ) :
        scene.kind === "weather" ? (
          <>
            <WeatherDisplay key={scene.revision} />
            <time className="weather-current-time" dateTime={now.toISOString()}>
              {now.toLocaleTimeString([], {
                hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
              })}
            </time>
          </>
        ) : scene.kind === "music" ? (
          <NowPlayingDisplay key={scene.revision} />
        ) : scene.kind === "recovery" ? (
          <div>
            <h1>No carousel scenes are currently available</h1>
            <p>Failed scenes will be retried automatically. Check the playlist in device administration.</p>
          </div>
        ) : (
          <>
            {scene.layout !== "notice" && (
              <div className="scene-clock">
                <time dateTime={now.toISOString()}>{now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time>
                <p>{now.toLocaleDateString([], { weekday: "long", year: "numeric", month: "long", day: "numeric" })}</p>
              </div>
            )}
            {scene.layout !== "clock" && <p className={`scene-notice ${size}`}>{scene.text}</p>}
            {scene.show_weather && <WeatherDisplay key={scene.revision} compact />}
          </>
        )}
    </main>
  );
}
