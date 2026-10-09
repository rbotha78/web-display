import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api";
import type { Notice } from "./Toast";

interface Props {
  current: string | null;
  notify: (text: string, kind?: Notice["kind"]) => void;
  fail: (error: unknown) => void;
}

export function HostnameCard({ current, notify, fail }: Props) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (current) setName(current);
  }, [current]);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    const wanted = name.trim().toLowerCase();
    if (!window.confirm(`Rename this device to "${wanted}"? The management service will restart.`)) {
      return;
    }
    setBusy(true);
    try {
      const result = await api.setHostname(wanted);
      notify(
        result.changed
          ? `Renaming to ${result.hostname}. Reconnect at https://${result.hostname}.local:8443 in about a minute and accept the new certificate.`
          : "The hostname is unchanged.",
        "success",
      );
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card" onSubmit={save}>
      <h2>Hostname</h2>
      <label>
        Device hostname
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          pattern="[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?"
          maxLength={63}
          disabled={!current}
        />
      </label>
      <p className="hint">
        Letters, digits and hyphens. Also used for the .local address. A new HTTPS certificate is
        created when the name changes.
      </p>
      <div className="actions">
        <button className="primary" disabled={!current || busy}>
          Save hostname
        </button>
      </div>
    </form>
  );
}
