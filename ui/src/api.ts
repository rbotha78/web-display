export interface Session {
  csrf: string;
}

export interface Config {
  url: string;
  auto_ap: boolean;
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

export const api = {
  health: () => request<{ paired: boolean }>("/health"),
  session: () => request<Session>("/api/session"),
  login: (password: string) => request<Session>("/api/login", "POST", { password }),
  pair: (code: string, password: string) =>
    request<Session>("/api/pair", "POST", { code, password }),
  logout: () => request<object>("/api/logout", "POST"),
  config: () => request<Config>("/api/config"),
  saveConfig: (config: Partial<Config>) => request<Config>("/api/config", "PUT", config),
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
};
