import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { SceneScreen } from "./SceneScreen";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

test.each([undefined, true, false])(
  "respects previous departure visibility without hiding upcoming services (%s)", async (showPrevious) => {
    vi.stubGlobal("fetch", vi.fn(async (path: string) => ({
      ok: true,
      json: async () => path === "/api/scene"
        ? { kind: "train", revision: "visibility", scene_id: "pad", station_crs: "PAD", text: "",
            show_previous_departure: showPrevious }
        : path === "/api/weather" ? {}
        : { station: "Paddington", rows: [
              { scheduled: "12:00", destination: "Reading", estimated: "On time" },
              { scheduled: "12:30", destination: "Oxford", estimated: "On time" },
            ],
            recent: { actual: "", display_stops: [{ station: "Earlier train", scheduled: "11:30" }] } },
    })));
    render(<SceneScreen />);
    expect(await screen.findByRole("region", { name: "Next service" })).toBeTruthy();
    expect(screen.getByRole("table", { name: "Later departures" }).textContent).toContain("Oxford");
    const previous = screen.queryByRole("region", { name: "Previous scheduled service" });
    if (showPrevious === false) {
      expect(previous).toBeNull();
      expect(document.querySelector(".train-recent")).toBeNull();
    } else {
      expect(previous?.textContent).toContain("Earlier train11:30");
    }
  },
);

test.each([null, { station: "Long station", actual: "On time" }])(
  "keeps the board station highlighted without inventing a last-seen location (%j)", async (lastSeen) => {
  vi.stubGlobal("fetch", vi.fn(async (path: string) => ({
    ok: true,
    json: async () => path === "/api/scene"
      ? { kind: "train", revision: "scroll", scene_id: "hww", title: "How Wood", text: "" }
      : path === "/api/weather" ? {}
      : { rows: [{ scheduled: "12:00", destination: "Watford" }],
          next: {
            last_seen: lastSeen,
            previous_stops: [[{ station: "Short station", scheduled: "11:50" }]],
            calling_at: [
              [{ station: "Long station", scheduled: "12:10" }],
              [{ station: "Branch station", scheduled: "12:15" }],
            ],
          } },
  })));
  render(<SceneScreen />);
  const route = await screen.findByRole("group", { name: "Next service station times" });
  expect([...route.querySelectorAll("strong")].map((element) => element.textContent))
    .toEqual(["Short station", "How Wood", "Long station", "Branch station"]);
  expect(route.querySelector('[aria-current="location"]')?.textContent).toBe("How Wood12:00");
  expect(route.querySelector(".train-stop-observed")).toBeNull();
  expect(route.querySelector(".train-last-seen")).toBeNull();
  expect(document.querySelector(".train-stop-scroll")).toBeNull();
});

test.each([["train", 0], ["weather", 10], ["dashboard", 5], ["recovery", 8]])(
  "applies the published safe area to %s scenes without inline styles", async (kind, percent) => {
    vi.stubGlobal("fetch", vi.fn(async (path: string) => ({
      ok: true,
      json: async () => path === "/api/scene"
        ? { kind, revision: "safe", layout: "notice", text: "Local notice", safe_area_percent: percent }
        : {},
    })));
    render(<SceneScreen />);
    await waitFor(() => expect(screen.getByRole("main").classList.contains(`safe-area-${percent}`)).toBe(true));
    expect(screen.getByRole("main").getAttribute("style")).toBeNull();
  },
);

test("scrolls only later departures and notices while next and previous services remain fixed", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn(async (path: string) => ({
    ok: true,
    json: async () => path === "/api/scene"
      ? { kind: "train", revision: "long", scene_id: "station", title: "Station", text: "" }
      : path === "/api/train?scene_id=station"
        ? { rows: [{ scheduled: "12:00", destination: "Long destination", status: "On time" },
                   { scheduled: "12:30", destination: "Later destination", estimated: "On time" },
                   { scheduled: "13:00", destination: "Final destination", estimated: "On time" }],
            alerts: ["Full station notice"], next: { calling_at: [[{ station: "Last calling point" }]] },
            recent: { actual: "", stops: [{ station: "Fixed station", scheduled: "11:00" }] } }
        : {},
  })));
  await act(async () => { render(<SceneScreen />); });
  const content = screen.getByLabelText("Departures and notices");
  Object.defineProperties(content, {
    scrollHeight: { configurable: true, value: 1200 },
    clientHeight: { configurable: true, value: 300 },
  });
  await act(async () => { vi.advanceTimersByTime(6000); });
  expect(content.scrollTop).toBeGreaterThan(0);
  expect(screen.getByText(/scrolling automatically/)).toBeTruthy();
  expect(screen.getByText("Full station notice")).toBeTruthy();
  expect(screen.getByText(/Last calling point/)).toBeTruthy();
  const next = screen.getByRole("region", { name: "Next service" });
  expect(content.contains(next)).toBe(false);
  expect(next.parentElement?.className).toBe("train-board");
  expect(next.textContent).toContain("Long destination");
  expect(next.textContent).toContain("Last calling point");
  expect(next.textContent).not.toContain("Later destination");
  const later = screen.getByRole("table", { name: "Later departures" });
  expect(content.contains(later)).toBe(true);
  expect(later.querySelectorAll("tbody > tr")).toHaveLength(2);
  expect(later.textContent).not.toContain("Long destination");
  expect(later.classList.contains("train-subsequent")).toBe(true);
  const previous = screen.getByRole("region", { name: "Previous scheduled service" });
  expect(content.contains(previous)).toBe(false);
  expect(previous.parentElement?.className).toBe("train-board");
  expect(previous.scrollTop).toBe(0);
});

test("shows only station times for the recent service, omitting separate arrivals and commentary", async () => {
  vi.stubGlobal("fetch", vi.fn(async (path: string) => ({
    ok: true,
    json: async () => path === "/api/scene"
      ? { kind: "train", revision: "recent", scene_id: "hww", title: "How Wood", text: "" }
      : path === "/api/weather" ? {}
      : {
          station: "How Wood",
          rows: [{ scheduled: "12:30", destination: "Next service", estimated: "On time" }],
          alerts: ["Station notice"],
          recent: {
            scheduled: "11:55", actual: "12:02", destination: "Terminus",
            status: "Arrived at Terminus at 12:36", delay_minutes: 7,
            last_seen: { station: "Terminus", actual: "12:36" }, stale: false,
            stops: [
              { station: "How Wood", timing: "arrival", scheduled: "11:54", actual: "12:01",
                estimated: "", delay_minutes: 7, cancelled: false },
              { station: "How Wood", timing: "departure", scheduled: "11:55", actual: "12:02",
                estimated: "", delay_minutes: 7, cancelled: false },
              { station: "Next stop", timing: "reported", scheduled: "12:10", actual: "",
                estimated: "12:15", delay_minutes: null, cancelled: false },
              { station: "Cancelled stop", timing: "reported", scheduled: "12:20", actual: "",
                estimated: "", delay_minutes: null, cancelled: true },
              { station: "Terminus", timing: "arrival", scheduled: "12:30", actual: "",
                estimated: "12:36", delay_minutes: null, cancelled: false },
            ],
          },
        },
  })));
  render(<SceneScreen />);
  const recent = await screen.findByRole("region", { name: "Most recent departure" });
  await waitFor(() => expect(recent.textContent).toContain("How Wood12:02"));
  expect(screen.getByLabelText("Actual 12:02").textContent).toBe("12:02");
  expect(screen.getByLabelText("Estimated 12:15").textContent).toBe("12:15 est");
  expect(recent.textContent).toContain("Cancelled stopCancelled");
  expect(recent.textContent).not.toMatch(/late|Last actual|Arrival|No actual|Expected|Terminus|12:01/);
  expect(recent.querySelectorAll(".train-recent-stop")).toHaveLength(3);
  expect(recent.querySelectorAll('[aria-current="location"]')).toHaveLength(1);
  expect(recent.querySelector(".train-recent-stop-current")?.textContent).toBe("How Wood12:02");
  expect(document.querySelector(".train-content")?.contains(recent)).toBe(false);
  expect(recent.parentElement?.className).toBe("train-board");
  expect(screen.getByText("Station notice").compareDocumentPosition(recent) &
    Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test("explains recent departure is unavailable without treating schedules as departures", async () => {
  vi.stubGlobal("fetch", vi.fn(async (path: string) => ({
    ok: true,
    json: async () => path === "/api/scene"
      ? { kind: "train", revision: "no-recent", scene_id: "hww", title: "How Wood", text: "" }
      : path === "/api/weather" ? {}
      : { rows: [{ scheduled: "12:30", estimated: "12:35" }],
          recent_error: "Waiting for a confirmed departure observed since startup" },
  })));
  render(<SceneScreen />);
  const recent = await screen.findByRole("region", { name: "Most recent departure" });
  await waitFor(() => expect(recent.textContent).toContain("Waiting for a confirmed departure"));
  expect(recent.textContent).not.toContain("Actual departure 12:30");
});

test("labels API look-back data as a previous scheduled service without claiming departure or lateness", async () => {
  vi.stubGlobal("fetch", vi.fn(async (path: string) => ({
    ok: true,
    json: async () => path === "/api/scene"
      ? { kind: "train", revision: "look-back", scene_id: "hww", title: "How Wood", text: "" }
      : path === "/api/weather" ? {}
      : {
          station: "How Wood", rows: [],
          recent: {
            scheduled: "08:57", actual: "", destination: "St Albans Abbey",
            status: "Departure not confirmed", delay_minutes: null, last_seen: null,
            stops: [{ station: "Park Street", scheduled: "08:59", actual: "",
              estimated: "On time", delay_minutes: null, timing: "reported", cancelled: false }],
          },
        },
  })));
  render(<SceneScreen />);
  const previous = await screen.findByRole("region", { name: "Previous scheduled service" });
  expect(previous.textContent).toContain("Park Street08:59");
  expect(screen.getByLabelText("Scheduled 08:59").textContent).toBe("08:59");
  expect(previous.textContent).not.toMatch(/not confirmed|not reported|Lateness|Expected|On time/);
  expect(previous.querySelector("[aria-current]")).toBeNull();
  expect(screen.queryByRole("heading", { name: "Most recent departure" })).toBeNull();
});

test.each(["weather", "dashboard"])("shows current weather and forecast in %s scenes", async (kind) => {
  const fetch = vi.fn(async (path: string) => ({
    ok: true,
    json: async () => path === "/api/scene"
      ? { kind, revision: "weather-one", layout: "clock", text: "", show_weather: kind === "dashboard" }
      : path === "/api/weather"
        ? { location: "St Albans, GB", temperature: 12, feels_like: 10, humidity: 80,
            wind_kmh: 7.2, description: "light rain", icon: "10d", observed_at: 100,
            forecast: [{ date: "2026-11-08", low: 8, high: 14, description: "cloudy", icon: "04d" }] }
        : {},
  }));
  vi.stubGlobal("fetch", fetch);
  render(<SceneScreen />);
  expect(await screen.findByText("St Albans, GB")).toBeTruthy();
  expect(screen.getByText("12 °C")).toBeTruthy();
  expect(screen.getByText("light rain")).toBeTruthy();
  expect(screen.getByText("8–14 °C")).toBeTruthy();
  expect(document.querySelector(".weather-current .weather-icon-rain")).toBeTruthy();
  expect(document.querySelector(".weather-forecast .weather-icon-cloud")).toBeTruthy();
  expect(fetch).toHaveBeenCalledWith("/api/weather", expect.anything());
});

test.each(["state", "flag"])("hides cancelled-service stops using the %s and displays coloured statuses", async (cancellation) => {
  vi.stubGlobal("fetch", vi.fn(async (path: string) => ({
    ok: true,
    json: async () => path === "/api/scene"
      ? { kind: "train", revision: "colours", scene_id: "hww", title: "How Wood", text: "" }
      : path === "/api/weather" ? {}
      : {
          platform_available: true,
          next: {
            previous_stops: [[{ station: "Previous stop", scheduled: "11:50" }]],
            calling_at: [[{ station: "Upcoming stop", scheduled: "12:10" }]],
            last_seen: { station: "Reported station", actual: "11:45" },
          },
          rows: [
            { scheduled: "12:00", destination: "A", status: "Cancelled",
              state: cancellation === "state" ? "cancelled" : "",
              cancelled: cancellation === "flag", service_type: "bus",
              alerts: ["Cancellation reason: Staff shortage", "<b>Use local buses</b>"] },
            { scheduled: "12:10", destination: "B", estimated: "12:15", state: "delayed" },
            { scheduled: "12:20", destination: "C", estimated: "On time", state: "on-time" },
          ],
          alerts: ["Please use platform 1", "<b>Not markup</b>"],
        },
  })));
  render(<SceneScreen />);
  expect((await screen.findByText("Cancelled")).className).toBe("train-cancelled");
  expect(screen.getByText("12:15").className).toBe("train-delayed");
  expect(screen.getByText("12:20").className).toBe("");
  expect(screen.getByText("On time").className).toBe("train-on-time");
  expect(screen.getByText("Rail replacement bus")).toBeTruthy();
  expect(screen.getByText("Bus stop")).toBeTruthy();
  expect(screen.getByText("Cancellation reason: Staff shortage")).toBeTruthy();
  expect(screen.getByText("<b>Use local buses</b>")).toBeTruthy();
  expect(document.querySelector(".train-service-notices b")).toBeNull();
  expect(screen.queryByText("Previous stops:")).toBeNull();
  expect(screen.queryByText("Calling at:")).toBeNull();
  expect(screen.queryByText("Previous stop")).toBeNull();
  expect(screen.queryByText("Upcoming stop")).toBeNull();
  const route = screen.getByRole("group", { name: "Next service station times" });
  expect(route.querySelector(".train-recent-stops")?.textContent).toBe("Reported station11:45");
  expect(route.querySelector(".train-last-seen")?.textContent).toBe("Last seen: Reported station at 11:45");
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
          last_seen: { station: "Previous station", actual: "09:52" },
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
  expect(await screen.findByRole("heading", { name: "London Paddington" })).toBeTruthy();
  expect(screen.getByText("Delayed by 7 min")).toBeTruthy();
  expect(screen.getByText("Bristol")).toBeTruthy();
  const route = screen.getByRole("group", { name: "Next service station times" });
  expect(route.textContent).toContain("Reading10:32 est");
  expect(route.textContent).toContain("Previous station09:52");
  expect(route.textContent).toContain("Unobserved station09:55");
  expect(route.querySelector('[aria-current="location"]')?.textContent).toBe("London Paddington10:00");
  expect(route.querySelector(".train-stop-observed")?.textContent).toBe("Previous station09:52");
  expect(route.querySelector(".train-last-seen")?.textContent).toBe("Last seen: Previous station at 09:52");
  expect(route.querySelectorAll('[aria-current="location"]')).toHaveLength(1);
  expect(screen.getByLabelText("Scheduled 10:00").parentElement?.hasAttribute("aria-current")).toBe(true);
  expect(route.textContent).not.toMatch(/Previous stops:|Calling at:|Last reported/);
  const clock = document.querySelector(".train-current-time");
  expect(clock?.textContent).toMatch(/^\d{2}:\d{2}:\d{2}$/);
  expect(await screen.findByText("St Albans, GB")).toBeTruthy();
  expect(screen.getByText("12 °C")).toBeTruthy();
  expect(screen.queryByText("rain tomorrow")).toBeNull();
  expect(fetch).toHaveBeenCalledWith("/api/train?scene_id=pad", expect.anything());
});

test("now playing scene shows the song details and proxied album cover", async () => {
  vi.stubGlobal("fetch", vi.fn(async (path: string) => ({
    ok: true,
    json: async () => path === "/api/scene"
      ? { kind: "music", revision: "music", scene_id: "", title: "", text: "" }
      : path === "/api/music"
        ? { state: "playing", title: "Ride on Time", artist: "Black Box", album: "Dreamland", art: "0123456789abcdef" }
        : {},
  })));
  render(<SceneScreen />);
  expect(await screen.findByRole("heading", { name: "Ride on Time" })).toBeTruthy();
  expect(screen.getByText("Black Box")).toBeTruthy();
  expect(screen.getByText("Dreamland")).toBeTruthy();
  const cover = screen.getByRole("img", { name: "Cover of Dreamland" }) as HTMLImageElement;
  expect(cover.getAttribute("src")).toBe("/api/music/art?v=0123456789abcdef");
});

test("now playing scene falls back without a cover or music", async () => {
  let music = { state: "playing", title: "Song", artist: "", album: "", art: "" };
  vi.stubGlobal("fetch", vi.fn(async (path: string) => ({
    ok: true,
    json: async () => path === "/api/scene"
      ? { kind: "music", revision: "music", scene_id: "", title: "", text: "" } : music,
  })));
  render(<SceneScreen />);
  expect(await screen.findByRole("heading", { name: "Song" })).toBeTruthy();
  expect(screen.queryByRole("img")).toBeNull();
  music = { state: "disabled", title: "", artist: "", album: "", art: "" };
  expect(await screen.findByRole("heading", { name: "No music playing" }, { timeout: 4000 })).toBeTruthy();
});
