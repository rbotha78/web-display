import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, type UpdateCheck, type UpdateStatus } from "../api";
import type { Notice } from "./Toast";

interface Props {
  notify: (text: string, kind?: Notice["kind"]) => void;
  fail: (error: unknown) => void;
}

function megabytes(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function UpdatesCard({ notify, fail }: Props) {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [check, setCheck] = useState<UpdateCheck | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setStatus(await api.updateStatus());
      setUnavailable(false);
    } catch (error) {
      setUnavailable(true);
      if (error instanceof Error && "status" in error && error.status === 401) fail(error);
    }
  }, [fail]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    try {
      await work();
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  };

  const installed = (version: string) => {
    notify(`Update ${version} installed. Restart the device to apply it.`, "success");
    setCheck(null);
    setFile(null);
    return refresh();
  };

  const checkForUpdate = () =>
    run(async () => {
      setCheck(await api.checkUpdate());
    });

  const installLatest = () => {
    if (!window.confirm(`Download and install version ${check?.latest?.version}?`)) return;
    return run(async () => installed((await api.installUpdate()).version));
  };

  const upload = (event: FormEvent) => {
    event.preventDefault();
    if (!file) return;
    return run(async () => installed((await api.uploadUpdate(file)).version));
  };

  const restart = () => {
    if (!window.confirm("Restart the device now?")) return;
    return run(async () => {
      await api.reboot();
      notify("Restarting. Reconnect in about a minute.", "success");
    });
  };

  const needsRestart = status !== null && status.installed_version !== status.running_version;

  return (
    <section className="card">
      <h2>Updates</h2>
      {unavailable && <p className="hint">Update information is not available.</p>}
      {status && (
        <dl>
          <dt>Firmware</dt>
          <dd>{status.base_version}</dd>
          <dt>Application</dt>
          <dd>{status.running_version}</dd>
          {status.previous_version && (
            <>
              <dt>Previous</dt>
              <dd>{status.previous_version}</dd>
            </>
          )}
        </dl>
      )}
      {needsRestart && (
        <p className="hint">
          Version {status.installed_version} is installed and starts after a restart. If it does
          not start correctly, the device reverts to the previous version automatically.
        </p>
      )}
      {status?.pending && !needsRestart && (
        <p className="hint">Version {status.pending.version} is being checked after startup.</p>
      )}
      {status && status.failed.length > 0 && (
        <p className="hint">Rejected after failing on this device: {status.failed.join(", ")}.</p>
      )}
      {check && (
        <p className="hint">
          {check.available && check.latest
            ? `Version ${check.latest.version} is available (${megabytes(check.latest.size)}).`
            : "No newer update is available."}
        </p>
      )}
      <div className="actions">
        <button type="button" disabled={busy} onClick={checkForUpdate}>
          Check for updates
        </button>
        {check?.available && (
          <button type="button" className="primary" disabled={busy} onClick={installLatest}>
            Install update
          </button>
        )}
        {needsRestart && (
          <button type="button" className="primary" disabled={busy} onClick={restart}>
            Restart now
          </button>
        )}
      </div>
      <form onSubmit={upload}>
        <label>
          Signed update file
          <input
            type="file"
            accept=".wdupdate"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
        </label>
        <p className="hint">
          Only updates signed by the release key are accepted; the device verifies every file.
        </p>
        <div className="actions">
          <button disabled={!file || busy}>Upload and install</button>
        </div>
      </form>
    </section>
  );
}
