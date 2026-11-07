import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { setCsrf, sleepDays, type SleepConfig } from "../api";
import { SleepCard } from "./SleepCard";

function config(): SleepConfig {
  return {
    days: Object.fromEntries(sleepDays.map((day) => [day, {
      enabled: false, start: "22:00", end: "07:00",
    }])) as SleepConfig["days"],
    sleeping: true, scheduled_sleep: true, wake_until: null,
    device_time: "2026-11-07T22:00:00+00:00", timezone: "GMT",
  };
}

function respond(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

afterEach(() => {
  cleanup();
  setCsrf("");
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

test("saves seven local-day windows and wakes/cancels with CSRF without changing the schedule", async () => {
  let saved = config();
  const fetch = vi.fn(async (path: string, init: RequestInit) => {
    const body = init.body ? JSON.parse(init.body as string) : undefined;
    if (path === "/api/sleep" && init.method === "PUT") saved = { ...saved, ...body };
    if (path === "/api/sleep/wake") saved = {
      ...saved, sleeping: body.duration_minutes === 0,
      wake_until: body.duration_minutes ? 1794096000 : null,
    };
    return respond(saved);
  });
  vi.stubGlobal("fetch", fetch);
  setCsrf("sleep-csrf");
  const notify = vi.fn();
  render(<SleepCard fail={vi.fn()} notify={notify} />);
  expect(await screen.findByText(/Device time:.*GMT/)).toBeTruthy();
  expect(screen.getByText(/Display is asleep/)).toBeTruthy();
  fireEvent.click(screen.getByLabelText("Monday sleep enabled"));
  fireEvent.change(screen.getByLabelText("Monday sleep start"), { target: { value: "23:00" } });
  fireEvent.change(screen.getByLabelText("Monday sleep end"), { target: { value: "06:00" } });
  fireEvent.click(screen.getByText("Save sleep schedule"));
  await waitFor(() => expect(notify).toHaveBeenCalledWith("Sleep schedule saved on this device.", "success"));
  expect(saved.days.monday).toEqual({ enabled: true, start: "23:00", end: "06:00" });
  expect(Object.keys(saved.days)).toHaveLength(7);
  const schedule = saved.days;
  fireEvent.change(screen.getByLabelText("Temporary wake duration"), { target: { value: "120" } });
  fireEvent.click(screen.getByText("Wake temporarily"));
  expect(await screen.findByText(/Display is awake.*Temporary wake expires.*UTC/)).toBeTruthy();
  fireEvent.click(screen.getByText("Cancel temporary wake"));
  expect(await screen.findByText(/Display is asleep/)).toBeTruthy();
  expect(saved.days).toEqual(schedule);
  const writes = fetch.mock.calls.filter(([, init]) => init.method !== "GET");
  expect(writes.map(([path, init]) => [path, init.method])).toEqual([
    ["/api/sleep", "PUT"], ["/api/sleep/wake", "POST"], ["/api/sleep/wake", "POST"],
  ]);
  expect(JSON.parse(writes[1][1].body as string)).toEqual({ duration_minutes: 120 });
  expect(JSON.parse(writes[2][1].body as string)).toEqual({ duration_minutes: 0 });
  writes.forEach(([, init]) => expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("sleep-csrf"));
});

test("save failure preserves edited windows, reports stale status and never claims success", async () => {
  const fail = vi.fn(), notify = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async (_path: string, init: RequestInit) => init.method === "PUT"
    ? respond({ error: "Sleep start and end must differ" }, 400) : respond(config())));
  render(<SleepCard fail={fail} notify={notify} />);
  fireEvent.click(await screen.findByLabelText("Tuesday sleep enabled"));
  fireEvent.change(screen.getByLabelText("Tuesday sleep end"), { target: { value: "22:00" } });
  fireEvent.click(screen.getByText("Save sleep schedule"));
  await waitFor(() => expect(fail).toHaveBeenCalled());
  expect((screen.getByLabelText("Tuesday sleep enabled") as HTMLInputElement).checked).toBe(true);
  expect((screen.getByLabelText("Tuesday sleep end") as HTMLInputElement).value).toBe("22:00");
  expect(screen.getByRole("alert").textContent).toContain("may be stale");
  expect((screen.getByText("Wake temporarily") as HTMLButtonElement).disabled).toBe(true);
  expect(notify).not.toHaveBeenCalled();
});

test("load errors do not manufacture an awake status or permit a default schedule save", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => respond({ error: "Cannot read sleep schedule" }, 500)));
  const fail = vi.fn();
  render(<SleepCard fail={fail} notify={vi.fn()} />);
  await waitFor(() => expect(fail).toHaveBeenCalled());
  expect(screen.queryByText(/Display is awake/)).toBeNull();
  expect((screen.getByText("Save sleep schedule") as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByText("Wake temporarily") as HTMLButtonElement).disabled).toBe(true);
});

test("polling refreshes status without discarding unsaved windows and is removed on unmount", async () => {
  vi.useFakeTimers();
  let saved = config();
  const fetch = vi.fn(async () => respond(saved));
  vi.stubGlobal("fetch", fetch);
  let view: ReturnType<typeof render>;
  await act(async () => { view = render(<SleepCard fail={vi.fn()} notify={vi.fn()} />); });
  fireEvent.click(screen.getByLabelText("Friday sleep enabled"));
  saved = { ...config(), sleeping: false, scheduled_sleep: false };
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  expect(screen.getByText(/Display is awake/)).toBeTruthy();
  expect((screen.getByLabelText("Friday sleep enabled") as HTMLInputElement).checked).toBe(true);
  expect(fetch).toHaveBeenCalledTimes(2);
  view!.unmount();
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  expect(fetch).toHaveBeenCalledTimes(2);
});

test("a poll started before wake cannot overwrite the successful wake response", async () => {
  vi.useFakeTimers();
  let reads = 0;
  let finishPoll!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn((path: string) => {
    if (path === "/api/sleep/wake") return Promise.resolve(respond({ ...config(), sleeping: false, wake_until: 1794096000 }));
    reads++;
    if (reads === 2) return new Promise<Response>((resolve) => { finishPoll = resolve; });
    return Promise.resolve(respond(config()));
  }));
  await act(async () => { render(<SleepCard fail={vi.fn()} notify={vi.fn()} />); });
  await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
  await act(async () => { fireEvent.click(screen.getByText("Wake temporarily")); });
  expect(screen.getByText(/Display is awake/)).toBeTruthy();
  await act(async () => { finishPoll(respond(config())); });
  expect(screen.getByText(/Display is awake/)).toBeTruthy();
});
