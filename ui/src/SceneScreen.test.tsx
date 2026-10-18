import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { SceneScreen } from "./SceneScreen";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("renders notice text as text and acknowledges rendering without loading admin APIs", async () => {
  const fetch = vi.fn(async (path: string) => ({
    ok: true,
    json: async () => path === "/api/scene"
      ? { kind: "dashboard", revision: "one", layout: "notice", text: "<b>Welcome</b>" }
      : {},
  }));
  vi.stubGlobal("fetch", fetch);
  render(<SceneScreen />);
  expect(await screen.findByText("<b>Welcome</b>")).toBeTruthy();
  expect(document.querySelector(".scene-notice b")).toBeNull();
  await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/scene/ready?revision=one", expect.anything()));
  expect(fetch.mock.calls.every(([path]) => path.startsWith("/api/scene"))).toBe(true);
});

test("recovery explains that failed scenes will retry", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({
    ok: true, json: async () => ({ kind: "recovery", revision: "two", layout: "notice", text: "" }),
  })));
  render(<SceneScreen />);
  expect(await screen.findByText("No carousel scenes are currently available")).toBeTruthy();
  expect(screen.getByText(/retried automatically/)).toBeTruthy();
});
