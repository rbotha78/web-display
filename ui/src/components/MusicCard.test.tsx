import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { api } from "../api";
import { MusicCard } from "./MusicCard";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test("defaults off and requires choosing an advertised station before enabling music", async () => {
  const config = { enabled: false, station_key: "", volume: 30,
    status: { state: "disabled", title: "", error: "" } };
  vi.spyOn(api, "musicConfig").mockResolvedValue(config);
  vi.spyOn(api, "musicStations").mockResolvedValue({ items: [
    { key: "/library/sections/3/stations/1", title: "Library Radio", library: "Music" },
  ] });
  const save = vi.spyOn(api, "saveMusicConfig").mockResolvedValue({
    ...config, enabled: true, station_key: "/library/sections/3/stations/1",
  });
  render(<MusicCard configured notify={vi.fn()} fail={vi.fn()} />);
  await screen.findByText("Music: disabled");
  const checkbox = screen.getByLabelText("Enable background music") as HTMLInputElement;
  expect(checkbox.checked).toBe(false);
  expect(checkbox.disabled).toBe(true);
  await screen.findByText("Music: Library Radio");
  fireEvent.change(screen.getByLabelText("Music source"), {
    target: { value: "/library/sections/3/stations/1" },
  });
  fireEvent.click(checkbox);
  fireEvent.change(screen.getByLabelText("Music volume"), { target: { value: "15" } });
  fireEvent.click(screen.getByText("Save music settings"));
  await waitFor(() => expect(save).toHaveBeenCalledWith({
    enabled: true, station_key: "/library/sections/3/stations/1", volume: 15,
  }));
});

test("selects smart playlists without changing their rules or order", async () => {
  const config = { enabled: false, station_key: "", volume: 30,
    status: { state: "disabled", title: "", error: "" } };
  vi.spyOn(api, "musicConfig").mockResolvedValue(config);
  vi.spyOn(api, "musicStations").mockResolvedValue({ items: [
    { key: "/playlists/11/items", title: "Favourites", library: "Playlists", kind: "playlist", smart: true },
  ] });
  const save = vi.spyOn(api, "saveMusicConfig").mockImplementation(async (settings) => ({ ...config, ...settings }));
  render(<MusicCard configured notify={vi.fn()} fail={vi.fn()} />);
  await screen.findByText("Playlists: Favourites (smart playlist)");
  fireEvent.change(screen.getByLabelText("Music source"), { target: { value: "/playlists/11/items" } });
  fireEvent.click(screen.getByLabelText("Enable background music"));
  fireEvent.click(screen.getByText("Save music settings"));
  await waitFor(() => expect(save).toHaveBeenCalledWith({
    enabled: true, station_key: "/playlists/11/items", volume: 30,
  }));
});

test.each(["artist", "album"] as const)("builds and saves a multi-%s mix with search and pagination", async (kind) => {
  const config = { enabled: false, station_key: "", volume: 30,
    status: { state: "disabled", title: "", error: "" } };
  vi.spyOn(api, "musicConfig").mockResolvedValue(config);
  vi.spyOn(api, "musicStations").mockResolvedValue({ items: [] });
  const browse = vi.spyOn(api, "musicBrowse").mockImplementation(async (query = {}) => {
    if (!query.library) return { items: [{ id: "3", title: "Music", type: "library" }], total: 1, offset: 0 };
    const offset = query.offset ?? 0;
    return { items: [{ id: offset ? "502" : "501", title: offset ? "Second choice" : "First choice", type: kind }],
      total: 101, offset };
  });
  const save = vi.spyOn(api, "saveMusicConfig").mockImplementation(async (settings) => ({ ...config, ...settings }));
  render(<MusicCard configured notify={vi.fn()} fail={vi.fn()} />);
  await screen.findByText("Music: disabled");
  fireEvent.click(screen.getByText(`Build ${kind} mix`));
  fireEvent.click(await screen.findByLabelText("First choice"));
  fireEvent.change(screen.getByLabelText(`Search ${kind === "artist" ? "artists" : "albums"}`), {
    target: { value: "Choice" },
  });
  fireEvent.click(screen.getByText("Search music"));
  await waitFor(() => expect(browse).toHaveBeenCalledWith({ library: "3", kind, search: "Choice", offset: 0 }));
  fireEvent.click(await screen.findByText("Next music page"));
  fireEvent.click(await screen.findByLabelText("Second choice"));
  fireEvent.click(screen.getByLabelText("Enable background music"));
  fireEvent.click(screen.getByText("Save music settings"));
  await waitFor(() => expect(save).toHaveBeenCalledWith({
    enabled: true, station_key: `mix:${kind}:501,502`, volume: 30,
  }));
  fireEvent.click(screen.getByText("Remove First choice"));
  expect(screen.getByRole("option", { name: `Custom ${kind} mix (1 selections)` })).toBeTruthy();
});

test("loads a saved mix for editing and reports source discovery failures", async () => {
  vi.spyOn(api, "musicConfig").mockResolvedValue({
    enabled: true, station_key: "mix:artist:501,502", volume: 30,
    status: { state: "playing", title: "Song", error: "" },
  });
  vi.spyOn(api, "musicStations").mockRejectedValue(new Error("Plex unavailable"));
  vi.spyOn(api, "musicBrowse").mockImplementation(async (query = {}) => ({
    items: query.key ? [{ id: "501", title: "Saved artist", type: "artist" },
      { id: "502", title: "Other artist", type: "artist" }] : [],
    total: query.key ? 2 : 0, offset: 0,
  }));
  const fail = vi.fn();
  render(<MusicCard configured notify={vi.fn()} fail={fail} />);
  await waitFor(() => expect(screen.getByText("Build artist mix")).toHaveProperty("disabled", false));
  fireEvent.click(screen.getByText("Build artist mix"));
  expect(await screen.findByText("Remove Saved artist")).toBeTruthy();
  expect(screen.getByText("Remove Other artist")).toBeTruthy();
  expect(fail).toHaveBeenCalledWith(expect.objectContaining({ message: "Plex unavailable" }));
});

test("keeps a disabled saved mix without looking it up after Plex credentials are removed", async () => {
  vi.spyOn(api, "musicConfig").mockResolvedValue({
    enabled: false, station_key: "mix:artist:501", volume: 30,
    status: { state: "disabled", title: "", error: "" },
  });
  const browse = vi.spyOn(api, "musicBrowse").mockResolvedValue({ items: [], total: 0, offset: 0 });
  const stations = vi.spyOn(api, "musicStations").mockResolvedValue({ items: [] });
  render(<MusicCard configured={false} notify={vi.fn()} fail={vi.fn()} />);
  await screen.findByText("Music: disabled");
  expect(browse).not.toHaveBeenCalled();
  expect(stations).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Music source")).toHaveProperty("value", "mix:artist:501");
});

test("shows sanitized playback failure and permits disabling an unavailable station", async () => {
  vi.spyOn(api, "musicConfig").mockResolvedValue({ enabled: true,
    station_key: "/library/sections/3/stations/1", volume: 15,
    status: { state: "error", title: "", error: "Audio playback failed; disable and re-enable music to retry." } });
  vi.spyOn(api, "musicStations").mockResolvedValue({ items: [] });
  vi.spyOn(api, "saveMusicConfig").mockResolvedValue({ enabled: false,
    station_key: "/library/sections/3/stations/1", volume: 15,
    status: { state: "disabled", title: "", error: "" } });
  render(<MusicCard configured notify={vi.fn()} fail={vi.fn()} />);
  await screen.findByRole("alert");
  fireEvent.click(screen.getByLabelText("Enable background music"));
  fireEvent.click(screen.getByText("Save music settings"));
  await waitFor(() => expect(api.saveMusicConfig).toHaveBeenCalledWith({
    enabled: false, station_key: "/library/sections/3/stations/1", volume: 15,
  }));
});
