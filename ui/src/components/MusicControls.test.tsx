import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { api, type MusicConfig } from "../api";
import { MusicControls } from "./MusicControls";

afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

const playing: MusicConfig = { enabled: true, station_key: "/playlists/11/items", volume: 30,
  status: { state: "playing", title: "Song", artist: "Singer", error: "", paused_by_user: false, rating_key: "42", liked: false } };

test("likes the displayed song in Plex and shows only a confirmed five-star rating", async () => {
  let current = playing;
  vi.spyOn(api, "musicConfig").mockImplementation(async () => current);
  let finish!: (value: MusicConfig) => void;
  const like = vi.spyOn(api, "likeMusic").mockReturnValue(new Promise((done) => { finish = done; }));
  const enabled = vi.fn();
  render(<MusicControls fail={vi.fn()} onEnabledChange={enabled} />);
  await screen.findByText("Song");
  expect(screen.getByText("Singer")).toBeTruthy();
  const button = screen.getByRole("button", { name: "Like song in Plex" });
  expect(button.getAttribute("aria-pressed")).toBe("false");
  fireEvent.click(button);
  expect(like).toHaveBeenCalledWith("42");
  expect(button).toHaveProperty("disabled", true);
  expect(button.getAttribute("aria-pressed")).toBe("false");
  current = { ...playing, status: { ...playing.status, liked: true } };
  await act(async () => { finish(current); });
  expect(button.getAttribute("aria-pressed")).toBe("true");
  expect(button).toHaveProperty("disabled", true);
  expect(button.title).toBe("Liked in Plex (5 stars)");
  expect(enabled).not.toHaveBeenCalled();
});

test("a failed like keeps the heart unfilled and reports the server error", async () => {
  vi.spyOn(api, "musicConfig").mockResolvedValue(playing);
  const error = new Error("The song has changed");
  vi.spyOn(api, "likeMusic").mockRejectedValue(error);
  const fail = vi.fn();
  render(<MusicControls fail={fail} onEnabledChange={vi.fn()} />);
  await screen.findByText("Song");
  const button = screen.getByRole("button", { name: "Like song in Plex" });
  fireEvent.click(button);
  await waitFor(() => expect(fail).toHaveBeenCalledWith(error));
  expect(button.getAttribute("aria-pressed")).toBe("false");
  expect(button).toHaveProperty("disabled", false);
});

test("the heart follows song changes and existing Plex ratings", async () => {
  vi.useFakeTimers();
  let current: MusicConfig = { ...playing, status: { ...playing.status, liked: true } };
  vi.spyOn(api, "musicConfig").mockImplementation(async () => current);
  render(<MusicControls fail={vi.fn()} onEnabledChange={vi.fn()} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  const button = screen.getByRole("button", { name: "Like song in Plex" });
  expect(button.getAttribute("aria-pressed")).toBe("true");
  current = { ...playing, status: { ...playing.status, title: "Next song", rating_key: "43" } };
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(button.getAttribute("aria-pressed")).toBe("false");
  expect(button).toHaveProperty("disabled", false);
});

test("pauses, resumes, skips and stops the saved source without editing settings", async () => {
  let current = playing;
  vi.spyOn(api, "musicConfig").mockImplementation(async () => current);
  const control = vi.spyOn(api, "controlMusic").mockImplementation(async (action) => {
    current = { ...current, enabled: action === "stop" ? false : action === "play" ? true : current.enabled,
      status: { ...current.status, paused_by_user: action === "pause",
        state: action === "pause" ? "paused" : action === "stop" ? "disabled" : "playing" } };
    return current;
  });
  const enabled = vi.fn();
  render(<MusicControls fail={vi.fn()} onEnabledChange={enabled} />);
  await screen.findByText("Song");
  fireEvent.click(screen.getByText("Pause"));
  fireEvent.click(await screen.findByText("Resume"));
  await screen.findByText("Pause");
  fireEvent.click(screen.getByText("Skip"));
  await waitFor(() => expect(control).toHaveBeenLastCalledWith("skip"));
  await waitFor(() => expect(screen.getByText("Stop")).toHaveProperty("disabled", false));
  fireEvent.click(screen.getByText("Stop"));
  await screen.findByText("Music stopped");
  expect(screen.getByText("Skip")).toHaveProperty("disabled", true);
  expect(screen.getByRole("button", { name: "Like song in Plex" })).toHaveProperty("disabled", true);
  fireEvent.click(screen.getByText("Play"));
  await screen.findByText("Pause");
  expect(control.mock.calls.map(([action]) => action)).toEqual(["pause", "resume", "skip", "stop", "play"]);
  expect(enabled).toHaveBeenCalledTimes(2);
  expect(current.station_key).toBe(playing.station_key);
});

test.each(["sleeping", "unconfigured"])("disables inappropriate controls when %s", async (state) => {
  vi.spyOn(api, "musicConfig").mockResolvedValue(state === "sleeping" ?
    { ...playing, status: { ...playing.status, state: "sleeping", title: "" } } :
    { ...playing, enabled: false, station_key: "", status: { state: "disabled", title: "", error: "" } });
  render(<MusicControls fail={vi.fn()} onEnabledChange={vi.fn()} />);
  await waitFor(() => expect(screen.getByText(state === "sleeping" ? "Pause" : "Play")).toHaveProperty("disabled", true));
  expect(screen.getByText("Skip")).toHaveProperty("disabled", true);
  expect(screen.getByRole("button", { name: "Like song in Plex" })).toHaveProperty("disabled", true);
  if (state === "sleeping") expect(screen.getByText("Stop")).toHaveProperty("disabled", false);
});

test("reports a rejected action without claiming playback changed", async () => {
  vi.spyOn(api, "musicConfig").mockResolvedValue(playing);
  const error = new Error("Music is sleeping");
  vi.spyOn(api, "controlMusic").mockRejectedValue(error);
  const fail = vi.fn();
  const enabled = vi.fn();
  render(<MusicControls fail={fail} onEnabledChange={enabled} />);
  await screen.findByText("Song");
  fireEvent.click(screen.getByText("Pause"));
  await waitFor(() => expect(fail).toHaveBeenCalledWith(error));
  expect(screen.getByText("Pause")).toBeTruthy();
  expect(enabled).not.toHaveBeenCalled();
});

test("does not overlap status polls and aborts an outstanding poll on unmount", async () => {
  vi.useFakeTimers();
  let resolve!: (value: MusicConfig) => void;
  const config = vi.spyOn(api, "musicConfig").mockReturnValue(new Promise((done) => { resolve = done; }));
  const fail = vi.fn();
  const rendered = render(<MusicControls fail={fail} onEnabledChange={vi.fn()} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
  expect(config).toHaveBeenCalledTimes(1);
  const signal = config.mock.calls[0][0];
  rendered.unmount();
  expect(signal?.aborted).toBe(true);
  await act(async () => { resolve(playing); });
  expect(fail).not.toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
});

test("reports a polling outage once instead of repeating notifications", async () => {
  vi.useFakeTimers();
  vi.spyOn(api, "musicConfig").mockRejectedValue(new Error("Device offline"));
  const fail = vi.fn();
  render(<MusicControls fail={fail} onEnabledChange={vi.fn()} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
  expect(fail).toHaveBeenCalledTimes(1);
  expect(screen.getByText("Player unavailable")).toBeTruthy();
  expect(screen.getByText("Play")).toHaveProperty("disabled", true);
  expect(screen.getByRole("button", { name: "Like song in Plex" })).toHaveProperty("disabled", true);
});
