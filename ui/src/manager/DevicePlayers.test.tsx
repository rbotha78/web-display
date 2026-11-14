import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { DeviceCard } from "./DeviceCard";
import { DevicePlayers } from "./DevicePlayers";
import { setToken } from "./api";

afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

test("keeps home compact and routes both transports to the selected display", async () => {
  setToken("manager-token");
  let paused = false;
  const music = { enabled: true, station_key: "station", volume: 30,
    status: { state: "playing", title: "Current song", error: "", rating_key: "42", liked: false } };
  const calls: { method: string; path: string; body?: { action?: string; rating_key?: string } }[] = [];
  const fetch = vi.fn(async (path: string, init: RequestInit) => {
    expect(path).toBe("/api/devices/aabbccddeeff/settings");
    expect(init.headers).toMatchObject({ "X-Manager-Token": "manager-token" });
    const request = JSON.parse(init.body as string);
    calls.push(request);
    let payload: object;
    if (request.path === "/api/plex/music") payload = music;
    else if (request.path === "/api/playlist/status") payload = {
      failures: {}, updated: 1, enabled: true, paused, transport_supported: true,
      transport_available: true, scene_title: "Clock",
    };
    else if (request.path === "/api/playlist/control") {
      if (request.body.action !== "next") paused = request.body.action === "pause";
      payload = { ok: true };
    } else if (request.path === "/api/plex/music/like") {
      music.status.liked = true;
      payload = music;
    } else throw new Error("Unexpected player request");
    return { ok: true, status: 200, json: async () => payload } as Response;
  });
  vi.stubGlobal("fetch", fetch);
  const notify = vi.fn();
  render(<DeviceCard device={{ id: "aabbccddeeff", name: "Lobby", host: "device.example",
    port: 8443, online: true }} notify={notify} refresh={vi.fn()} readd={vi.fn()} configure={vi.fn()} />);
  expect(await screen.findByText("Current song")).toBeTruthy();
  expect(await screen.findByText("Carousel playing")).toBeTruthy();
  expect(screen.queryByLabelText("Display address")).toBeNull();
  expect(screen.queryByText("Check for updates")).toBeNull();
  expect(screen.queryByText("Open admin page")).toBeNull();
  expect(screen.queryByText("Connection")).toBeNull();
  expect(screen.getByText("Configure device")).toBeTruthy();
  expect(calls.every(call => !call.path.includes("debug-keys"))).toBe(true);
  fireEvent.click(screen.getByText("Pause carousel"));
  expect(await screen.findByText("Carousel paused")).toBeTruthy();
  fireEvent.click(screen.getByText("Next scene"));
  await waitFor(() => expect(calls.some(call => call.body?.action === "next")).toBe(true));
  expect(screen.getByText("Carousel paused")).toBeTruthy();
  fireEvent.click(screen.getByLabelText("Like song in Plex"));
  await waitFor(() => expect(screen.getByLabelText("Like song in Plex").getAttribute("aria-pressed")).toBe("true"));
  expect(calls.find(call => call.path.endsWith("/like"))?.body).toEqual({ rating_key: "42" });
  expect(notify).not.toHaveBeenCalled();
});

test("older firmware cannot enable carousel controls and failed music polling is explicit", async () => {
  const notify = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async (_path: string, init: RequestInit) => {
    const music = JSON.parse(init.body as string).path === "/api/plex/music";
    return { ok: !music, status: music ? 404 : 200,
      json: async () => music ? { error: "Music needs newer firmware" } : { failures: {}, updated: null } } as Response;
  }));
  render(<DevicePlayers deviceId="aabbccddeeff" notify={notify} />);
  expect(await screen.findByText("Update device firmware for carousel controls.")).toBeTruthy();
  expect(await screen.findByText("Player unavailable")).toBeTruthy();
  expect(screen.getByText("Next scene")).toHaveProperty("disabled", true);
  expect(notify).toHaveBeenCalledWith("Music needs newer firmware", "error");
});

test("does not overlap pending polls and aborts both players on close", async () => {
  vi.useFakeTimers();
  const signals: AbortSignal[] = [];
  const fetch = vi.fn((_path: string, init: RequestInit) => {
    signals.push(init.signal as AbortSignal);
    return new Promise<Response>(() => {});
  });
  vi.stubGlobal("fetch", fetch);
  const view = render(<DevicePlayers deviceId="aabbccddeeff" notify={vi.fn()} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(20000); });
  expect(fetch).toHaveBeenCalledTimes(2);
  view.unmount();
  expect(signals.every(signal => signal.aborted)).toBe(true);
  await act(async () => { await vi.advanceTimersByTimeAsync(20000); });
  expect(fetch).toHaveBeenCalledTimes(2);
});
