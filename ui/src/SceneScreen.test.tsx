import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { SceneScreen } from "./SceneScreen";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

test.each(["weather", "dashboard"])("shows current weather and forecast in %s scenes", async (kind) => {
  const fetch = vi.fn(async (path: string) => ({
    ok: true,
    json: async () => path === "/api/scene"
      ? { kind, revision: "weather-one", layout: "clock", text: "", show_weather: kind === "dashboard" }
      : path === "/api/weather"
        ? { location: "St Albans, GB", temperature: 12, feels_like: 10, humidity: 80,
            wind_kmh: 7.2, description: "light rain", observed_at: 100,
            forecast: [{ date: "2026-11-08", low: 8, high: 14, description: "cloudy" }] }
        : {},
  }));
  vi.stubGlobal("fetch", fetch);
  render(<SceneScreen />);
  expect(await screen.findByText("St Albans, GB")).toBeTruthy();
  expect(screen.getByText("12 °C")).toBeTruthy();
  expect(screen.getByText("light rain")).toBeTruthy();
  expect(screen.getByText("8–14 °C")).toBeTruthy();
  expect(fetch).toHaveBeenCalledWith("/api/weather", expect.anything());
});

test("colours departure statuses and displays station messages as text", async () => {
  vi.stubGlobal("fetch", vi.fn(async (path: string) => ({
    ok: true,
    json: async () => path === "/api/scene"
      ? { kind: "train", revision: "colours", scene_id: "hww", title: "How Wood", text: "" }
      : path === "/api/weather" ? {}
      : {
          rows: [
            { scheduled: "12:00", destination: "A", status: "Cancelled", state: "cancelled" },
            { scheduled: "12:10", destination: "B", estimated: "12:15", state: "delayed" },
            { scheduled: "12:20", destination: "C", estimated: "On time", state: "on-time" },
          ],
          alerts: ["Please use platform 1", "<b>Not markup</b>"],
        },
  })));
  render(<SceneScreen />);
  expect((await screen.findByText("Cancelled")).className).toBe("train-cancelled");
  expect(screen.getByText("12:15").className).toBe("train-delayed");
  expect(screen.getByText("12:20").className).toBe("train-on-time");
  expect(screen.getByText("Please use platform 1")).toBeTruthy();
  expect(screen.getByText("<b>Not markup</b>")).toBeTruthy();
  expect(document.querySelector(".train-alerts b")).toBeNull();
});

test("updates a dashboard in place and acknowledges only new revisions", async () => {
  vi.useFakeTimers();
  let revision = "one";
  const fetch = vi.fn(async (path: string) => ({
    ok: true,
    json: async () => path === "/api/scene"
      ? { kind: "dashboard", revision, layout: "notice", text: revision }
      : {},
  }));
  vi.stubGlobal("fetch", fetch);
  const { unmount } = render(<SceneScreen />);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(screen.getByText("one")).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(500); });
  expect(fetch.mock.calls.filter(([path]) => path.includes("/ready"))).toHaveLength(1);
  revision = "two";
  await act(async () => { await vi.advanceTimersByTimeAsync(500); });
  expect(screen.getByText("two")).toBeTruthy();
  expect(fetch.mock.calls.filter(([path]) => path.includes("/ready"))).toHaveLength(2);
  unmount();
  const calls = fetch.mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(fetch.mock.calls).toHaveLength(calls);
});

test("renders notice text as text and acknowledges rendering without loading admin APIs", async () => {
  const fetch = vi.fn(async (path: string) => ({
    ok: true,
    json: async () => path === "/api/scene"
      ? { kind: "dashboard", revision: "one", layout: "notice", text: "<b>Welcome</b>" }
      : {},
  }));
  vi.stubGlobal("fetch", fetch);
  render(<SceneScreen />);
  expect(await screen.findByText("<b>Welcome</b>")).toBeTruthy();
  expect(document.querySelector(".scene-notice b")).toBeNull();
  await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/scene/ready?revision=one", expect.anything()));
  expect(fetch.mock.calls.every(([path]) => path.startsWith("/api/scene"))).toBe(true);
});

test("retries a failed dashboard acknowledgement without overlapping requests", async () => {
  vi.useFakeTimers();
  let attempts = 0;
  const fetch = vi.fn(async (path: string) => {
    if (path.includes("/ready")) {
      attempts += 1;
      return { ok: attempts > 1, json: async () => ({}) };
    }
    return {
      ok: true,
      json: async () => ({ kind: "dashboard", revision: "one", layout: "clock", text: "" }),
    };
  });
  vi.stubGlobal("fetch", fetch);
  const { unmount } = render(<SceneScreen />);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(attempts).toBe(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(attempts).toBe(2);
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(attempts).toBe(2);
  unmount();
});

test("aborts a stalled acknowledgement and retries without overlapping requests", async () => {
  vi.useFakeTimers();
  let attempts = 0;
  const fetch = vi.fn((path: string, options?: RequestInit) => {
    if (path.includes("/ready")) {
      attempts += 1;
      if (attempts === 1) {
        return new Promise<Response>((_resolve, reject) => {
          options?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
        });
      }
      return Promise.resolve(new Response("{}"));
    }
    return Promise.resolve(new Response(JSON.stringify({
      kind: "dashboard", revision: "one", layout: "clock", text: "",
    })));
  });
  vi.stubGlobal("fetch", fetch);
  const { unmount } = render(<SceneScreen />);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(attempts).toBe(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(attempts).toBe(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(attempts).toBe(2);
  unmount();
});

test("recovery explains that failed scenes will retry", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({
    ok: true, json: async () => ({ kind: "recovery", revision: "two", layout: "notice", text: "" }),
  })));
  render(<SceneScreen />);
  expect(await screen.findByText("No carousel scenes are currently available")).toBeTruthy();
  expect(screen.getByText(/retried automatically/)).toBeTruthy();
});

test("renders cached train departures and next-service delay", async () => {
  vi.stubGlobal("fetch", vi.fn(async (path: string) => ({
    ok: true,
    json: async () => path === "/api/scene"
      ? {
        kind: "train", revision: "train-one", scene_id: "pad", title: "Paddington",
        station_crs: "PAD", layout: "clock", text: "",
      }
      : path === "/api/weather" ? {
        location: "St Albans, GB", temperature: 12, feels_like: 10, description: "cloudy",
        humidity: 80, wind_kmh: 7, forecast: [
          { date: "2026-11-08", low: 8, high: 14, description: "rain tomorrow" },
        ],
      }
      : {
        station: "London Paddington",
        platform_available: true,
        next: {
          status: "Delayed by 7 min", delay_minutes: 7,
          calling_at: [[{
            station: "Reading", scheduled: "10:25", estimated: "10:32",
            actual: "", cancelled: false,
          }]],
          last_seen: { station: "Ealing Broadway", actual: "10:08" },
          previous_stops: [[
            { station: "Previous station", scheduled: "09:50", estimated: "",
              actual: "09:52", cancelled: false },
            { station: "Unobserved station", scheduled: "09:55", estimated: "On time",
              actual: "", cancelled: false },
          ]],
        },
        rows: [
          { scheduled: "10:00", destination: "Bristol", platform: "4", status: "Delayed by 7 min" },
          { scheduled: "10:15", destination: "Oxford", platform: "5", estimated: "On time" },
        ],
      },
  })));
  render(<SceneScreen />);
  expect(await screen.findByText("London Paddington")).toBeTruthy();
  expect(screen.getByText("Delayed by 7 min")).toBeTruthy();
  expect(screen.getByText("Bristol")).toBeTruthy();
  expect(screen.getByText(/Reading \(10:25; expected 10:32\)/)).toBeTruthy();
  expect(screen.getByText(/Ealing Broadway at 10:08/)).toBeTruthy();
  expect(screen.getByText("Previous stops:")).toBeTruthy();
  expect(screen.getByText(/Previous station \(actual 09:52\)/)).toBeTruthy();
  expect(screen.getByText(/Unobserved station \(09:55\)/)).toBeTruthy();
  const clock = document.querySelector(".train-current-time");
  expect(clock?.textContent).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  expect(await screen.findByText("St Albans, GB")).toBeTruthy();
  expect(screen.getByText("12 °C")).toBeTruthy();
  expect(screen.queryByText("rain tomorrow")).toBeNull();
  expect(fetch).toHaveBeenCalledWith("/api/train?scene_id=pad", expect.anything());
});
