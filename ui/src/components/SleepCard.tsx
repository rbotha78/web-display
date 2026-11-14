import { useEffect, useRef, useState, type FormEvent } from "react";
import { sleepDays, type SleepConfig, type SleepSchedule, type SleepWindow } from "../api";
import { useDeviceApi } from "../DeviceApiContext";
import type { Notice } from "./Toast";

export function SleepCard({ notify, fail }: {
  notify: (text: string, kind?: Notice["kind"]) => void;
  fail: (error: unknown) => void;
}) {
  const api = useDeviceApi();
  const [status, setStatus] = useState<SleepConfig | null>(null);
  const [draft, setDraft] = useState<SleepSchedule | null>(null);
  const [minutes, setMinutes] = useState(30);
  const [busy, setBusy] = useState(false);
  const [stale, setStale] = useState(false);
  const revision = useRef(0);
  const mutating = useRef(false);

  useEffect(() => {
    let active = true;
    let loaded = false;
    async function refresh() {
      if (mutating.current) return;
      const version = revision.current;
      try {
        const value = await api.sleep();
        if (!active || version !== revision.current) return;
        setStatus(value);
        setStale(false);
        if (!loaded) {
          setDraft({ days: value.days });
          loaded = true;
        }
      } catch (error) {
        if (!active || version !== revision.current) return;
        setStale(true);
        fail(error);
      }
    }
    void refresh();
    const timer = window.setInterval(() => void refresh(), 30000);
    return () => { active = false; window.clearInterval(timer); };
  }, [fail]);

  function change(day: (typeof sleepDays)[number], changes: Partial<SleepWindow>) {
    setDraft((value) => value && { days: { ...value.days, [day]: { ...value.days[day], ...changes } } });
  }

  async function mutate(request: () => Promise<SleepConfig>, message: string) {
    revision.current++;
    mutating.current = true;
    setBusy(true);
    try {
      setStatus(await request());
      setStale(false);
      notify(message, "success");
    } catch (error) {
      setStale(true);
      fail(error);
    } finally {
      revision.current++;
      mutating.current = false;
      setBusy(false);
    }
  }

  function save(event: FormEvent) {
    event.preventDefault();
    if (draft) void mutate(() => api.saveSleep(draft), "Sleep schedule saved on this device.");
  }

  return <section className="card wide">
    <h2>Weekly sleep</h2>
    <p className="muted">During sleep, all video scenes are skipped and background music stops.
      Web, dashboard, train and weather scenes continue. A video-only carousel shows its recovery screen.</p>
    <p className="muted">Times use the device’s local clock, not this browser’s timezone.
      An end before the start continues into the next day, even if that day is disabled.
      Enabled start and end times must differ.</p>
    {status && <>
      <p>Device time: {status.device_time} ({status.timezone})</p>
      <p role="status">Display is {status.sleeping ? "asleep" : "awake"}.
        {status.scheduled_sleep ? " Scheduled sleep is active." : " Outside scheduled sleep."}
        {status.wake_until !== null && ` Temporary wake expires at ${new Date(status.wake_until * 1000).toISOString()} (UTC).`}</p>
    </>}
    {stale && <p role="alert">Sleep status unavailable; the last known state may be stale.</p>}
    <form onSubmit={save}>
      {draft && sleepDays.map((day) => {
        const window = draft.days[day];
        const title = day[0].toUpperCase() + day.slice(1);
        return <fieldset key={day} disabled={busy}>
          <legend>{title}</legend>
          <div className="actions">
            <label><input type="checkbox" checked={window.enabled}
              aria-label={`${title} sleep enabled`}
              onChange={(event) => change(day, { enabled: event.target.checked })} />Enabled</label>
            <label>Start<input type="time" aria-label={`${title} sleep start`} value={window.start}
              required={window.enabled} disabled={!window.enabled}
              onChange={(event) => change(day, { start: event.target.value })} /></label>
            <label>End<input type="time" aria-label={`${title} sleep end`} value={window.end}
              required={window.enabled} disabled={!window.enabled}
              onChange={(event) => change(day, { end: event.target.value })} /></label>
          </div>
        </fieldset>;
      })}
      <button className="primary" disabled={busy || !draft}>Save sleep schedule</button>
    </form>
    <label>Temporary wake duration
      <select value={minutes} disabled={busy} onChange={(event) => setMinutes(Number(event.target.value))}>
        {[15, 30, 60, 120, 240].map((value) => <option key={value} value={value}>{value} minutes</option>)}
      </select>
    </label>
    <div className="actions">
      <button type="button" disabled={busy || !status || stale}
        onClick={() => void mutate(() => api.wake(minutes), "Temporary wake started; schedule unchanged.")}>
        Wake temporarily
      </button>
      <button type="button" disabled={busy || !status?.wake_until || stale}
        onClick={() => void mutate(() => api.wake(0), "Temporary wake cancelled; schedule resumed.")}>
        Cancel temporary wake
      </button>
    </div>
  </section>;
}
