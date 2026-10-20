import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { api, setCsrf, type Playlist } from "../api";
import { PlaylistCard } from "./PlaylistCard";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  setCsrf("");
});

test("draft edits reorder scenes, keep video cutoff optional, and publish only on save", async () => {
  let saved: Playlist = {
    enabled: false,
    scenes: [
      { id: "clock", kind: "dashboard", enabled: true, title: "Clock", duration: 60,
        url: "", layout: "clock", text: "" },
      { id: "video", kind: "video", enabled: true, title: "Video", duration: 300,
        url: "https://example.com/live", layout: "clock-notice", text: "" },
    ],
  };
  const fetch = vi.fn(async (_path: string, init: RequestInit = {}) => {
    if (init.method === "PUT") saved = JSON.parse(init.body as string) as Playlist;
    return { ok: true, json: async () => saved };
  });
  vi.stubGlobal("fetch", fetch);
  vi.spyOn(api, "plexConfig").mockResolvedValue({
    server_url: "", configured: false, default_bitrate_kbps: 4000,
  });
  setCsrf("session-csrf");
  const notify = vi.fn();
  render(<PlaylistCard notify={notify} fail={vi.fn()} />);
  await screen.findByText("Scene 1: Clock");
  fireEvent.click(screen.getByLabelText("Enable carousel"));
  fireEvent.click(screen.getByLabelText(/No cutoff/));
  fireEvent.click(screen.getByLabelText("Enable audio"));
  fireEvent.click(screen.getByLabelText("Move scene 2 up"));
  const first = screen.getByText("Scene 1: Video").closest("fieldset")!;
  fireEvent.click(within(first).getByLabelText("Include this scene"));
  expect(saved.enabled).toBe(false);
  expect(saved.scenes[0].id).toBe("clock");
  expect(fetch).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByText("Save and apply"));
  await waitFor(() => expect(notify).toHaveBeenCalledWith(expect.stringContaining("saved and applied"), "success"));
  expect(saved.enabled).toBe(true);
  expect(saved.scenes.map((scene) => scene.id)).toEqual(["video", "clock"]);
  expect(saved.scenes[0].duration).toBeNull();
  expect(saved.scenes[0].enabled).toBe(false);
  expect(saved.scenes[0].audio_enabled).toBe(true);
  const request = fetch.mock.calls.find(([, init]) => init?.method === "PUT")![1]!;
  expect((request.headers as Record<string, string>)["X-CSRF-Token"]).toBe("session-csrf");
});

test("adds and removes dashboard scenes without publishing partial changes", async () => {
  vi.spyOn(api, "playlist").mockResolvedValue({ enabled: false, scenes: [] });
  vi.spyOn(api, "plexConfig").mockResolvedValue({
    server_url: "", configured: false, default_bitrate_kbps: 4000,
  });
  const save = vi.spyOn(api, "savePlaylist");
  render(<PlaylistCard notify={vi.fn()} fail={vi.fn()} />);
  await waitFor(() => expect((screen.getByLabelText("New scene type") as HTMLSelectElement).disabled).toBe(false));
  fireEvent.change(screen.getByLabelText("New scene type"), { target: { value: "dashboard" } });
  fireEvent.click(screen.getByText("Add scene"));
  expect(screen.getByLabelText("Scene duration (seconds)")).toHaveProperty("value", "60");
  fireEvent.change(screen.getByLabelText("Notice text"), { target: { value: "Welcome" } });
  fireEvent.click(screen.getByLabelText("Remove scene 1"));
  expect(screen.queryByLabelText("Notice text")).toBeNull();
  expect(save).not.toHaveBeenCalled();
});

test("a rejected save keeps the draft and displays the server error", async () => {
  vi.spyOn(api, "playlist").mockResolvedValue({ enabled: false, scenes: [] });
  vi.spyOn(api, "plexConfig").mockResolvedValue({
    server_url: "", configured: false, default_bitrate_kbps: 4000,
  });
  vi.spyOn(api, "savePlaylist").mockRejectedValue(new Error("Enable at least one scene"));
  const fail = vi.fn();
  const notify = vi.fn();
  render(<PlaylistCard notify={notify} fail={fail} />);
  await waitFor(() => expect((screen.getByLabelText("Enable carousel") as HTMLInputElement).disabled).toBe(false));
  fireEvent.click(screen.getByLabelText("Enable carousel"));
  fireEvent.click(screen.getByText("Save and apply"));
  await waitFor(() => expect(fail).toHaveBeenCalledWith(expect.objectContaining({ message: "Enable at least one scene" })));
  expect(screen.getByLabelText("Enable carousel")).toHaveProperty("checked", true);
  expect(notify).not.toHaveBeenCalled();
});

test("playback status shows failures alongside the matching saved scene", async () => {
  vi.spyOn(api, "plexConfig").mockResolvedValue({
    server_url: "", configured: false, default_bitrate_kbps: 4000,
  });
  vi.spyOn(api, "playlist").mockResolvedValue({
    enabled: true,
    scenes: [{ id: "bad", kind: "video", title: "News", enabled: true, duration: 300,
      url: "https://example.com/live.m3u8", layout: "clock", text: "" }],
  });

  vi.spyOn(api, "playlistStatus").mockResolvedValue({
    failures: { bad: { message: "Stream returned a web page or other non-HLS content.", time: 100 } },
    updated: 100,
  });
  render(<PlaylistCard notify={vi.fn()} fail={vi.fn()} />);
  await screen.findByText("Scene 1: News");
  fireEvent.click(screen.getByText("Check playback status"));
  expect(await screen.findByRole("status")).toHaveProperty("textContent",
    expect.stringContaining("Stream returned a web page"));
});

test("selects a Plex library item for a video scene and saves only its identifier", async () => {
  vi.spyOn(api, "playlist").mockResolvedValue({
    enabled: false,
    scenes: [{ id: "movie", kind: "video", title: "Movie", enabled: true, duration: 300,
      url: "", source: "url", plex_rating_key: "", layout: "clock", text: "" }],
  });
  vi.spyOn(api, "plexConfig").mockResolvedValue({
    server_url: "http://plex.local:32400", configured: true, default_bitrate_kbps: 4000,
  });
  const browse = vi.spyOn(api, "plexBrowse").mockImplementation(async (key) => ({
    items: key
      ? [{ id: "42", title: "Example Film", type: "movie", container: false }]
      : [{ key: "1", title: "Movies", type: "movie", container: true }],
  }));
  const save = vi.spyOn(api, "savePlaylist").mockImplementation(async (playlist) => playlist);
  render(<PlaylistCard notify={vi.fn()} fail={vi.fn()} />);
  await screen.findByText("Scene 1: Movie");
  fireEvent.change(screen.getByLabelText("Video source"), { target: { value: "plex" } });
  fireEvent.click(screen.getByText("Choose Plex media"));
  fireEvent.click(await screen.findByText("Movies ›"));
  fireEvent.click(await screen.findByText("Example Film"));
  expect(screen.getByText("Selected from Plex: Example Film")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Plex target bitrate (kbps)"), {
    target: { value: "6500" },
  });
  fireEvent.click(screen.getByText("Save and apply"));
  await waitFor(() => expect(save).toHaveBeenCalled());
  expect(save.mock.calls[0][0].scenes[0]).toMatchObject({
    source: "plex", plex_rating_key: "42", url: "", plex_bitrate_kbps: 6500,
  });
  expect(browse).toHaveBeenCalledWith("1");
});
