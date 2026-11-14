import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ManagerApp } from "./ManagerApp";

vi.mock("./DevicePlayers", () => ({ DevicePlayers: () => null }));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const musicConfig = { enabled: false, station_key: "", volume: 30,
  status: { state: "disabled", title: "", error: "", paused_by_user: false } };

function stub(devices: object[]) {
  const calls: { path: string; init: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init: RequestInit = {}) => {
      calls.push({ path, init });
      if (path.endsWith("/settings")) return { ok: true, status: 200, json: async () => musicConfig } as Response;
      return { ok: true, status: 200, json: async () => ({ devices }) } as Response;
    }),
  );
  return calls;
}

test("lists devices and sends the token", async () => {
  window.location.hash = "#token=abc";
  const calls = stub([
    { id: "aabbccddeeff", name: "Lobby", host: "10.0.0.5", port: 8443, online: true, version: "0.5.6", url: "https://x.example", internet: true, default_route: "eth0",
      wifi_ssid: "Home", wifi_connected: false },
  ]);
  render(<ManagerApp />);
  expect(await screen.findByText("Lobby")).toBeTruthy();
  fireEvent.click(screen.getByText("More actions"));
  expect(screen.getByText("0.5.6")).toBeTruthy();
  expect(screen.getByText("Home (not connected)")).toBeTruthy();
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

test("does not offer known alternate addresses or DNS aliases as new while scan flags are stale", async () => {
  vi.stubGlobal("fetch", vi.fn(async (path: string) => ({
    ok: true, status: 200, json: async () => path === "/api/discover"
      ? { found: [
        { name: "PI-TWO.LOCAL.", host: "10.0.0.7", port: 8443, enrolled: false },
        { name: "Other interface", host: "10.0.0.8", port: 8443, enrolled: false },
        { name: "New display", host: "10.0.0.9", port: 8443, enrolled: false },
      ] }
      : { devices: [{
        id: "aabbccddeeff", name: "Living room", host: "10.0.0.5", port: 8443, online: false,
        hostname: "pi-two", addresses: ["10.0.0.5", "10.0.0.8"],
      }] },
  })));
  render(<ManagerApp />);
  expect(await screen.findByText("Living room")).toBeTruthy();
  expect(await screen.findByText(/New display/)).toBeTruthy();
  expect(screen.queryByText(/PI-TWO/)).toBeNull();
  expect(screen.queryByText(/Other interface/)).toBeNull();
  expect(screen.getAllByRole("button", { name: "Add" })).toHaveLength(1);
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
  fireEvent.click(await screen.findByText("More actions"));
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
  fireEvent.click(await screen.findByText("More actions"));
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
  fireEvent.click(await screen.findByText("More actions"));
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
  fireEvent.click(await screen.findByText("More actions"));
  await screen.findByText("Open admin page");
  expect(screen.queryByText(/Development SSH keys/)).toBeNull();
});

function syncStub(devices: object[]) {
  const calls: { path: string; init: RequestInit }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (path: string, init: RequestInit = {}) => {
    calls.push({ path, init });
    const body = path === "/api/devices" ? { devices } : path === "/api/discover" ? { found: [] }
      : path.endsWith("/settings") ? musicConfig
      : { results: [{ id: "leader", name: "Kitchen", ok: true }, { id: "follower", name: "Lounge", ok: true }] };
    return { ok: true, status: 200, json: async () => body } as Response;
  }));
  return calls;
}

const syncLeader = { id: "leader", name: "Kitchen", host: "10.0.0.1", port: 8443, online: true,
  music_sync: { supported: true, role: "off", group_id: "", connected: true } };
const syncFollower = { ...syncLeader, id: "follower", name: "Lounge", host: "10.0.0.2" };

test("creates a music sync group from supported online devices with the correct body", async () => {
  const calls = syncStub([syncLeader, syncFollower,
    { ...syncLeader, id: "old", name: "Old firmware", music_sync: { supported: false } },
    { ...syncLeader, id: "offline", name: "Disconnected", online: false, music_sync: null }]);
  render(<ManagerApp />);
  fireEvent.click(await screen.findByRole("button", { name: "Music sync groups" }));
  fireEvent.change(screen.getByLabelText("Leader"), { target: { value: "leader" } });
  expect((screen.getByRole("checkbox", { name: /Old firmware/ }) as HTMLInputElement).disabled).toBe(true);
  expect((screen.getByRole("checkbox", { name: /Disconnected/ }) as HTMLInputElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: "Create group" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("checkbox", { name: "Lounge" }));
  fireEvent.click(screen.getByRole("button", { name: "Create group" }));
  await waitFor(() => expect(calls.find(call => call.path === "/api/sync-groups")).toBeTruthy());
  const call = calls.find(call => call.path === "/api/sync-groups")!;
  expect(call.init.method).toBe("POST");
  expect(JSON.parse(call.init.body as string)).toEqual({ leader: "leader", followers: ["follower"] });
  expect(await screen.findByText("Kitchen: updated")).toBeTruthy();
});

test("lists sync groups and follower errors and posts leave and dissolve operations", async () => {
  const group_id = "group_abcdefghijkl";
  const calls = syncStub([
    { ...syncLeader, music_sync: { supported: true, role: "leader", group_id, members: [{ name: "Lounge" }] } },
    { ...syncFollower, music_sync: { supported: true, role: "follower", group_id, leader: { name: "Kitchen" }, connected: false, error: "Different Plex server" } },
  ]);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<ManagerApp />);
  expect(await screen.findByText("Music sync: leading (1 followers)")).toBeTruthy();
  expect(screen.getByText("Music sync: following Kitchen — Different Plex server")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Music sync groups" }));
  expect(screen.getByText("Leader: Kitchen")).toBeTruthy();
  expect(screen.getByText("Lounge — Different Plex server")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Remove follower Lounge" }));
  await waitFor(() => expect(calls.some(call => call.path === "/api/devices/follower/sync/leave")).toBe(true));
  await waitFor(() => expect((screen.getByRole("button", { name: "Dissolve" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "Dissolve" }));
  await waitFor(() => expect(calls.some(call => call.path === "/api/sync-groups/dissolve")).toBe(true));
  expect(JSON.parse(calls.find(call => call.path === "/api/sync-groups/dissolve")!.init.body as string)).toEqual({ group_id });
});

test("warns before reassigning grouped devices and offers to dissolve the old leader group first", async () => {
  const calls = syncStub([{ ...syncLeader,
    music_sync: { supported: true, role: "leader", group_id: "group_abcdefghijkl", members: [{ name: "Lounge" }] } }, syncFollower]);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<ManagerApp />);
  fireEvent.click(await screen.findByRole("button", { name: "Music sync groups" }));
  fireEvent.change(screen.getByLabelText("Leader"), { target: { value: "leader" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "Lounge" }));
  expect(screen.getByRole("button", { name: "Dissolve Kitchen's group first" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Create group" }));
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining("Existing followers"));
  expect(calls.some(call => call.path === "/api/sync-groups")).toBe(false);
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Create group" }));
  await waitFor(() => expect(calls.some(call => call.path === "/api/sync-groups")).toBe(true));
});

test("reserves the compact sync line across refresh states and lists offline last-known followers", async () => {
  const devices = [
    { ...syncLeader, music_sync: { supported: false } },
    { ...syncFollower, online: false, music_sync: null, music_sync_last_known: {
      supported: true, role: "follower", group_id: "group_abcdefghijkl", leader: { name: "Study" }, connected: false,
    } },
  ];
  syncStub(devices);
  render(<ManagerApp />);
  const unsupported = await screen.findByText("Music sync: unsupported (update firmware)");
  expect(unsupported.classList.contains("manager-sync-line")).toBe(true);
  expect(screen.getByText("Music sync: unavailable (offline)").classList.contains("manager-sync-line")).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Music sync groups" }));
  expect(screen.getByText("Leader: Study")).toBeTruthy();
  expect(screen.getByText("Lounge — offline (last known)")).toBeTruthy();
});

test("shows a floating player for a music sync group that controls its leader", async () => {
  window.location.hash = "#token=abc";
  const settings: { id: string; body: { method: string; path: string; body?: unknown } }[] = [];
  const devices = [
    { id: "aabbccddeeff", name: "Lobby", host: "10.0.0.5", port: 8443, online: true,
      music_sync: { supported: true, role: "leader", members: [{ name: "Kitchen", listening: true, last_seen: 1 }] } },
    { id: "112233445566", name: "Kitchen", host: "10.0.0.6", port: 8443, online: true,
      music_sync: { supported: true, role: "follower" } },
  ];
  vi.stubGlobal("fetch", vi.fn(async (path: string, init: RequestInit = {}) => {
    const match = /\/api\/devices\/([0-9a-f]+)\/settings$/.exec(path);
    if (match) {
      const body = JSON.parse(String(init.body));
      settings.push({ id: match[1], body });
      return { ok: true, status: 200, json: async () => ({ enabled: true, station_key: "/playlists/1/items", volume: 30,
        status: { state: "playing", title: "Group song", artist: "Band", error: "", paused_by_user: false } }) } as Response;
    }
    return { ok: true, status: 200, json: async () => ({ devices }) } as Response;
  }));
  render(<ManagerApp />);
  const player = await screen.findByRole("region", { name: "Audio player" });
  expect(await screen.findByText("Group song")).toBeTruthy();
  expect(player.textContent).toContain("Lobby group (+1)");
  fireEvent.click(screen.getByRole("button", { name: "Pause" }));
  await waitFor(() => expect(settings.some(({ body }) => body.path === "/api/plex/music/control")).toBe(true));
  expect(settings.every(({ id }) => id === "aabbccddeeff")).toBe(true);
});

test("hides the floating player without a music sync group", async () => {
  window.location.hash = "#token=abc";
  stub([{ id: "aabbccddeeff", name: "Lobby", host: "10.0.0.5", port: 8443, online: true,
    music_sync: { supported: true, role: "off" } }]);
  render(<ManagerApp />);
  expect(await screen.findByText("Lobby")).toBeTruthy();
  expect(screen.queryByRole("region", { name: "Audio player" })).toBeNull();
});
