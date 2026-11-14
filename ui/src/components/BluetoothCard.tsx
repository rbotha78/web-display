import { useCallback, useEffect, useState } from "react";
import type { BluetoothDevice, BluetoothStatus } from "../api";
import { useDeviceApi } from "../DeviceApiContext";
import type { Notice } from "./Toast";

interface Props {
  notify: (text: string, kind?: Notice["kind"]) => void;
  fail: (error: unknown) => void;
  active?: boolean;
}

export function BluetoothCard({ notify, fail, active = true }: Props) {
  const api = useDeviceApi();
  const [status, setStatus] = useState<BluetoothStatus | null>(null);
  const [devices, setDevices] = useState<BluetoothDevice[]>([]);
  const [selected, setSelected] = useState("");
  const [delay, setDelay] = useState(0);
  const [busy, setBusy] = useState<"" | "scan" | "pair" | "save">("");

  const apply = useCallback((value: BluetoothStatus) => {
    setStatus(value);
    setDelay(value.delay_ms);
  }, []);

  const refresh = useCallback(async () => {
    try {
      setStatus(await api.bluetooth());
    } catch (error) {
      fail(error);
    }
  }, [fail]);

  useEffect(() => {
    if (!active) return;
    api.bluetooth().then(apply).catch(fail);
    const timer = window.setInterval(() => void refresh(), 10000);
    return () => window.clearInterval(timer);
  }, [active, apply, fail, refresh]);

  const run = async (kind: typeof busy, work: () => Promise<BluetoothStatus>, done: string) => {
    setBusy(kind);
    try {
      apply(await work());
      notify(done, "success");
    } catch (error) {
      fail(error);
    } finally {
      setBusy("");
    }
  };

  const scan = async () => {
    setBusy("scan");
    try {
      const { items } = await api.scanBluetooth();
      setDevices(items);
      setSelected(items.find((item) => item.audio)?.address ?? items[0]?.address ?? "");
      notify(items.length ? `${items.length} Bluetooth devices found.` : "No Bluetooth devices found.", "info");
    } catch (error) {
      fail(error);
    } finally {
      setBusy("");
    }
  };

  if (!status) {
    return <section className="card"><h2>Bluetooth speaker</h2><p className="muted">Loading…</p></section>;
  }
  const unavailable = !status.supported
    ? "This display's base image does not include Bluetooth audio. Flash a newer image to use a speaker."
    : status.adapter === false
      ? "No Bluetooth adapter found. Raspberry Pi 2 needs a USB Bluetooth adapter."
      : "";
  const state = status.active
    ? { text: "Playing through speaker", kind: "ok" }
    : status.enabled
      ? { text: "Not connected · using wired output", kind: "unknown" }
      : { text: "Off · using wired output", kind: "unknown" };
  const chosen = devices.find((device) => device.address === selected);

  return (
    <section className="card">
      <h2>Bluetooth speaker</h2>
      {unavailable ? <p className="muted">{unavailable}</p> : (
        <>
          {status.address ? (
            <>
              <p>
                {status.name || status.address} <span className={`pill ${state.kind}`}>{state.text}</span>
              </p>
              <label className="check">
                <input type="checkbox" checked={status.enabled} disabled={busy !== ""}
                  onChange={(event) => void run("save", () => api.saveBluetooth({ enabled: event.target.checked }),
                    event.target.checked ? "Speaker enabled." : "Speaker disabled; using wired output.")} />
                <span>Play video audio and music through this speaker</span>
              </label>
              <form className="row" onSubmit={(event) => {
                event.preventDefault();
                void run("save", () => api.saveBluetooth({ delay_ms: delay }), "Speaker delay saved.");
              }}>
                <label className="grow">
                  Extra speaker delay (ms)
                  <input type="number" min="-1000" max="1000" step="10" required value={delay}
                    onChange={(event) => setDelay(event.target.valueAsNumber)} />
                </label>
                <button disabled={busy !== "" || delay === status.delay_ms}>Save delay</button>
              </form>
              <p className="hint">Only affects synced group music. The speaker's reported latency is already
                compensated; raise this if this display still sounds behind the others.</p>
            </>
          ) : <p className="muted">No speaker paired. Audio uses the wired output.</p>}
          <p className="hint">Put the speaker in pairing mode, then search. Pairing takes up to a minute.
            While the speaker is off or out of range, audio falls back to the wired output and the display
            keeps trying to reconnect.</p>
          <div className="row">
            <button type="button" disabled={busy !== ""} onClick={() => void scan()}>
              {busy === "scan" ? "Searching…" : "Find speakers"}
            </button>
          </div>
          {devices.length > 0 && (
            <label>
              Device
              <select value={selected} onChange={(event) => setSelected(event.target.value)}>
                {devices.map((device) => (
                  <option key={device.address} value={device.address}>
                    {device.name}{device.audio ? "" : " (not an audio device)"}{device.paired ? " · paired" : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
          <div className="actions">
            {chosen && (
              <button type="button" className="primary" disabled={busy !== ""}
                onClick={() => void run("pair", () => api.pairBluetooth(chosen.address, chosen.name),
                  `${chosen.name} paired and connected.`)}>
                {busy === "pair" ? "Pairing…" : "Pair and use"}
              </button>
            )}
            {status.address && (
              <button type="button" className="danger" disabled={busy !== ""}
                onClick={() => {
                  if (window.confirm(`Forget ${status.name || status.address}?`)) {
                    void run("save", () => api.forgetBluetooth(), "Speaker forgotten.");
                  }
                }}>
                Forget speaker
              </button>
            )}
          </div>
        </>
      )}
    </section>
  );
}
