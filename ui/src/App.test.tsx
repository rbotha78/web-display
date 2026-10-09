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
