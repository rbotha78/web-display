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
  fireEvent.change(screen.getByLabelText("Music station"), {
    target: { value: "/library/sections/3/stations/1" },
  });
  fireEvent.click(checkbox);
  fireEvent.change(screen.getByLabelText("Music volume"), { target: { value: "15" } });
  fireEvent.click(screen.getByText("Save music settings"));
  await waitFor(() => expect(save).toHaveBeenCalledWith({
    enabled: true, station_key: "/library/sections/3/stations/1", volume: 15,
  }));
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
