import { useCallback, useEffect, useState } from "react";
import { Toast, type Notice } from "../components/Toast";
import { AddDevice } from "./AddDevice";
import { DeviceCard } from "./DeviceCard";
import { api, setToken, type Device } from "./api";

function readToken(): string {
  const match = /token=([^&]+)/.exec(window.location.hash);
  return match ? decodeURIComponent(match[1]) : "";
}

export function ManagerApp() {
  const [ready, setReady] = useState(false);
  const [devices, setDevices] = useState<Device[] | null>(null);
  const [adding, setAdding] = useState<{ host: string; port: number } | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);

  const notify = useCallback((text: string, kind: Notice["kind"] = "info") => {
    setNotice({ text, kind, id: Date.now() });
  }, []);

  useEffect(() => {
    setToken(readToken());
    setReady(true);
  }, []);

  const refresh = useCallback(async () => {
    try {
      setDevices((await api.devices()).devices);
    } catch (error) {
      notify(error instanceof Error ? error.message : "Could not load devices", "error");
    }
  }, [notify]);

  useEffect(() => {
    if (!ready) return;
    void refresh();
    const timer = window.setInterval(() => void refresh(), 20000);
    return () => window.clearInterval(timer);
  }, [ready, refresh]);

  const updateAll = async () => {
    if (!window.confirm("Install the latest update on every device that has one?")) return;
    setBusy(true);
    try {
      const { results } = await api.updateAll();
      const installed = results.filter((r) => r.outcome === "installed").length;
      const failed = results.filter((r) => r.outcome === "failed");
      notify(
        failed.length
          ? installed +
              " updated, " +
              failed.length +
              " failed: " +
              failed.map((r) => r.name + " (" + r.error + ")").join("; ")
          : installed + " updated, " + (results.length - installed) + " already up to date",
        failed.length ? "error" : "success",
      );
      await refresh();
    } catch (error) {
      notify(error instanceof Error ? error.message : "Update failed", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="shell">
      <header className="topbar">
        <h1>Web Display Manager</h1>
        <div className="actions">
          <button type="button" onClick={() => void refresh()}>
            Refresh
          </button>
          <button type="button" disabled={busy || !devices?.length} onClick={updateAll}>
            Update all
          </button>
          <button
            type="button"
            className="primary"
            onClick={() => setAdding({ host: "", port: 8443 })}
          >
            Add device
          </button>
        </div>
      </header>
      <div className="grid">
        {adding && (
          <AddDevice
            notify={notify}
            initialHost={adding.host}
            initialPort={adding.port}
            done={() => {
              setAdding(null);
              void refresh();
            }}
          />
        )}
        {devices?.map((device) => (
          <DeviceCard
            key={device.id}
            device={device}
            notify={notify}
            refresh={() => void refresh()}
            readd={(d) => setAdding({ host: d.host, port: d.port })}
          />
        ))}
        {devices && devices.length === 0 && !adding && (
          <p className="muted">No devices yet. Choose Add device to find or add one.</p>
        )}
      </div>
      <Toast notice={notice} />
    </div>
  );
}
