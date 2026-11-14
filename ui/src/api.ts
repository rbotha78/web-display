import type { TrainBoard } from "./SceneScreen";
import type { WeatherData } from "./components/WeatherDisplay";

export interface RemoteView {
  available: true;
  mode: "carousel" | "single" | "recovery";
  updated: number;
  heartbeat_age_seconds?: number;
  device_time: string;
  timezone: string;
  scene: (Pick<Scene, "id" | "kind"> & Partial<Scene>) | null;
  train: TrainBoard | null;
  weather: WeatherData | null;
  music?: { state: string; title?: string; artist?: string; album?: string; next_title?: string; next_artist?: string } | null;
  sleep: { sleeping: boolean; scheduled_sleep: boolean; wake_until: number | null };
  message: string;
}

export interface Session {
  csrf: string;
  can_manage_keys?: boolean;
}

export interface Config {
  url: string;
  auto_ap: boolean;
  safe_area_percent: number;
  audio_output: string;
}

export const sleepDays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"] as const;
export interface SleepWindow {
  enabled: boolean;
  start: string;
  end: string;
}
export interface SleepSchedule {
  days: Record<(typeof sleepDays)[number], SleepWindow>;
}
export interface SleepConfig extends SleepSchedule {
  sleeping: boolean;
  scheduled_sleep: boolean;
  wake_until: number | null;
  device_time: string;
  timezone: string;
}

export interface Scene {
  id: string;
  kind: "web" | "dashboard" | "video" | "train" | "weather" | "music";
  title: string;
  enabled: boolean;
  duration: number | null;
  audio_enabled?: boolean;
  plex_bitrate_kbps?: number | null;
  url: string;
  source?: "url" | "plex";
  plex_rating_key?: string;
  layout: "clock" | "notice" | "clock-notice";
  text: string;
  station_crs?: string;
  destination_crs?: string;
  filter_station_crs?: string;
  show_previous_departure?: boolean;
  show_weather?: boolean;
}

export interface WeatherConfig {
  city: string;
  country: string;
  configured: boolean;
}

export interface RailDataConfig {
  board_configured: boolean;
  details_configured: boolean;
  refresh_seconds: number;
}

export interface PlexConfig {
  server_url: string;
  configured: boolean;
  default_bitrate_kbps: number;
  server_name?: string;
}

export const WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"] as const;
export type Weekday = typeof WEEKDAYS[number];

export interface MusicConfig {
  enabled: boolean;
  station_key: string;
  volume: number;
  day_sources?: Record<Weekday, string>;
  active_day?: Weekday;
  active_station_key?: string;
  status: { state: string; title: string; artist?: string; error: string; paused_by_user?: boolean;
    rating_key?: string; liked?: boolean };
}

export interface MusicSyncStatus {
  supported: boolean;
  role: "off" | "leader" | "follower";
  group_id: string;
  leader: { name: string; hostname: string; port: number; addresses: string[] } | null;
  members: { name: string; listening: boolean; last_seen: number }[];
  connected: boolean;
  error: string;
}

export interface BluetoothStatus {
  supported: boolean;
  adapter: boolean | null;
  address: string;
  name: string;
  enabled: boolean;
  delay_ms: number;
  connected: boolean;
  active: boolean;
}

export interface BluetoothDevice {
  address: string;
  name: string;
  paired: boolean;
  connected: boolean;
  audio: boolean;
}

export interface MusicStation {
  key: string;
  title: string;
  library: string;
  kind?: "station" | "playlist" | "mix";
  smart?: boolean;
}

export interface MusicBrowseItem {
  id: string;
  title: string;
  type: "library" | "artist" | "album";
  artist?: string;
}

export interface MusicBrowseResult {
  items: MusicBrowseItem[];
  total: number;
  offset: number;
}

export interface MusicFilterChoice {
  id: string;
  title: string;
}

export interface MusicFilters {
  decade: MusicFilterChoice[];
  genre: MusicFilterChoice[];
  mood: MusicFilterChoice[];
}

export interface SmartPlaylistCriteria {
  library: string;
  decade: string;
  genre: string;
  mood: string;
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
  enabled?: boolean;
  paused?: boolean;
  transport_supported?: boolean;
  transport_available?: boolean;
  scene_title?: string;
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
    saved_ssid?: string;
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

export async function request<T>(path: string, method = "GET", body?: unknown, signal?: AbortSignal): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (csrf) headers["X-CSRF-Token"] = csrf;
  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? (method === "GET" ? undefined : "{}") : JSON.stringify(body),
    credentials: "same-origin",
    signal,
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

export type ApiRequest = typeof request;

export function createApi(request: ApiRequest, upload: typeof uploadUpdate | null = uploadUpdate) {
  return {
  health: () => request<{ paired: boolean }>("/health"),
  session: () => request<Session>("/api/session"),
  managerLogin: (token: string) => request<Session>("/api/admin-login", "POST", { token }),
  login: (password: string) => request<Session>("/api/login", "POST", { password }),
  pair: (code: string, password: string) =>
    request<Session>("/api/pair", "POST", { code, password }),
  logout: () => request<object>("/api/logout", "POST"),
  config: () => request<Config>("/api/config"),
  audioOutputs: () => request<{ items: { id: string; label: string }[] }>("/api/audio/outputs"),
  saveConfig: (config: Partial<Config>) => request<Config>("/api/config", "PUT", config),
  sleep: () => request<SleepConfig>("/api/sleep"),
  saveSleep: (schedule: SleepSchedule) => request<SleepConfig>("/api/sleep", "PUT", schedule),
  wake: (duration_minutes: number) => request<SleepConfig>("/api/sleep/wake", "POST", { duration_minutes }),
  playlist: () => request<Playlist>("/api/playlist"),
  playlistStatus: (signal?: AbortSignal) => request<PlaybackStatus>("/api/playlist/status", "GET", undefined, signal),
  controlCarousel: (action: "pause" | "resume" | "next") =>
    request<{ ok: boolean }>("/api/playlist/control", "POST", { action }),
  remoteView: () => request<RemoteView>("/api/remote-view"),
  savePlaylist: (playlist: Playlist) => request<Playlist>("/api/playlist", "PUT", playlist),
  plexConfig: () => request<PlexConfig>("/api/plex/config"),
  musicConfig: (signal?: AbortSignal) => request<MusicConfig>("/api/plex/music", "GET", undefined, signal),
  controlMusic: (action: "play" | "pause" | "resume" | "stop" | "skip") =>
    request<MusicConfig>("/api/plex/music/control", "POST", { action }),
  likeMusic: (ratingKey: string) =>
    request<MusicConfig>("/api/plex/music/like", "POST", { rating_key: ratingKey }),
  musicSync: () => request<MusicSyncStatus>("/api/music/sync"),
  leaveMusicSync: () => request<MusicSyncStatus>("/api/music/sync", "PUT", { role: "off" }),
  bluetooth: () => request<BluetoothStatus>("/api/bluetooth"),
  saveBluetooth: (settings: { enabled?: boolean; delay_ms?: number }) =>
    request<BluetoothStatus>("/api/bluetooth", "PUT", settings),
  scanBluetooth: () => request<{ items: BluetoothDevice[] }>("/api/bluetooth/scan", "POST"),
  pairBluetooth: (address: string, name: string) =>
    request<BluetoothStatus>("/api/bluetooth/pair", "POST", { address, name }),
  forgetBluetooth: () => request<BluetoothStatus>("/api/bluetooth/forget", "POST"),
  musicStations: () => request<{ items: MusicStation[] }>("/api/plex/stations"),
  musicFilters: (library: string) =>
    request<MusicFilters>(`/api/plex/music/filters?${new URLSearchParams({ library })}`),
  previewSmartPlaylist: (criteria: SmartPlaylistCriteria) =>
    request<{ count: number }>("/api/plex/music/playlists/preview", "POST", criteria),
  createSmartPlaylist: (criteria: SmartPlaylistCriteria & { title: string }) =>
    request<MusicStation>("/api/plex/music/playlists", "POST", criteria),
  musicBrowse: (query: { library?: string; kind?: "artist" | "album"; search?: string; offset?: number; key?: string } = {}) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) params.set(key, String(value));
    return request<MusicBrowseResult>("/api/plex/music/browse" + (params.size ? `?${params}` : ""));
  },
  saveMusicConfig: (config: { enabled: boolean; station_key: string; volume: number;
    day_sources?: Record<Weekday, string> }) =>
    request<MusicConfig>("/api/plex/music", "PUT", config),
  railDataConfig: () => request<RailDataConfig>("/api/raildata/config"),
  weatherConfig: () => request<WeatherConfig>("/api/weather/config"),
  saveWeatherConfig: (config: {
    city?: string; country?: string; api_key?: string; clear_key?: boolean;
  }) => request<WeatherConfig>("/api/weather/config", "PUT", config),
  saveRailDataConfig: (config: {
    api_key?: string;
    service_details_api_key?: string;
    clear_key?: boolean;
    clear_details_key?: boolean;
    refresh_seconds?: number;
  }) =>
    request<RailDataConfig>("/api/raildata/config", "PUT", config),
  savePlexConfig: (config: {
    server_url: string;
    token?: string;
    clear_token?: boolean;
    default_bitrate_kbps?: number;
  }) =>
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
  uploadUpdate: upload,
  };
}

export const api = createApi(request);
