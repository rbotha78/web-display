import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ManagerApp } from "./ManagerApp";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function stub(devices: object[]) {
  const calls: { path: string; init: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init: RequestInit = {}) => {
      calls.push({ path, init });
      return { ok: true, status: 200, json: async () => ({ devices }) } as Response;
    }),
  );
  return calls;
}

test("lists devices and sends the token", async () => {
  window.location.hash = "#token=abc";
  const calls = stub([
    { id: "aabbccddeeff", name: "Lobby", host: "10.0.0.5", port: 8443, online: true, version: "0.5.6", url: "https://x.example", internet: true, default_route: "eth0" },
  ]);
  render(<ManagerApp />);
  expect(await screen.findByText("Lobby")).toBeTruthy();
  expect(screen.getByText("0.5.6")).toBeTruthy();
  const headers = calls[0].init.headers as Record<string, string>;
  expect(headers["X-Manager-Token"]).toBe("abc");
});

test("offers to trust a changed certificate", async () => {
  stub([
    { id: "aabbccddeeff", name: "Lobby", host: "10.0.0.5", port: 8443, online: false, certificate_changed: true, error: "changed" },
  ]);
  render(<ManagerApp />);
  fireEvent.click(await screen.findByText("Trust again"));
  await waitFor(() => expect(screen.getByText("Add a device")).toBeTruthy());
});

test("shows discovered devices on the first screen", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => ({
      ok: true,
      status: 200,
      json: async () =>
        path === "/api/discover"
          ? { found: [{ name: "web-display-pi3", host: "10.0.0.7", port: 8443, enrolled: false }] }
          : { devices: [] },
    })),
  );
  render(<ManagerApp />);
  expect(await screen.findByText(/web-display-pi3/)).toBeTruthy();
  fireEvent.click(screen.getByText("Add"));
  expect(screen.getByText("Add a device")).toBeTruthy();
});

test("shows manager version and an outdated firmware warning", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => ({
      ok: true,
      status: 200,
      json: async () =>
        path === "/api/discover"
          ? { found: [] }
          : {
              manager_version: "0.5.7",
              devices: [{ id: "aabbccddeeff", name: "Old", host: "h", port: 8443, online: true, version: "0.5.6", firmware_outdated: true, min_firmware: "0.5.7" }],
            },
    })),
  );
  render(<ManagerApp />);
  expect(await screen.findByText(/Web Display Manager 0.5.7/)).toBeTruthy();
  expect(screen.getByText(/older than the 0.5.7/)).toBeTruthy();
});

test("links to each device's admin page and manages its SSH keys", async () => {
  window.location.hash = "#token=abc";
  let keys = [{ id: "aaaaaaaaaaaa", type: "ssh-ed25519", comment: "laptop", fingerprint: "SHA256:abc" }];
  const calls: { path: string; init: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init: RequestInit = {}) => {
      calls.push({ path, init });
      const base = "/api/devices/aabbccddeeff/debug-keys";
      let body: object = {};
      if (path === "/api/devices") {
        body = { devices: [{ id: "aabbccddeeff", name: "Lobby", host: "10.0.0.5", port: 8443, online: true, version: "0.5.9", url: "https://x.example", internet: true, default_route: "eth0" }] };
      } else if (path === base && init.method === "POST") {
        keys = [...keys, { ...keys[0], id: "bbbbbbbbbbbb", comment: "desk" }];
        body = { enabled: true, keys };
      } else if (path === base + "/aaaaaaaaaaaa") {
        keys = keys.slice(1);
        body = { enabled: true, keys };
      } else if (path === base) {
        body = { enabled: true, keys };
      }
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<ManagerApp />);
  const link = (await screen.findByText("Open without automatic login")) as HTMLAnchorElement;
  expect(link.getAttribute("href")).toBe("https://10.0.0.5:8443/");
  expect(link.rel).toContain("noopener");

  expect(await screen.findByText("laptop")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Public key"), { target: { value: "ssh-ed25519 AAAA desk" } });
  fireEvent.click(screen.getByText("Add key"));
  expect(await screen.findByText("desk")).toBeTruthy();
  const add = calls.find((c) => c.init.method === "POST")!;
  expect(JSON.parse(add.init.body as string)).toEqual({ key: "ssh-ed25519 AAAA desk" });

  fireEvent.click(screen.getAllByText("Remove").find((b) => b.closest("p"))!);
  await waitFor(() => expect(screen.queryByText("laptop")).toBeNull());
});

test("opens a one-time login link in an isolated tab", async () => {
  const replace = vi.fn();
  const tab = { opener: window, location: { replace }, close: vi.fn() };
  vi.stubGlobal("open", vi.fn(() => tab));
  let resolveLogin: (reply: Response) => void = () => undefined;
  const fetch = vi.fn(async (path: string) => {
    if (path.endsWith("/admin-login")) {
      return new Promise<Response>((resolve) => { resolveLogin = resolve; });
    }
    return {
      ok: true, status: 200,
      json: async () => ({ devices: [{ id: "aabbccddeeff", name: "Lobby", host: "device.example", port: 8443, online: true }] }),
    } as Response;
  });
  vi.stubGlobal("fetch", fetch);
  render(<ManagerApp />);
  fireEvent.click(await screen.findByText("Open admin page"));
  expect(window.open).toHaveBeenCalledWith("", "_blank");
  expect(tab.opener).toBeNull();
  expect(replace).not.toHaveBeenCalled();
  resolveLogin({ ok: true, json: async () => ({ url: "https://device.example:8443/#admin-login=secret" }) } as Response);
  await waitFor(() => expect(replace).toHaveBeenCalledWith("https://device.example:8443/#admin-login=secret"));
  expect(fetch).toHaveBeenCalledWith("/api/devices/aabbccddeeff/admin-login", expect.objectContaining({ method: "POST" }));
});

test("closes the new tab and reports a failed automatic login", async () => {
  const tab = { opener: window, close: vi.fn() };
  vi.stubGlobal("open", vi.fn(() => tab));
  vi.stubGlobal("fetch", vi.fn(async (path: string) => ({
    ok: !path.endsWith("/admin-login"), status: path.endsWith("/admin-login") ? 502 : 200,
    json: async () => path.endsWith("/admin-login")
      ? { error: "Update device software first" }
      : { devices: [{ id: "aabbccddeeff", name: "Lobby", host: "device.example", port: 8443, online: true }] },
  })));
  render(<ManagerApp />);
  fireEvent.click(await screen.findByText("Open admin page"));
  expect(await screen.findByText("Update device software first")).toBeTruthy();
  expect(tab.close).toHaveBeenCalled();
});

test("hides SSH keys when the device has none to manage", async () => {
  window.location.hash = "#token=abc";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      const failed = path.endsWith("/debug-keys");
      return {
        ok: !failed,
        status: failed ? 404 : 200,
        json: async () =>
          failed
            ? { error: "not found" }
            : { devices: [{ id: "aabbccddeeff", name: "Lobby", host: "10.0.0.5", port: 8443, online: true, version: "0.5.9" }] },
      } as Response;
    }),
  );
  render(<ManagerApp />);
  await screen.findByText("Open admin page");
  expect(screen.queryByText(/Development SSH keys/)).toBeNull();
});
