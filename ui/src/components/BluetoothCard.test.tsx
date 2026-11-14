import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { BluetoothStatus } from "../api";
import { BluetoothCard } from "./BluetoothCard";

const base: BluetoothStatus = {
  supported: true, adapter: true, address: "", name: "", enabled: false, delay_ms: 0,
  connected: false, active: false,
};

function respond(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("finds, pairs and tunes a speaker, preferring audio devices", async () => {
  let status = { ...base };
  const fetch = vi.fn(async (path: string, init: RequestInit) => {
    const body = init.body ? JSON.parse(init.body as string) : undefined;
    if (path === "/api/bluetooth/scan") {
      return respond({ items: [
        { address: "11:22:33:44:55:66", name: "Phone", paired: false, connected: false, audio: false },
        { address: "AA:BB:CC:DD:EE:01", name: "Kitchen", paired: false, connected: false, audio: true },
      ] });
    }
    if (path === "/api/bluetooth/pair") {
      status = { ...status, address: body.address, name: body.name, enabled: true, connected: true, active: true };
    }
    if (path === "/api/bluetooth" && init.method === "PUT") status = { ...status, ...body };
    return respond(status);
  });
  vi.stubGlobal("fetch", fetch);
  const notify = vi.fn();
  render(<BluetoothCard notify={notify} fail={vi.fn()} />);
  fireEvent.click(await screen.findByText("Find speakers"));
  expect(await screen.findByDisplayValue("Kitchen")).toBeTruthy();
  fireEvent.click(screen.getByText("Pair and use"));
  expect(await screen.findByText("Playing through speaker")).toBeTruthy();
  expect(JSON.parse(fetch.mock.calls.find(([path]) => path === "/api/bluetooth/pair")![1].body as string))
    .toEqual({ address: "AA:BB:CC:DD:EE:01", name: "Kitchen" });
  fireEvent.change(screen.getByLabelText("Extra speaker delay (ms)"), { target: { value: "150" } });
  fireEvent.click(screen.getByText("Save delay"));
  await waitFor(() => expect(notify).toHaveBeenCalledWith("Speaker delay saved.", "success"));
  expect(status.delay_ms).toBe(150);
});

test("explains missing adapters and base images", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => respond({ ...base, adapter: false })));
  render(<BluetoothCard notify={vi.fn()} fail={vi.fn()} />);
  expect(await screen.findByText(/needs a USB Bluetooth adapter/)).toBeTruthy();
  expect(screen.queryByText("Find speakers")).toBeNull();
});
