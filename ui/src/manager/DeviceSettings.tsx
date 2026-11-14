import { useCallback, useEffect, useState } from "react";
import { DeviceApiContext } from "../DeviceApiContext";
import { Dashboard } from "../components/Dashboard";
import type { Notice } from "../components/Toast";
import { settingsApi, type Device } from "./api";

export function DeviceSettings({ device, notify, close }: {
  device: Device;
  notify: (text: string, kind?: Notice["kind"]) => void;
  close: () => void;
}) {
  const [connection, setConnection] = useState<{
    api: ReturnType<typeof settingsApi>; lifetime: AbortController;
  } | null>(null);

  useEffect(() => {
    const lifetime = new AbortController();
    setConnection({ api: settingsApi(device.id, lifetime.signal), lifetime });
    return () => lifetime.abort();
  }, [device.id]);

  const notifyLive = useCallback((text: string, kind?: Notice["kind"]) => {
    if (!connection?.lifetime.signal.aborted) notify(text, kind);
  }, [connection, notify]);
  const expired = useCallback(() => {
    if (!connection?.lifetime.signal.aborted) {
      notify("The device rejected the manager key. Enrol this device again.", "error");
      close();
    }
  }, [connection, notify, close]);

  return <section aria-label={`Configuration for ${device.name}`} className="manager-settings">
    <header className="topbar">
      <div>
        <h2>{device.name}: configuration</h2>
        <p className="muted">Changes are saved on this display, not in the manager.</p>
      </div>
      <button type="button" onClick={() => {
        if (window.confirm("Close configuration? Any unsaved edits will be discarded.")) close();
      }}>Back to devices</button>
    </header>
    {connection ? <DeviceApiContext.Provider value={connection.api}>
      <Dashboard canManageKeys={false} notify={notifyLive} onExpired={expired} />
    </DeviceApiContext.Provider> : <p>Loading configuration...</p>}
  </section>;
}
