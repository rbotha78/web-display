import { useCallback, useEffect, useState } from "react";
import { Toast, type Notice } from "../components/Toast";
import { AddDevice } from "./AddDevice";
import { DeviceCard } from "./DeviceCard";
import { api, setToken, type Device, type Found } from "./api";

function readToken(): string {
  const match = /token=([^&]+)/.exec(window.location.hash);
  return match ? decodeURIComponent(match[1]) : "";
}

export function ManagerApp() {
  const [ready, setReady] = useState(false);
  const [devices, setDevices] = useState<Device[] | null>(null);
  const [adding, setAdding] = useState<{ host: string; port: number; name?: string } | null>(null);
  const [managerVersion, setManagerVersion] = useState("");
  const [found, setFound] = useState<Found[]>([]);
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
      const reply = await api.devices();
      setDevices(reply.devices);
      setManagerVersion(reply.manager_version ?? "");
    } catch (error) {
      notify(error instanceof Error ? error.message : "Could not load devices", "error");
    }
  }, [notify]);

  useEffect(() => {
    if (!ready) return;
    const scan = () => api.discover().then((r) => setFound(r.found ?? []), () => undefined);
    void refresh();
    void scan();
    const timer = window.setInterval(() => {
      void refresh();
      void scan();
    }, 20000);
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
            initialName={adding.name}
            done={() => {
              setAdding(null);
              void refresh();
            }}
          />
        )}
        {!adding && found.some((f) => !f.enrolled) && (
          <section className="card" aria-label="Found on your network">
            <h2>Found on your network</h2>
            {found
              .filter((f) => !f.enrolled)
              .map((f) => (
                <p key={f.host + ":" + f.port}>
                  {f.name} ({f.host}){" "}
                  <button
                    type="button"
                    onClick={() => setAdding({ host: f.host, port: f.port, name: f.name })}
                  >
                    Add
                  </button>
                </p>
              ))}
          </section>
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
      {managerVersion && <footer className="muted">Web Display Manager {managerVersion}</footer>}
      <Toast notice={notice} />
    </div>
  );
}
