import { Fragment, useEffect, useRef, useState } from "react";
import type { Scene } from "./api";
import { WeatherDisplay } from "./components/WeatherDisplay";

interface DisplayScene {
  kind: "dashboard" | "train" | "weather" | "recovery";
  revision: string;
  layout: Scene["layout"];
  text: string;
  scene_id: string;
  title: string;
  station_crs: string;
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

export interface TrainBoard {
  crs?: string;
  station?: string;
  generated_at?: string;
  services_available?: boolean;
  platform_available?: boolean;
  updated_at?: number;
  rows?: TrainRow[];
  alerts?: string[];
  details_available?: boolean;
  details_error?: string;
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

function callingPointText(point: CallingPoint): string {
  const time = point.cancelled ? "Cancelled" :
    point.actual ? `actual ${point.actual}` : point.scheduled;
  const expected = !point.cancelled && !point.actual && point.estimated &&
    point.estimated !== "On time" ? `; expected ${point.estimated}` : "";
  return `${point.station} (${time}${expected})`;
}

function StopLine({ label, groups }: { label: string; groups: CallingPoint[][] }) {
  const ref = useRef<HTMLSpanElement>(null);
  const text = groups.map((group) => group.map(callingPointText).join(", ")).join(" | ");
  useEffect(() => {
    let direction = 1;
    let pause = 40;
    if (ref.current) ref.current.scrollLeft = 0;
    const timer = window.setInterval(() => {
      const element = ref.current;
      if (!element) return;
      const maximum = element.scrollWidth - element.clientWidth;
      if (maximum <= 1 || pause-- > 0) return;
      element.scrollLeft += direction * 2;
      if (element.scrollLeft >= maximum || element.scrollLeft <= 0) {
        direction *= -1;
        pause = 40;
      }
    }, 50);
    return () => window.clearInterval(timer);
  }, [text]);
  if (!text) return null;
  return (
    <p className="train-stop-line">
      <strong>{label}: </strong>
      <span ref={ref} className="train-stop-scroll">{text}</span>
    </p>
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
  const publishedSafeArea = scene?.safe_area_percent ?? 5;
  const safeArea = Number.isInteger(publishedSafeArea) &&
    publishedSafeArea >= 0 && publishedSafeArea <= 10 ? publishedSafeArea : 5;
  return (
    <main ref={screenRef} className={`scene-screen safe-area-${safeArea} ${scene?.layout ?? ""}${scene?.show_weather ? " weather-dashboard" : ""}${scene?.kind === "train" ? " train-screen" : ""}`}>
      {error ? <p role="alert">{error}</p> : !scene ? <p>Loading scene...</p> :
        scene.kind === "train" ? (
          <section className="train-board">
            <header>
              <p className="train-eyebrow">Live departures</p>
              <h1>{trainBoard?.station || scene.title || scene.station_crs}</h1>
            </header>
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
            {!!trainBoard?.rows?.length && (
              <table>
                <thead>
                  <tr>
                    <th>Time</th><th>Destination</th>
                    {trainBoard.platform_available && <th>Platform</th>}
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {trainBoard.rows.map((row, index) => (
                    <Fragment key={`${row.scheduled}-${index}`}>
                    <tr className={index === 0 ? "train-next" : ""}>
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
                      {trainBoard.platform_available && (
                        <td>{row.service_type === "bus" ? "Bus stop" : row.platform || "TBC"}</td>
                      )}
                      <td className={row.state === "cancelled" || row.cancelled ? "train-cancelled" :
                        row.state === "delayed" ? "train-delayed" :
                        row.state === "on-time" ? "train-on-time" : ""}>
                        {row.status || (row.cancelled ? "Cancelled" : row.estimated || "Status unavailable")}
                      </td>
                    </tr>
                    {index === 0 && trainBoard.next && (
                      <tr className="train-next train-detail-row">
                        <td colSpan={trainBoard.platform_available ? 4 : 3}>
                          <div className="train-calling">
                            {row.state !== "cancelled" && !row.cancelled && (
                              <>
                                <StopLine label="Previous stops" groups={trainBoard.next.previous_stops ?? []} />
                                <StopLine label="Calling at" groups={trainBoard.next.calling_at ?? []} />
                              </>
                            )}
                            {trainBoard.next.last_seen && (
                              <p>
                                <strong>Last reported at: </strong>
                                {trainBoard.next.last_seen.station} at {trainBoard.next.last_seen.actual}
                              </p>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
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
