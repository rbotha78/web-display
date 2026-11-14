import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { sleepDays } from "../api";
import { settingsApi } from "./api";
import { ManagerApp } from "./ManagerApp";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function routes(pendingConfig = false) {
  const calls: { path: string; init: RequestInit; request?: { method: string; path: string; body?: object } }[] = [];
  let config = { url: "https://original.example", auto_ap: false, safe_area_percent: 5, audio_output: "hdmi" };
  const values: Record<string, object> = {
    "/api/status": { hostname: "lobby", addresses: [], paired: true },
    "/api/network": { internet: { online: true }, website: { reachable: true }, default_route: "eth0", wifi: {} },
    "/api/playlist": { enabled: false, scenes: [] },
    "/api/plex/config": { configured: false, server_url: "", default_bitrate_kbps: 4000 },
    "/api/plex/music": { enabled: false, station_key: "", volume: 30, status: { state: "disabled", title: "", error: "" } },
    "/api/plex/stations": { items: [] },
    "/api/raildata/config": { board_configured: false, details_configured: false, refresh_seconds: 30 },
    "/api/weather/config": { city: "", country: "", configured: false },
    "/api/audio/outputs": { items: [{ id: "hdmi", label: "HDMI" }] },
    "/api/debug-keys": { enabled: false, keys: [] },
    "/api/sleep": { days: Object.fromEntries(sleepDays.map(day =>
      [day, { enabled: false, start: "22:00", end: "07:00" }])), sleeping: false,
      scheduled_sleep: false, wake_until: null, device_time: "", timezone: "UTC" },
    "/api/update/status": { base_version: "0.5.18", running_version: "0.5.18", installed_version: "0.5.18",
      previous_version: null, pending: null, failed: [], repository: "rbotha78/web-display" },
  };
  vi.stubGlobal("fetch", vi.fn(async (path: string, init: RequestInit = {}) => {
    const request = init.body ? JSON.parse(init.body as string) : undefined;
    calls.push({ path, init, request });
    let payload: object;
    if (path === "/api/devices") payload = { devices: [
      { id: "aabbccddeeff", name: "Lobby", host: "device.example", port: 8443, online: true },
      { id: "112233445566", name: "Studio", host: "other.example", port: 8443, online: true },
    ] };
    else if (path === "/api/discover") payload = { found: [] };
    else if (path.endsWith("/debug-keys")) payload = { enabled: false, keys: [] };
    else if (path.endsWith("/settings")) {
      if (request.path === "/api/config") {
        if (pendingConfig) return await new Promise<Response>(() => {});
        if (request.method === "PUT") config = { ...config, ...request.body };
        payload = config;
      } else if (request.method === "PUT") payload = request.body;
      else payload = values[request.path] ?? {};
    } else throw new Error("Unexpected request: " + path);
    return { ok: true, status: 200, json: async () => payload } as Response;
  }));
  window.location.hash = "#token=manager-token";
  return calls;
}

test("edits both screens natively with per-device requests and retained drafts", async () => {
  const calls = routes();
  const open = vi.spyOn(window, "open");
  const rendered = render(<ManagerApp />);
  fireEvent.click((await screen.findAllByText("Configure device"))[0]);
  await screen.findByRole("region", { name: "Configuration for Lobby" });
  const url = await screen.findByLabelText("Web page URL") as HTMLInputElement;
  await waitFor(() => expect(url.value).toBe("https://original.example"));
  fireEvent.change(url, { target: { value: "https://draft.example" } });
  fireEvent.click(screen.getByRole("button", { name: "Carousel" }));
  await waitFor(() => expect(screen.getByLabelText("New scene type")).toHaveProperty("disabled", false));
  fireEvent.click(screen.getByText("Add scene"));
  fireEvent.change(screen.getByLabelText("Scene web page URL"), { target: { value: "https://scene.example" } });
  fireEvent.click(screen.getByRole("button", { name: "Global settings" }));
  rendered.rerender(<ManagerApp />);
  expect(url.value).toBe("https://draft.example");
  fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
  await screen.findByText("Settings saved. The display will update shortly.");
  fireEvent.click(screen.getByRole("button", { name: "Carousel" }));
  expect(screen.getByLabelText("Scene web page URL")).toHaveProperty("value", "https://scene.example");
  fireEvent.click(screen.getByRole("button", { name: "Save and apply" }));
  await waitFor(() => expect(calls.some(c => c.request?.method === "PUT" && c.request.path === "/api/playlist")).toBe(true));
  expect(calls.filter(c => c.path.endsWith("/settings") && c.request?.method !== "GET")
    .every(c => c.path === "/api/devices/aabbccddeeff/settings")).toBe(true);
  const save = calls.find(c => c.request?.method === "PUT" && c.request.path === "/api/config")!;
  expect(save.request?.body).toMatchObject({ url: "https://draft.example" });
  expect(save.init.headers).toMatchObject({ "X-Manager-Token": "manager-token" });
  expect(open).not.toHaveBeenCalled();
  expect(rendered.container.querySelector("iframe")).toBeNull();
  expect(calls.some(c => c.path === "/api/config" || c.request?.path === "/api/keys")).toBe(false);
});

test("closing configuration cancels in-flight device requests", async () => {
  const calls = routes(true);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<ManagerApp />);
  fireEvent.click((await screen.findAllByText("Configure device"))[0]);
  await screen.findByRole("region", { name: "Configuration for Lobby" });
  await waitFor(() => expect(calls.some(c => c.request?.path === "/api/config")).toBe(true));
  const pending = calls.find(c => c.request?.path === "/api/config")!;
  fireEvent.click(screen.getByText("Back to devices"));
  expect(pending.init.signal?.aborted).toBe(true);
  expect(screen.queryByRole("region", { name: "Configuration for Lobby" })).toBeNull();
  await screen.findAllByText("Configure device");
});

test("bodyless device mutations are proxied with an empty JSON body", async () => {
  const calls = routes();
  const api = settingsApi("aabbccddeeff", new AbortController().signal);
  await api.scanBluetooth();
  await api.status();
  const scan = calls.find(c => c.request?.path === "/api/bluetooth/scan")!;
  expect(scan.request).toEqual({ method: "POST", path: "/api/bluetooth/scan", body: {} });
  expect(calls.find(c => c.request?.path === "/api/status")!.request).not.toHaveProperty("body");
});
