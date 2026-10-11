import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ManagerApp } from "./ManagerApp";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
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
