import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";

type Handler = (init: RequestInit) => { status?: number; body: unknown };

function mockFetch(routes: Record<string, Handler>) {
  const calls: { path: string; init: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init: RequestInit = {}) => {
      calls.push({ path, init });
      const handler = routes[`${init.method ?? "GET"} ${path}`];
      const result = handler ? handler(init) : { status: 404, body: { error: "not found" } };
      return {
        ok: (result.status ?? 200) < 400,
        status: result.status ?? 200,
        json: async () => result.body,
      } as Response;
    }),
  );
  return calls;
}

const unauthorised = { status: 401, body: { error: "login required" } };

beforeEach(() => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("shows pairing when the device is unpaired", async () => {
  mockFetch({
    "GET /api/session": () => unauthorised,
    "GET /health": () => ({ body: { status: "ok", paired: false } }),
  });
  render(<App />);
  expect(await screen.findByText("First-use pairing")).toBeTruthy();
});

test("shows login when paired, and sends credentials", async () => {
  const calls = mockFetch({
    "GET /api/session": () => unauthorised,
    "GET /health": () => ({ body: { status: "ok", paired: true } }),
    "POST /api/login": () => ({ status: 401, body: { error: "Invalid password" } }),
  });
  render(<App />);
  const password = await screen.findByLabelText("Administrator password");
  fireEvent.change(password, { target: { value: "wrong password!" } });
  fireEvent.click(screen.getByText("Log in"));
  expect(await screen.findByText("Invalid password")).toBeTruthy();
  const login = calls.find((call) => call.path === "/api/login")!;
  expect(JSON.parse(login.init.body as string)).toEqual({ password: "wrong password!" });
});

test("dashboard loads settings and saves them with the CSRF token", async () => {
  const calls = mockFetch({
    "GET /api/session": () => ({ body: { csrf: "token-1" } }),
    "GET /api/status": () => ({
      body: { hostname: "web-display", addresses: ["192.168.0.250"], paired: true },
    }),
    "GET /api/network": () => ({
      body: {
        internet: { online: true, reachable_endpoints: 3, total_endpoints: 3 },
        website: { host: "example.com", reachable: true },
        default_route: "eth0",
        wifi: { country: "GB", saved_network: false, devices: [] },
      },
    }),
    "GET /api/config": () => ({ body: { url: "https://example.com", auto_ap: false } }),
    "PUT /api/config": () => ({ body: { url: "https://example.com", auto_ap: true } }),
  });
  render(<App />);
  const url = (await screen.findByLabelText("Web page URL")) as HTMLInputElement;
  await waitFor(() => expect(url.value).toBe("https://example.com"));
  expect(await screen.findByText("Online (3/3)")).toBeTruthy();

  const checkbox = screen.getByRole("checkbox") as HTMLInputElement;
  expect(checkbox.checked).toBe(false);
  fireEvent.click(checkbox);
  fireEvent.click(screen.getByText("Save settings"));
  expect(await screen.findByText(/Settings saved/)).toBeTruthy();

  const save = calls.find((call) => call.init.method === "PUT")!;
  expect(JSON.parse(save.init.body as string)).toEqual({
    url: "https://example.com",
    auto_ap: true,
  });
  expect((save.init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("token-1");
});

test("renaming the device posts the validated hostname", async () => {
  const calls = mockFetch({
    "GET /api/session": () => ({ body: { csrf: "token-1" } }),
    "GET /api/status": () => ({
      body: { hostname: "web-display", addresses: [], paired: true },
    }),
    "GET /api/network": () => ({
      body: {
        internet: { online: true, reachable_endpoints: 3, total_endpoints: 3 },
        website: { reachable: true },
        default_route: "eth0",
        wifi: {},
      },
    }),
    "GET /api/config": () => ({ body: { url: "https://example.com", auto_ap: false } }),
    "POST /api/hostname": () => ({ status: 202, body: { hostname: "lobby", changed: true } }),
  });
  render(<App />);
  const input = (await screen.findByLabelText("Device hostname")) as HTMLInputElement;
  await waitFor(() => expect(input.value).toBe("web-display"));
  fireEvent.change(input, { target: { value: "Lobby" } });
  fireEvent.click(screen.getByText("Save hostname"));
  expect(await screen.findByText(/lobby\.local:8443/)).toBeTruthy();
  const post = calls.find((call) => call.path === "/api/hostname")!;
  expect(JSON.parse(post.init.body as string)).toEqual({ hostname: "lobby" });
});

const updateStatus = {
  base_version: "0.5.0",
  running_version: "0.5.0",
  installed_version: "0.5.0",
  previous_version: null,
  pending: null,
  failed: [],
  repository: "rbotha78/web-display",
};

function dashboardRoutes(extra: Record<string, Handler>): Record<string, Handler> {
  return {
    "GET /api/session": () => ({ body: { csrf: "token-1" } }),
    "GET /api/status": () => ({ body: { hostname: "web-display", addresses: [], paired: true } }),
    "GET /api/network": () => ({
      body: {
        internet: { online: true, reachable_endpoints: 3, total_endpoints: 3 },
        website: { reachable: true },
        default_route: "eth0",
        wifi: {},
      },
    }),
    "GET /api/config": () => ({ body: { url: "https://example.com", auto_ap: false } }),
    "GET /api/update/status": () => ({ body: updateStatus }),
    ...extra,
  };
}

test("checking for updates offers and installs a newer release", async () => {
  let installedVersion = "0.5.0";
  const calls = mockFetch(
    dashboardRoutes({
      "GET /api/update/status": () => ({
        body: { ...updateStatus, installed_version: installedVersion },
      }),
      "POST /api/update/check": () => ({
        body: { available: true, latest: { version: "0.5.1", size: 2 * 1024 * 1024 } },
      }),
      "POST /api/update/install": () => {
        installedVersion = "0.5.1";
        return { body: { ok: true, version: "0.5.1", restart_required: true } };
      },
    }),
  );
  render(<App />);
  fireEvent.click(await screen.findByText("Check for updates"));
  expect(await screen.findByText(/Version 0\.5\.1 is available \(2\.0 MB\)/)).toBeTruthy();
  fireEvent.click(screen.getByText("Install update"));
  expect(await screen.findByText(/Update 0\.5\.1 installed/)).toBeTruthy();
  expect(await screen.findByText("Restart now")).toBeTruthy();
  const install = calls.find((call) => call.path === "/api/update/install")!;
  expect((install.init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("token-1");
});

test("shows when no newer update exists", async () => {
  mockFetch(
    dashboardRoutes({
      "POST /api/update/check": () => ({ body: { available: false, latest: null } }),
    }),
  );
  render(<App />);
  fireEvent.click(await screen.findByText("Check for updates"));
  expect(await screen.findByText("No newer update is available.")).toBeTruthy();
  expect(screen.queryByText("Install update")).toBeNull();
});

test("uploading an update file sends the raw bytes with the CSRF token", async () => {
  const calls = mockFetch(
    dashboardRoutes({
      "POST /api/update/upload": () => ({
        body: { ok: true, version: "0.5.2", restart_required: true },
      }),
    }),
  );
  render(<App />);
  const input = (await screen.findByLabelText("Signed update file")) as HTMLInputElement;
  const file = new File(["bundle"], "web-display-update-0.5.2.wdupdate");
  fireEvent.change(input, { target: { files: [file] } });
  fireEvent.click(screen.getByText("Upload and install"));
  expect(await screen.findByText(/Update 0\.5\.2 installed/)).toBeTruthy();
  const upload = calls.find((call) => call.path === "/api/update/upload")!;
  expect(upload.init.body).toBe(file);
  expect((upload.init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("token-1");
});

test("a rejected update shows the device's reason", async () => {
  mockFetch(
    dashboardRoutes({
      "POST /api/update/upload": () => ({
        status: 400,
        body: { error: "Signature verification failed" },
      }),
    }),
  );
  render(<App />);
  const input = (await screen.findByLabelText("Signed update file")) as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File(["x"], "bad.wdupdate")] } });
  fireEvent.click(screen.getByText("Upload and install"));
  expect(await screen.findByText("Signature verification failed")).toBeTruthy();
});

test("an installed but not yet running update asks for a restart", async () => {
  mockFetch(
    dashboardRoutes({
      "GET /api/update/status": () => ({
        body: {
          ...updateStatus,
          installed_version: "0.5.1",
          pending: { version: "0.5.1", previous: null, attempts: 0 },
        },
      }),
    }),
  );
  render(<App />);
  expect(await screen.findByText(/Version 0\.5\.1 is installed and starts after a restart/)).toBeTruthy();
  expect(screen.getByText("Restart now")).toBeTruthy();
});
