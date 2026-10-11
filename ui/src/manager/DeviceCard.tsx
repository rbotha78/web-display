import { useEffect, useState } from "react";
import { api, type Device } from "./api";

interface Props {
  device: Device;
  notify: (text: string, kind?: "info" | "success" | "error") => void;
  refresh: () => void;
  readd: (device: Device) => void;
}

export function DeviceCard({ device, notify, refresh, readd }: Props) {
  const [url, setUrl] = useState(device.url ?? "");
  const [editing, setEditing] = useState(false);
  const [available, setAvailable] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!editing) setUrl(device.url ?? "");
  }, [device.url, editing]);

  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    try {
      await work();
    } catch (error) {
      notify(error instanceof Error ? error.message : "Request failed", "error");
    } finally {
      setBusy(false);
    }
  };

  const saveUrl = () =>
    run(async () => {
      await api.setUrl(device.id, url.trim());
      setEditing(false);
      notify("Display address saved on " + device.name, "success");
      refresh();
    });

  const check = () =>
    run(async () => {
      const result = await api.checkUpdate(device.id);
      setAvailable(result.available && result.latest ? result.latest.version : null);
      notify(
        result.available && result.latest
          ? device.name + ": version " + result.latest.version + " is available"
          : device.name + " is up to date",
      );
    });

  const install = () =>
    run(async () => {
      const result = await api.installUpdate(device.id);
      setAvailable(null);
      notify(device.name + ": " + result.version + " installed; restart to apply it", "success");
      refresh();
    });

  const reboot = () => {
    if (!window.confirm("Restart " + device.name + " now?")) return;
    return run(async () => {
      await api.reboot(device.id);
      notify(device.name + " is restarting", "success");
    });
  };

  const forget = () => {
    if (
      !window.confirm(
        "Remove " + device.name + " from this manager? Also revoke the manager's key on the device.",
      )
    ) {
      return;
    }
    return run(async () => {
      await api.forget(device.id);
      refresh();
    });
  };

  const connection =
    device.default_route === undefined
      ? "unknown"
      : device.wifi_connected && device.default_route.startsWith("wl")
        ? "Wi-Fi"
        : device.default_route || "none";
  const healthy = device.online && !device.error;

  return (
    <section className="card" aria-label={device.name}>
      <h2>
        {device.name}{" "}
        <span className={"pill " + (device.online ? "ok" : "bad")}>
          {device.online ? "Online" : "Offline"}
        </span>
      </h2>
      <p className="muted">
        {device.addresses && device.addresses.length > 1
          ? device.addresses.join(", ")
          : device.host}
        :{device.port}
        {device.hostname ? " · " + device.hostname : ""}
      </p>
      {device.certificate_changed && (
        <p className="error">
          The certificate changed (for example after a rename or reflash).{" "}
          <button type="button" onClick={() => readd(device)}>
            Trust again
          </button>
        </p>
      )}
      {device.key_rejected && (
        <p className="error">
          The device rejected this manager's key.{" "}
          <button type="button" onClick={() => readd(device)}>
            Enrol again
          </button>
        </p>
      )}
      {device.error && !device.certificate_changed && !device.key_rejected && (
        <p className="error">{device.error}</p>
      )}
      {healthy && (
        <dl>
          <dt>Version</dt>
          <dd>
            {device.version}
            {device.restart_needed
              ? " (" + device.installed_version + " installed, restart to apply)"
              : ""}
          </dd>
          <dt>Connection</dt>
          <dd>{connection}</dd>
          <dt>Internet</dt>
          <dd>
            <span className={"pill " + (device.internet ? "ok" : "bad")}>
              {device.internet ? "Online" : "Offline"}
            </span>
          </dd>
        </dl>
      )}
      {healthy && (
        <>
          <label>
            Display address
            <input
              value={url}
              onChange={(event) => {
                setUrl(event.target.value);
                setEditing(true);
              }}
              placeholder="https://example.com"
            />
          </label>
          <div className="actions">
            <button type="button" disabled={busy || !editing} onClick={saveUrl}>
              Save address
            </button>
            <button type="button" disabled={busy} onClick={check}>
              Check for updates
            </button>
            {available && (
              <button type="button" className="primary" disabled={busy} onClick={install}>
                Install {available}
              </button>
            )}
            <button type="button" disabled={busy} onClick={reboot}>
              Restart
            </button>
          </div>
        </>
      )}
      <div className="actions">
        <button type="button" disabled={busy} onClick={forget}>
          Remove
        </button>
      </div>
    </section>
  );
}
