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

export class ManagerError extends Error {}

let token = "";

export function setToken(value: string) {
  token = value;
}

async function request<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "X-Manager-Token": token };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let payload: { error?: string } = {};
  try {
    payload = await response.json();
  } catch {
    // Keep the generic message below.
  }
  if (!response.ok) throw new ManagerError(payload.error || "Request failed (" + response.status + ")");
  return payload as T;
}

export const api = {
  devices: () => request<{ devices: Device[]; manager_version?: string }>("/api/devices"),
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
  checkUpdate: (id: string) =>
    request<{ available: boolean; latest: { version: string } | null }>(
      "/api/devices/" + id + "/update-check",
      "POST",
      {},
    ),
  installUpdate: (id: string) =>
    request<{ version: string }>("/api/devices/" + id + "/update-install", "POST", {}),
  updateAll: () => request<{ results: UpdateOutcome[] }>("/api/update-all", "POST", {}),
};
