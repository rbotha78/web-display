import { useCallback, useEffect, useState } from "react";
import { Toast, type Notice } from "../components/Toast";
import { AddDevice } from "./AddDevice";
import { DeviceCard } from "./DeviceCard";
import { MusicSyncGroups } from "./MusicSyncGroups";
import { DeviceSettings } from "./DeviceSettings";
import { GroupPlayer } from "./GroupPlayer";
import { api, setToken, type Device, type Found } from "./api";

function readToken(): string {
  const match = /token=([^&]+)/.exec(window.location.hash);
  return match ? decodeURIComponent(match[1]) : "";
}

function normalizedHostname(value: string) {
  return value.trim().replace(/\.$/, "").toLowerCase().replace(/\.local$/, "");
}

export function ManagerApp() {
  const [ready, setReady] = useState(false);
  const [devices, setDevices] = useState<Device[] | null>(null);
  const [adding, setAdding] = useState<{ host: string; port: number; name?: string } | null>(null);
  const [managerVersion, setManagerVersion] = useState("");
  const [found, setFound] = useState<Found[]>([]);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);
  const [syncGroups, setSyncGroups] = useState(false);
  const [configuring, setConfiguring] = useState<Device | null>(null);

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

  const closeSettings = useCallback(() => {
    setConfiguring(null);
    void refresh();
  }, [refresh]);

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

  const leaders = devices?.filter((device) => device.online && device.music_sync?.role === "leader") ?? [];

  const unpaired = found.filter(item => !item.enrolled && !devices?.some(device =>
    device.port === item.port && [device.host, ...(device.addresses ?? []), device.hostname]
      .some(host => host && [item.host, item.name].some(value =>
        normalizedHostname(host) === normalizedHostname(value))),
  ));

  return (
    <div className={`shell manager-shell${configuring ? " admin-shell" : ""}`}>
      <header className="topbar">
        <h1>Web Display Manager</h1>
        {!configuring && <div className="actions">
          <button type="button" onClick={() => void refresh()}>
            Refresh
          </button>
          <details>
            <summary>Manager actions</summary>
            <button type="button" onClick={() => setSyncGroups(true)}>Music sync groups</button>
            <button type="button" disabled={busy || !devices?.length} onClick={updateAll}>
              Update all
            </button>
          </details>
          <button
            type="button"
            className="primary"
            onClick={() => setAdding({ host: "", port: 8443 })}
          >
            Add device
          </button>
        </div>}
      </header>
      {syncGroups && !configuring && <MusicSyncGroups devices={devices ?? []} refresh={refresh}
        close={() => setSyncGroups(false)} notify={notify} />}
      {configuring ? <DeviceSettings key={configuring.id} device={configuring} notify={notify}
        close={closeSettings} /> : <div className="grid">
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
        {!adding && unpaired.length > 0 && (
          <section className="card" aria-label="Found on your network">
            <h2>Found on your network</h2>
            {unpaired
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
            configure={() => setConfiguring(device)}
          />
        ))}
        {devices && devices.length === 0 && !adding && (
          <p className="muted">No devices yet. Choose Add device to find or add one.</p>
        )}
      </div>}
      {!configuring && leaders.length > 0 && <GroupPlayer leaders={leaders} notify={notify} />}
      {managerVersion && <footer className="muted">Web Display Manager {managerVersion}</footer>}
      <Toast notice={notice} />
    </div>
  );
}
