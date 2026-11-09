import { useEffect, useRef, useState } from "react";
import type { TrainBoard } from "../SceneScreen";

interface StationTime {
  station: string;
  scheduled?: string;
  estimated?: string;
  actual?: string;
  cancelled?: boolean;
  timing?: "departure" | "arrival" | "reported";
}

const clock = /^(?:[01]\d|2[0-3]):[0-5]\d$/;

export function nextServiceLastSeen(board: TrainBoard): string {
  const seen = board.next?.last_seen;
  return seen && clock.test(seen.actual) ? seen.station : "";
}

export function nextServiceStops(board: TrainBoard, station: string): StationTime[] {
  const next = board.next;
  const row = board.rows?.[0];
  if (!next || !row) return [];
  if (row.cancelled || row.state === "cancelled") {
    return next.last_seen && nextServiceLastSeen(board) ? [next.last_seen] : [];
  }
  return [
    ...(next.previous_stops ?? []).flat(),
    { station, scheduled: next.scheduled || row.scheduled, actual: next.actual,
      estimated: next.estimated || row.estimated, timing: "departure" },
    ...(next.calling_at ?? []).flat(),
  ];
}

export function ServiceStationTimes({ stops, station = "", lastSeen, showLastSeen = true, includeArrivals = false }: {
  stops: StationTime[];
  station?: string;
  lastSeen?: { station: string; actual: string } | null;
  showLastSeen?: boolean;
  includeArrivals?: boolean;
}) {
  const gridRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [capacity, setCapacity] = useState(6);
  const observed = lastSeen && clock.test(lastSeen.actual) && lastSeen.station.trim() ? lastSeen : null;
  const route = stops.filter((stop) => includeArrivals || stop.timing !== "arrival");
  const normalize = (name: string) => name.trim().toLowerCase();
  const pinned = new Set(
    [station, observed?.station ?? ""]
      .filter((name) => name.trim())
      .map((name) => route.findIndex((stop) => normalize(stop.station) === normalize(name)))
      .filter((index) => index >= 0),
  );
  const overflowing = route.length > capacity;
  const routeKey = JSON.stringify(route.map((stop) => [stop.station, stop.scheduled]));
  const pinnedKey = [...pinned].sort((a, b) => a - b).join(",");

  useEffect(() => {
    const grid = gridRef.current;
    if (!grid) return;
    const measure = () => {
      const width = grid.getBoundingClientRect().width;
      const style = window.getComputedStyle(grid);
      const fontSize = parseFloat(style.fontSize);
      const gap = parseFloat(style.columnGap) || 0;
      if (width > 0 && fontSize > 0) {
        setCapacity(Math.max(1, Math.floor((width + gap) / (8 * fontSize + gap))));
      }
    };
    measure();
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(measure) : null;
    observer?.observe(grid);
    window.addEventListener("resize", measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, []);

  useEffect(() => {
    const scroll = scrollRef.current;
    if (!overflowing || !scroll) return;
    scroll.scrollLeft = 0;
    let direction = 1;
    let pause = 30;
    const timer = window.setInterval(() => {
      const maximum = scroll.scrollWidth - scroll.clientWidth;
      if (maximum <= 1 || pause-- > 0) return;
      scroll.scrollLeft = Math.max(0, Math.min(maximum, scroll.scrollLeft + direction * 4));
      if (scroll.scrollLeft >= maximum || scroll.scrollLeft <= 0) {
        direction *= -1;
        pause = 30;
      }
    }, 100);
    return () => window.clearInterval(timer);
  }, [routeKey, pinnedKey, overflowing]);

  function renderStop(stop: StationTime, index: number) {
    const current = !!station.trim() && normalize(stop.station) === normalize(station);
    const seen = observed && normalize(stop.station) === normalize(observed.station);
    const actual = stop.actual && clock.test(stop.actual) ? stop.actual : "";
    const estimated = stop.estimated && clock.test(stop.estimated) ? stop.estimated : "";
    const scheduled = stop.scheduled && clock.test(stop.scheduled) ? stop.scheduled : "";
    const time = stop.cancelled ? "Cancelled" : actual || estimated || scheduled || "--";
    const label = stop.cancelled ? "Cancelled" : actual ? `Actual ${actual}` :
      estimated ? `Estimated ${estimated}` : scheduled ? `Scheduled ${scheduled}` : "Time not reported";
    return (
      <div className={`train-recent-stop${current ? " train-recent-stop-current" : ""}${seen ? " train-stop-observed" : ""}`}
        aria-current={current ? "location" : undefined} key={`${stop.station}-${index}`}>
        <strong title={stop.station}>{stop.station}</strong>
        <span title={label} aria-label={label}>{time}{!stop.cancelled && !actual && estimated ? " est" : ""}</span>
      </div>
    );
  }

  return (
    <>
    <div ref={gridRef} className={`train-recent-stops${overflowing ? " train-route-overflow" : ""}`}>
      {overflowing ? <>
        {route.map((stop, index) => pinned.has(index) ? renderStop(stop, index) : null)}
        <div ref={scrollRef} className="train-route-scroll" role="group"
          aria-label="Other calling stations" tabIndex={0}>
          <div className="train-route-track">
            {route.map((stop, index) => pinned.has(index) ? null : renderStop(stop, index))}
          </div>
        </div>
      </> : route.map(renderStop)}
    </div>
    {observed && showLastSeen && <div className="train-route-meta">
      <p className="train-last-seen">
        Last seen: <span title={observed.station}>{observed.station}</span> at <time>{observed.actual}</time>
      </p>
    </div>}
    </>
  );
}
