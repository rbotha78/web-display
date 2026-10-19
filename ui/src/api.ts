export interface Session {
  csrf: string;
}

export interface Config {
  url: string;
  auto_ap: boolean;
}

export interface Scene {
  id: string;
  kind: "web" | "dashboard" | "video";
  title: string;
  enabled: boolean;
  duration: number | null;
  url: string;
  source?: "url" | "plex";
  plex_rating_key?: string;
  layout: "clock" | "notice" | "clock-notice";
  text: string;
}

export interface PlexConfig {
  server_url: string;
  configured: boolean;
  server_name?: string;
}

export interface PlexBrowseItem {
  id?: string;
  key?: string;
  title: string;
  type: string;
  container: boolean;
  duration?: number | null;
}

export interface Playlist {
  enabled: boolean;
  scenes: Scene[];
}

export interface PlaybackStatus {
  failures: Record<string, { message: string; time: number }>;
  updated: number | null;
}

export interface Network {
  ssid: string;
  signal: number;
  secured: boolean;
}

export interface Status {
  hostname: string;
  addresses: string[];
  paired: boolean;
}

export interface NetworkStatus {
  internet: { online: boolean; reachable_endpoints: number; total_endpoints: number };
  website: { host?: string; reachable: boolean };
  default_route: string;
  wifi: {
    error?: string;
    country?: string;
    wifi_blocked?: boolean;
    saved_network?: boolean;
    devices?: { device: string; type: string; state: string; connection: string }[];
  };
}

export interface UpdateStatus {
  base_version: string;
  running_version: string;
  installed_version: string;
  previous_version: string | null;
  pending: { version: string; previous: string | null; attempts: number } | null;
  failed: string[];
  repository: string;
}

export interface UpdateCheck {
  available: boolean;
  latest: { version: string; size: number } | null;
}

export interface UpdateResult {
  version: string;
  restart_required: boolean;
}

export interface ApiKey {
  id: string;
  name: string;
  created: number | null;
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

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

let csrf = "";

export function setCsrf(value: string): void {
  csrf = value;
}

export async function request<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (csrf) headers["X-CSRF-Token"] = csrf;
  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? (method === "GET" ? undefined : "{}") : JSON.stringify(body),
    credentials: "same-origin",
  });
  let payload: { error?: string } = {};
  try {
    payload = await response.json();
  } catch {
    // Non-JSON error bodies fall through to the generic message.
  }
  if (!response.ok) {
    throw new ApiError(payload.error || `Request failed (${response.status})`, response.status);
  }
  return payload as T;
}

async function readError(response: Response): Promise<ApiError> {
  let payload: { error?: string } = {};
  try {
    payload = await response.json();
  } catch {
    // Non-JSON error bodies fall through to the generic message.
  }
  return new ApiError(payload.error || `Request failed (${response.status})`, response.status);
}

async function uploadUpdate(file: File): Promise<UpdateResult> {
  const response = await fetch("/api/update/upload", {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream", "X-CSRF-Token": csrf },
    body: file,
    credentials: "same-origin",
  });
  if (!response.ok) throw await readError(response);
  return (await response.json()) as UpdateResult;
}

export const api = {
  health: () => request<{ paired: boolean }>("/health"),
  session: () => request<Session>("/api/session"),
  login: (password: string) => request<Session>("/api/login", "POST", { password }),
  pair: (code: string, password: string) =>
    request<Session>("/api/pair", "POST", { code, password }),
  logout: () => request<object>("/api/logout", "POST"),
  config: () => request<Config>("/api/config"),
  saveConfig: (config: Partial<Config>) => request<Config>("/api/config", "PUT", config),
  playlist: () => request<Playlist>("/api/playlist"),
  playlistStatus: () => request<PlaybackStatus>("/api/playlist/status"),
  savePlaylist: (playlist: Playlist) => request<Playlist>("/api/playlist", "PUT", playlist),
  plexConfig: () => request<PlexConfig>("/api/plex/config"),
  savePlexConfig: (config: { server_url: string; token?: string; clear_token?: boolean }) =>
    request<PlexConfig>("/api/plex/config", "PUT", config),
  plexBrowse: (key?: string) => request<{ items: PlexBrowseItem[] }>(
    "/api/plex/browse" + (key === undefined ? "" : "?key=" + encodeURIComponent(key)),
  ),
  status: () => request<Status>("/api/status"),
  network: () => request<NetworkStatus>("/api/network"),
  setHostname: (hostname: string) =>
    request<{ hostname: string; changed: boolean }>("/api/hostname", "POST", { hostname }),
  reboot: () => request<object>("/api/reboot", "POST"),
  scan: () => request<{ networks: Network[] }>("/api/wifi/scan", "POST"),
  setCountry: (country: string) =>
    request<object>("/api/wifi/country", "POST", { country: country.toUpperCase() }),
  connect: (ssid: string, password: string) =>
    request<object>("/api/wifi/connect", "POST", { ssid, password }),
  forget: () => request<object>("/api/wifi/forget", "POST", {}),
  updateStatus: () => request<UpdateStatus>("/api/update/status"),
  checkUpdate: () => request<UpdateCheck>("/api/update/check", "POST"),
  apiKeys: () => request<{ keys: ApiKey[] }>("/api/keys"),
  revokeApiKey: (id: string) => request<object>("/api/keys/" + id, "DELETE"),
  debugKeys: () => request<SshKeys>("/api/debug-keys"),
  addDebugKey: (key: string, name: string) =>
    request<SshKeys>("/api/debug-keys", "POST", name ? { key, name } : { key }),
  removeDebugKey: (id: string) => request<SshKeys>("/api/debug-keys/" + id, "DELETE"),
  installUpdate: () => request<UpdateResult>("/api/update/install", "POST"),
  uploadUpdate,
};
