import { ApiError, createApi, type ApiRequest, type RemoteView } from "../api";

export interface MusicSyncState {
  supported: boolean;
  role?: "off" | "leader" | "follower";
  group_id?: string;
  leader?: { name: string; hostname: string; port: number; addresses: string[] } | null;
  members?: { name: string; listening: boolean; last_seen: number }[];
  connected?: boolean;
  error?: string;
}

export interface SyncResult {
  id: string;
  name: string;
  ok: boolean;
  error?: string;
}

export interface SyncReply {
  group_id?: string;
  results: SyncResult[];
}

export interface Device {
  id: string;
  name: string;
  host: string;
  port: number;
  online: boolean;
  version?: string;
  hostname?: string;
  addresses?: string[];
  url?: string;
  internet?: boolean | null;
  default_route?: string;
  wifi_connected?: boolean | null;
  installed_version?: string;
  restart_needed?: boolean;
  error?: string;
  certificate_changed?: boolean;
  key_rejected?: boolean;
  firmware_outdated?: boolean;
  min_firmware?: string;
  music_sync?: MusicSyncState | null;
  music_sync_last_known?: MusicSyncState;
}

export interface Found {
  name: string;
  host: string;
  port: number;
  enrolled: boolean;
}

export interface UpdateOutcome {
  id: string;
  name: string;
  outcome: string;
  version?: string;
  error?: string;
}

export interface SshKey {
  id: string;
  type: string;
  comment: string;
  fingerprint: string;
}

export interface SshKeys {
  enabled: boolean;
  keys: SshKey[];
}

export class ManagerError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}

let token = "";

export function setToken(value: string) {
  token = value;
}

async function request<T>(path: string, method = "GET", body?: unknown, signal?: AbortSignal): Promise<T> {
  const headers: Record<string, string> = { "X-Manager-Token": token };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  let payload: { error?: string } = {};
  try {
    payload = await response.json();
  } catch {
    // Keep the generic message below.
  }
  if (!response.ok) throw new ManagerError(payload.error || "Request failed (" + response.status + ")", response.status);
  return payload as T;
}

export function settingsApi(id: string, lifetime: AbortSignal) {
  const send: ApiRequest = async <T,>(path: string, method = "GET", body?: unknown, signal?: AbortSignal) => {
    const controller = new AbortController();
    const abort = () => controller.abort();
    for (const source of [lifetime, signal]) {
      if (source?.aborted) controller.abort();
      source?.addEventListener("abort", abort, { once: true });
    }
    try {
      return await request<T>("/api/devices/" + id + "/settings", "POST", { method, path, body }, controller.signal);
    } catch (error) {
      if (error instanceof ManagerError) throw new ApiError(error.message, error.status);
      throw error;
    } finally {
      lifetime.removeEventListener("abort", abort);
      signal?.removeEventListener("abort", abort);
    }
  };
  return createApi(send, null);
}

export const api = {
  createSyncGroup: (leader: string, followers: string[]) =>
    request<SyncReply>("/api/sync-groups", "POST", { leader, followers }),
  dissolveSyncGroup: (group_id: string) =>
    request<SyncReply>("/api/sync-groups/dissolve", "POST", { group_id }),
  leaveSyncGroup: (id: string) =>
    request<SyncReply>("/api/devices/" + id + "/sync/leave", "POST", {}),
  devices: () => request<{ devices: Device[]; manager_version?: string }>("/api/devices"),
  remoteView: (id: string, signal?: AbortSignal) =>
    request<RemoteView>("/api/devices/" + id + "/remote-view", "GET", undefined, signal),
  discover: () => request<{ found: Found[] }>("/api/discover"),
  fingerprint: (host: string, port: number) =>
    request<{ host: string; port: number; fingerprint: string }>("/api/fingerprint", "POST", {
      host,
      port,
    }),
  enroll: (body: {
    host: string;
    port: number;
    fingerprint: string;
    password: string;
    name: string;
  }) => request<{ id: string }>("/api/enroll", "POST", body),
  forget: (id: string) => request<object>("/api/devices/" + id, "DELETE"),
  setUrl: (id: string, url: string) =>
    request<object>("/api/devices/" + id + "/url", "POST", { url }),
  reboot: (id: string) => request<object>("/api/devices/" + id + "/reboot", "POST", {}),
  adminLogin: (id: string) =>
    request<{ url: string }>("/api/devices/" + id + "/admin-login", "POST", {}),
  checkUpdate: (id: string) =>
    request<{ available: boolean; latest: { version: string } | null }>(
      "/api/devices/" + id + "/update-check",
      "POST",
      {},
    ),
  installUpdate: (id: string) =>
    request<{ version: string }>("/api/devices/" + id + "/update-install", "POST", {}),
  debugKeys: (id: string) => request<SshKeys>("/api/devices/" + id + "/debug-keys"),
  addDebugKey: (id: string, key: string, name: string) =>
    request<SshKeys>("/api/devices/" + id + "/debug-keys", "POST", name ? { key, name } : { key }),
  removeDebugKey: (id: string, keyId: string) =>
    request<SshKeys>("/api/devices/" + id + "/debug-keys/" + keyId, "DELETE"),
  updateAll: () => request<{ results: UpdateOutcome[] }>("/api/update-all", "POST", {}),
};
