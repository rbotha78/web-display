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

test("creates a Plex smart playlist matching all selected filters without changing playback", async () => {
  const config = { enabled: false, station_key: "", volume: 30,
    status: { state: "disabled", title: "", error: "" } };
  vi.spyOn(api, "musicConfig").mockResolvedValue(config);
  vi.spyOn(api, "musicStations").mockResolvedValue({ items: [] });
  vi.spyOn(api, "musicBrowse").mockResolvedValue({
    items: [{ id: "3", title: "Music", type: "library" }], total: 1, offset: 0,
  });
  vi.spyOn(api, "musicFilters").mockResolvedValue({
    decade: [{ id: "1990", title: "1990s" }], genre: [{ id: "101", title: "Rock" }],
    mood: [{ id: "201", title: "Energetic" }],
  });
  const preview = vi.spyOn(api, "previewSmartPlaylist").mockResolvedValue({ count: 12 });
  const create = vi.spyOn(api, "createSmartPlaylist").mockResolvedValue({
    key: "/playlists/901/items", title: "90s Energy", library: "Playlists", kind: "playlist", smart: true,
  });
  const save = vi.spyOn(api, "saveMusicConfig");
  const notify = vi.fn();
  render(<MusicCard configured notify={notify} fail={vi.fn()} />);
  await screen.findByText("Music: disabled");
  fireEvent.click(screen.getByRole("button", { name: "Create Plex smart playlist" }));
  await screen.findByRole("option", { name: "Energetic" });
  expect(screen.getByText("Create playlist in Plex")).toHaveProperty("disabled", true);
  fireEvent.change(screen.getByLabelText("Playlist name"), { target: { value: "90s Energy" } });
  fireEvent.keyDown(screen.getByLabelText("Playlist name"), { key: "Enter" });
  expect(save).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Album decade"), { target: { value: "1990" } });
  fireEvent.change(screen.getByLabelText("Track genre"), { target: { value: "101" } });
  fireEvent.change(screen.getByLabelText("Track mood"), { target: { value: "201" } });
  fireEvent.click(screen.getByText("Preview matching tracks"));
  await screen.findByText("12 tracks match all selected criteria.");
  const criteria = { library: "3", decade: "1990", genre: "101", mood: "201" };
  expect(preview).toHaveBeenCalledWith(criteria);
  fireEvent.click(screen.getByText("Create playlist in Plex"));
  await screen.findByRole("option", { name: "Playlists: 90s Energy (smart playlist)" });
  expect(create).toHaveBeenCalledWith({ ...criteria, title: "90s Energy" });
  expect(screen.getByLabelText("Music source")).toHaveProperty("value", "");
  expect(save).not.toHaveBeenCalled();
  expect(notify).toHaveBeenCalledWith(expect.stringContaining("created in Plex"), "success");
});

test("clears smart rules on library changes, handles empty choices and reports creation failures", async () => {
  vi.spyOn(api, "musicConfig").mockResolvedValue({ enabled: false, station_key: "", volume: 30,
    status: { state: "disabled", title: "", error: "" } });
  vi.spyOn(api, "musicStations").mockResolvedValue({ items: [] });
  vi.spyOn(api, "musicBrowse").mockResolvedValue({
    items: [{ id: "3", title: "Music", type: "library" }, { id: "4", title: "Other", type: "library" }],
    total: 2, offset: 0,
  });
  vi.spyOn(api, "musicFilters").mockImplementation(async (library) => ({
    decade: [{ id: library === "3" ? "1990" : "2000", title: library === "3" ? "1990s" : "2000s" }],
    genre: [], mood: [],
  }));
  const preview = vi.spyOn(api, "previewSmartPlaylist").mockResolvedValue({ count: 0 });
  const create = vi.spyOn(api, "createSmartPlaylist").mockRejectedValue(new Error("Plex denied playlist creation"));
  const fail = vi.fn();
  render(<MusicCard configured notify={vi.fn()} fail={fail} />);
  await screen.findByText("Music: disabled");
  fireEvent.click(screen.getByRole("button", { name: "Create Plex smart playlist" }));
  await screen.findByText("No track moods are available in this library.");
  fireEvent.change(screen.getByLabelText("Playlist name"), { target: { value: "Decade" } });
  fireEvent.change(screen.getByLabelText("Album decade"), { target: { value: "1990" } });
  fireEvent.click(screen.getByText("Preview matching tracks"));
  await screen.findByText("0 tracks match all selected criteria.");
  expect(screen.getByText("Create playlist in Plex")).toHaveProperty("disabled", true);
  fireEvent.change(screen.getByLabelText("Smart playlist library"), { target: { value: "4" } });
  await screen.findByRole("option", { name: "2000s" });
  expect(screen.getByLabelText("Album decade")).toHaveProperty("value", "");
  expect(screen.queryByText("0 tracks match all selected criteria.")).toBeNull();
  fireEvent.change(screen.getByLabelText("Album decade"), { target: { value: "2000" } });
  fireEvent.click(screen.getByText("Create playlist in Plex"));
  await waitFor(() => expect(fail).toHaveBeenCalledWith(expect.objectContaining({ message: "Plex denied playlist creation" })));
  expect(create).toHaveBeenCalledWith({ title: "Decade", library: "4", decade: "2000", genre: "", mood: "" });
  expect(screen.getByLabelText("Playlist name")).toHaveProperty("value", "Decade");
  expect(preview).toHaveBeenCalledTimes(1);
});

test("updates enabled state from player controls without discarding music source or volume drafts", async () => {
  const config = { enabled: true, station_key: "/playlists/11/items", volume: 30,
    status: { state: "playing", title: "Song", error: "" } };
  vi.spyOn(api, "musicConfig").mockResolvedValue(config);
  vi.spyOn(api, "musicStations").mockResolvedValue({ items: [
    { key: "/playlists/11/items", title: "Saved", library: "Playlists" },
    { key: "/playlists/12/items", title: "Draft", library: "Playlists" },
  ] });
  const notify = vi.fn(), fail = vi.fn();
  const rendered = render(<MusicCard configured notify={notify} fail={fail} />);
  await screen.findByText("Playlists: Draft");
  fireEvent.change(screen.getByLabelText("Music source"), { target: { value: "/playlists/12/items" } });
  fireEvent.change(screen.getByLabelText("Music volume"), { target: { value: "45" } });
  rendered.rerender(<MusicCard configured notify={notify} fail={fail} playbackConfig={{
    ...config, enabled: false, status: { state: "disabled", title: "", error: "" },
  }} />);
  expect(screen.getByLabelText("Enable background music")).toHaveProperty("checked", false);
  expect(screen.getByLabelText("Music source")).toHaveProperty("value", "/playlists/12/items");
  expect(screen.getByLabelText("Music volume")).toHaveProperty("value", "45");
});

test("saves weekday sources while preserving saved mixes and marking today", async () => {
  const days = { monday: "", tuesday: "", wednesday: "", thursday: "", friday: "", saturday: "",
    sunday: "mix:artist:501,502" };
  const config = { enabled: true, station_key: "/playlists/11/items", volume: 30, day_sources: days,
    active_day: "saturday" as const, active_station_key: "/playlists/11/items",
    status: { state: "playing", title: "Song", error: "" } };
  vi.spyOn(api, "musicConfig").mockResolvedValue(config);
  vi.spyOn(api, "musicStations").mockResolvedValue({ items: [
    { key: "/playlists/11/items", title: "Favourites", library: "Playlists" },
    { key: "/playlists/12/items", title: "Friday Mix", library: "Playlists" },
  ] });
  const save = vi.spyOn(api, "saveMusicConfig").mockImplementation(async (settings) => ({ ...config, ...settings }));
  render(<MusicCard configured notify={vi.fn()} fail={vi.fn()} />);
  expect(await screen.findByLabelText("Saturday (today)")).toBeTruthy();
  expect(screen.getByText("Today (Saturday) uses the saved source: Playlists: Favourites")).toBeTruthy();
  const sunday = screen.getByLabelText("Sunday") as HTMLSelectElement;
  expect(sunday.selectedOptions[0].textContent).toBe("Custom artist mix (2 selections)");
  await screen.findAllByText("Playlists: Friday Mix");
  fireEvent.change(screen.getByLabelText("Friday"), { target: { value: "/playlists/12/items" } });
  fireEvent.click(screen.getByText("Save music settings"));
  await waitFor(() => expect(save).toHaveBeenCalledWith({
    enabled: true, station_key: "/playlists/11/items", volume: 30,
    day_sources: { ...days, friday: "/playlists/12/items" },
  }));
});

test("shows the follower's music group and leaves it", async () => {
  const config = { enabled: false, station_key: "", volume: 30,
    status: { state: "playing", title: "Song", error: "" } };
  vi.spyOn(api, "musicConfig").mockResolvedValue(config);
  vi.spyOn(api, "musicStations").mockResolvedValue({ items: [] });
  vi.spyOn(api, "musicSync").mockResolvedValue({ supported: true, role: "follower", group_id: "g".repeat(16),
    leader: { name: "Kitchen", hostname: "kitchen-pi", port: 8443, addresses: ["192.168.0.249"] },
    members: [], connected: false, error: "Cannot reach the leader display" });
  const leave = vi.spyOn(api, "leaveMusicSync").mockResolvedValue({ supported: true, role: "off", group_id: "",
    leader: null, members: [], connected: true, error: "" });
  const notify = vi.fn();
  render(<MusicCard configured notify={notify} fail={vi.fn()} />);
  await screen.findByText("Music sync: following Kitchen");
  expect(screen.getByText("Cannot reach the leader display")).toBeTruthy();
  fireEvent.click(screen.getByText("Leave group"));
  await waitFor(() => expect(notify).toHaveBeenCalledWith("This display left its music group"));
  expect(leave).toHaveBeenCalled();
  expect(screen.queryByText("Music sync: following Kitchen")).toBeNull();
});
