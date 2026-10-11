import { useCallback, useEffect, useState } from "react";
import { api, type ApiKey } from "../api";
import type { Notice } from "./Toast";

interface Props {
  notify: (text: string, kind?: Notice["kind"]) => void;
  fail: (error: unknown) => void;
}

function created(seconds: number | null): string {
  return seconds ? new Date(seconds * 1000).toLocaleString() : "unknown date";
}

export function ManagerKeysCard({ notify, fail }: Props) {
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setKeys((await api.apiKeys()).keys);
    } catch (error) {
      if (error instanceof Error && "status" in error && error.status === 401) fail(error);
    }
  }, [fail]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const revoke = async (key: ApiKey) => {
    if (
      !window.confirm(
        `Revoke "${key.name}" (${created(key.created)})? The manager that uses it will lose access until it is enrolled again.`,
      )
    ) {
      return;
    }
    setBusy(true);
    try {
      await api.revokeApiKey(key.id);
      notify("Access revoked", "success");
      await refresh();
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  };

  if (keys === null) return null;
  return (
    <section className="card" aria-label="Manager access">
      <h2>Manager access</h2>
      {keys.length === 0 ? (
        <p className="hint">No managers are enrolled on this device.</p>
      ) : (
        <>
          <p className="hint">
            Each key lets a Web Display Manager control this device without the password.
          </p>
          {keys.map((key) => (
            <p key={key.id}>
              {key.name} <span className="hint">added {created(key.created)}</span>{" "}
              <button type="button" disabled={busy} onClick={() => void revoke(key)}>
                Revoke
              </button>
            </p>
          ))}
        </>
      )}
    </section>
  );
}
