import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { RemoteView } from "../api";
import { DeviceCard } from "./DeviceCard";
import { RemotePreview } from "./RemotePreview";

const initial: RemoteView = {
  available: true, mode: "carousel", updated: 1794207600,
  device_time: "2026-11-09T15:00:00+09:00", timezone: "JST",
  scene: { id: "notice", kind: "dashboard", layout: "clock-notice", text: "<script>Only text</script>" },
  train: null, weather: null,
  sleep: { sleeping: false, scheduled_sleep: false, wake_until: null }, message: "",
};

function reply(value: unknown = initial, ok = true) {
  return { ok, status: ok ? 200 : 503, json: async () => value } as Response;
}

async function settle() {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("polls only when open, switches active scenes, ticks in device time and never loads web or media", async () => {
  vi.useFakeTimers();
  let value = initial;
  const fetch = vi.fn(async () => reply(value));
  vi.stubGlobal("fetch", fetch);
  const card = render(<DeviceCard
    device={{ id: "aabbccddeeff", name: "Lobby", host: "10.0.0.1", port: 8443, online: false }}
    notify={vi.fn()} refresh={vi.fn()} readd={vi.fn()} />);
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Preview display"));
  await settle();
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(screen.getByText("<script>Only text</script>")).toBeTruthy();
  expect(card.container.querySelector("script")).toBeNull();
  expect(screen.getByText("15:00:00")).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(screen.getByText("15:00:01")).toBeTruthy();
  value = { ...initial, scene: { id: "website", kind: "web", title: "Website", url: "https://example.com/page" } };
  await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
  expect(screen.getByText("https://example.com/page")).toBeTruthy();
  expect(screen.queryByText("<script>Only text</script>")).toBeNull();
  value = { ...initial, scene: { id: "movie", kind: "video", title: "Film", source: "plex", audio_enabled: false } };
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(screen.getByText("Film")).toBeTruthy();
  expect(screen.getByText(/Video metadata only/)).toBeTruthy();
  expect(card.container.querySelector("iframe, video, audio, embed, object, img")).toBeNull();
  for (const [path] of fetch.mock.calls as unknown as [string][]) {
    expect(path).toBe("/api/devices/aabbccddeeff/remote-view");
  }
  fireEvent.click(screen.getByText("Close preview"));
  expect(screen.queryByRole("dialog")).toBeNull();
  const count = fetch.mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(20000); });
  expect(fetch).toHaveBeenCalledTimes(count);
});

test("does not overlap pending polls and aborts in-flight requests on close", async () => {
  vi.useFakeTimers();
  let resolve!: (value: Response) => void;
  const fetch = vi.fn((_path: string, _init: RequestInit) => new Promise<Response>((done) => { resolve = done; }));
  vi.stubGlobal("fetch", fetch);
  const view = render(<RemotePreview deviceId="aabbccddeeff" deviceName="Lobby" close={vi.fn()} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
  expect(fetch).toHaveBeenCalledTimes(1);
  const signal = fetch.mock.calls[0][1].signal;
  view.unmount();
  expect(signal?.aborted).toBe(true);
  await act(async () => { resolve(reply()); await Promise.resolve(); });
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  expect(fetch).toHaveBeenCalledTimes(1);
});

test("hides the old scene after errors and recovers when polling succeeds", async () => {
  vi.useFakeTimers();
  const fetch = vi.fn().mockResolvedValueOnce(reply())
    .mockResolvedValueOnce(reply({ error: "Kiosk heartbeat is stale" }, false))
    .mockResolvedValue(reply({ ...initial, mode: "recovery", scene: null, message: "Setup access point active" }));
  vi.stubGlobal("fetch", fetch);
  render(<RemotePreview deviceId="aabbccddeeff" deviceName="Lobby" close={vi.fn()} />);
  await settle();
  expect(screen.getByText("<script>Only text</script>")).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(screen.getByRole("alert").textContent).toContain("Kiosk heartbeat is stale");
  expect(screen.queryByText("<script>Only text</script>")).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(screen.getByText("Setup access point active")).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
});

test("renders train and weather from the same snapshot without extra requests", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => reply({
    ...initial,
    scene: { id: "train", kind: "train", station_crs: "PAD" },
    train: { crs: "PAD", station: "Paddington", rows: [
      { scheduled: "15:30", destination: "Reading", status: "On time", platform: "4" },
    ], alerts: ["<b>Station notice</b>"],
    next: {
      scheduled: "15:30", estimated: "15:33", actual: "",
      last_seen: { station: "Earlier stop", actual: "15:21" },
      previous_stops: [[{ station: "Earlier stop", actual: "15:21" }]],
      calling_at: [[{ station: "Upcoming stop", scheduled: "15:40", estimated: "On time" }]],
    },
    recent: {
      scheduled: "15:00", actual: "15:04", destination: "Oxford", status: "Departed at 15:04",
      delay_minutes: 4, stale: false, details_error: "",
      last_seen: { station: "Oxford", actual: "16:00" },
      stops: [{ station: "Paddington", timing: "departure", scheduled: "15:00", actual: "15:04",
        estimated: "", delay_minutes: 4, cancelled: false },
        { station: "Oxford", timing: "arrival", scheduled: "15:55", actual: "16:00",
        estimated: "", delay_minutes: 5, cancelled: false }],
    } },
    weather: { location: "London", temperature: 12, description: "Clear sky", icon: "01d" },
  })));
  render(<RemotePreview deviceId="aabbccddeeff" deviceName="Lobby" close={vi.fn()} />);
  await settle();
  expect(screen.getByRole("heading", { name: "Paddington" })).toBeTruthy();
  expect(screen.getByText("Reading")).toBeTruthy();
  expect(screen.getByText("<b>Station notice</b>")).toBeTruthy();
  const route = screen.getByRole("group", { name: "Next service station times" });
  expect(route.querySelector(".train-recent-stops")?.textContent).toBe("Earlier stop15:21Paddington15:33 estUpcoming stop15:40");
  expect(route.querySelector('[aria-current="location"]')?.textContent).toBe("Paddington15:33 est");
  expect(route.querySelector(".train-stop-observed")?.textContent).toBe("Earlier stop15:21");
  expect(route.querySelector(".train-last-seen")?.textContent).toBe("Last seen: Earlier stop at 15:21");
  expect(screen.getByRole("region", { name: "Most recent departure" }).textContent).toContain("Paddington15:04");
  expect(screen.getByRole("region", { name: "Most recent departure" })
    .querySelector('[aria-current="location"]')?.textContent).toBe("Paddington15:04");
  expect(screen.getByRole("region", { name: "Most recent departure" }).textContent).not.toMatch(/Oxford|arrival|late/);
  expect(screen.getByText("12 °C")).toBeTruthy();
  expect(fetch).toHaveBeenCalledTimes(1);
});

test("expires a near-stale kiosk heartbeat even while the preview connection remains open", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn(async () => reply({ ...initial, heartbeat_age_seconds: 14 })));
  render(<RemotePreview deviceId="aabbccddeeff" deviceName="Lobby" close={vi.fn()} />);
  await settle();
  expect(screen.getByText("<script>Only text</script>")).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(screen.getByRole("alert").textContent).toContain("heartbeat is stale");
  expect(screen.queryByText("<script>Only text</script>")).toBeNull();
});

test("hides previous departure information when disabled on the active scene", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => reply({
    ...initial,
    scene: { id: "train", kind: "train", station_crs: "PAD", show_previous_departure: false },
    train: {
      station: "Paddington", rows: [{ scheduled: "15:30", destination: "Reading" }],
      recent: { actual: "15:04", display_stops: [{ station: "Hidden stop", actual: "15:04" }] },
    },
  })));
  render(<RemotePreview deviceId="aabbccddeeff" deviceName="Lobby" close={vi.fn()} />);
  await settle();
  expect(screen.getByText("Reading")).toBeTruthy();
  expect(screen.queryByRole("region", { name: "Most recent departure" })).toBeNull();
  expect(screen.queryByText("Hidden stop")).toBeNull();
});

test("distinguishes previous scheduled service from a confirmed departure", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => reply({
    ...initial,
    scene: { id: "train", kind: "train", station_crs: "HWW" },
    train: {
      station: "How Wood", rows: [],
      recent: {
        scheduled: "08:57", actual: "", status: "Departure not confirmed",
        destination: "St Albans Abbey", delay_minutes: null,
        stops: [{ station: "Park Street", scheduled: "08:59", actual: "", estimated: "On time", timing: "reported" }],
      },
    },
  })));
  render(<RemotePreview deviceId="aabbccddeeff" deviceName="Lobby" close={vi.fn()} />);
  const previous = await screen.findByRole("region", { name: "Previous scheduled service" });
  expect(previous.textContent).toContain("Park Street08:59");
  expect(previous.textContent).not.toMatch(/not confirmed|not reported|Lateness/);
  expect(screen.queryByRole("heading", { name: "Most recent departure" })).toBeNull();
});
