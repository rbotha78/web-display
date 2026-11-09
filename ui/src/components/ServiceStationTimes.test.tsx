import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ServiceStationTimes } from "./ServiceStationTimes";
import { RecentServiceContent } from "./RecentServiceContent";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const stops = Array.from({ length: 27 }, (_, index) => ({
  station: `Station ${index}`, scheduled: "12:00", actual: index === 20 ? "12:01" : "",
}));
const lastSeen = { station: "Station 20", actual: "12:01" };

test("scrolls the full route back and forth while selected and observed stations stay pinned", () => {
  vi.useFakeTimers();
  const { container, unmount } = render(
    <ServiceStationTimes stops={stops} station="Station 13" lastSeen={lastSeen} />,
  );
  const scroll = screen.getByRole("group", { name: "Other calling stations" });
  Object.defineProperties(scroll, {
    scrollWidth: { configurable: true, value: 380 },
    clientWidth: { configurable: true, value: 300 },
  });
  const selected = container.querySelector('[aria-current="location"]')!;
  const observed = container.querySelector(".train-stop-observed")!;
  expect(selected.querySelector("strong")?.textContent).toBe("Station 13");
  expect(observed.querySelector("strong")?.textContent).toBe("Station 20");
  expect(scroll.contains(selected)).toBe(false);
  expect(scroll.contains(observed)).toBe(false);
  expect([...scroll.querySelectorAll("strong")].map((cell) => cell.textContent))
    .toEqual(stops.filter((stop) => !["Station 13", "Station 20"].includes(stop.station))
      .map((stop) => stop.station));
  act(() => { vi.advanceTimersByTime(3000); });
  expect(scroll.scrollLeft).toBe(0);
  act(() => { vi.advanceTimersByTime(2000); });
  expect(scroll.scrollLeft).toBe(80);
  act(() => { vi.advanceTimersByTime(3000); });
  expect(scroll.scrollLeft).toBe(80);
  act(() => { vi.advanceTimersByTime(2000); });
  expect(scroll.scrollLeft).toBe(0);
  expect(screen.queryByLabelText("Calling stations page")).toBeNull();
  expect(container.querySelector("[style]")).toBeNull();
  unmount();
  expect(vi.getTimerCount()).toBe(0);
});

test("timing refreshes preserve scrolling, but a different service resets its position", () => {
  vi.useFakeTimers();
  const { rerender } = render(<ServiceStationTimes stops={stops} station="Station 13" />);
  const scroll = screen.getByRole("group", { name: "Other calling stations" });
  Object.defineProperties(scroll, {
    scrollWidth: { configurable: true, value: 1200 },
    clientWidth: { configurable: true, value: 300 },
  });
  act(() => { vi.advanceTimersByTime(4000); });
  expect(scroll.scrollLeft).toBe(40);
  rerender(<ServiceStationTimes
    stops={stops.map((stop) => ({ ...stop, estimated: "12:05" }))} station="Station 13" />);
  act(() => { vi.advanceTimersByTime(1000); });
  expect(scroll.scrollLeft).toBe(80);
  rerender(<ServiceStationTimes
    stops={stops.map((stop) => ({ ...stop, scheduled: "13:00" }))} station="Station 13" />);
  expect(scroll.scrollLeft).toBe(0);
  rerender(<ServiceStationTimes stops={stops.slice(0, 3)} station="Station 0" />);
  expect(screen.queryByRole("group", { name: "Other calling stations" })).toBeNull();
  expect(vi.getTimerCount()).toBe(0);
});

test("scrolls only while the route overflows its container and keeps the highlights on resize", () => {
  let width = 560;
  let resize = () => {};
  const disconnect = vi.fn();
  vi.stubGlobal("ResizeObserver", class {
    constructor(callback: ResizeObserverCallback) {
      resize = () => callback([], this);
    }
    observe() {}
    unobserve() {}
    disconnect = disconnect;
  });
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
    () => new DOMRect(0, 0, width, 100),
  );
  const style = window.getComputedStyle(document.body);
  style.fontSize = "16px";
  style.columnGap = "8px";
  vi.spyOn(window, "getComputedStyle").mockReturnValue(style);
  const { container, unmount } = render(
    <ServiceStationTimes stops={stops} station="Station 13" lastSeen={lastSeen} />,
  );
  expect(screen.getByRole("group", { name: "Other calling stations" })).toBeTruthy();
  width = 4000;
  act(() => { resize(); });
  expect(screen.queryByRole("group", { name: "Other calling stations" })).toBeNull();
  expect([...container.querySelectorAll("strong")].map((cell) => cell.textContent))
    .toEqual(stops.map((stop) => stop.station));
  width = 100;
  act(() => { resize(); });
  expect(screen.getByRole("group", { name: "Other calling stations" })).toBeTruthy();
  expect(container.querySelector('[aria-current="location"] strong')?.textContent).toBe("Station 13");
  expect(container.querySelector(".train-stop-observed strong")?.textContent).toBe("Station 20");
  unmount();
  expect(disconnect).toHaveBeenCalledOnce();
});

test("pins only one cell when selected and last-seen stations are the same", () => {
  const { container } = render(
    <ServiceStationTimes stops={stops} station="Station 20" lastSeen={lastSeen} />,
  );
  const scroll = screen.getByRole("group", { name: "Other calling stations" });
  const pinned = container.querySelectorAll(".train-route-overflow > .train-recent-stop");
  expect(pinned).toHaveLength(1);
  expect(pinned[0].classList.contains("train-recent-stop-current")).toBe(true);
  expect(pinned[0].classList.contains("train-stop-observed")).toBe(true);
  expect(scroll.querySelector(".train-stop-observed")).toBeNull();
  expect(container.querySelectorAll(".train-recent-stop")).toHaveLength(stops.length);
});

test("previous service displays only the three selected stops, including terminal arrival", () => {
  const route = stops.map((stop) => ({
    ...stop, estimated: "", cancelled: false, delay_minutes: null, timing: "reported" as const,
  }));
  const { container } = render(<RecentServiceContent station="Station 13" recent={{
    scheduled: "12:00", actual: "", destination: "Station 26", platform: "", operator: "",
    status: "", delay_minutes: null, last_seen: lastSeen, updated_at: 100, stale: false, details_error: "",
    stops: route, display_stops: [route[13], route[20], { ...route[26], timing: "arrival" }],
  }} />);
  expect([...container.querySelectorAll("strong")].map((cell) => cell.textContent))
    .toEqual(["Station 13", "Station 20", "Station 26"]);
  expect(container.querySelector('[aria-current="location"] strong')?.textContent).toBe("Station 13");
  expect(screen.queryByRole("group", { name: "Other calling stations" })).toBeNull();
  expect(screen.queryByText("Station 0")).toBeNull();
});
