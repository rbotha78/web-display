import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, type SshKey } from "../api";
import type { Notice } from "./Toast";

interface Props {
  notify: (text: string, kind?: Notice["kind"]) => void;
  fail: (error: unknown) => void;
}

export function DebugKeysCard({ notify, fail }: Props) {
  const [keys, setKeys] = useState<SshKey[] | null>(null);
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const result = await api.debugKeys();
      setKeys(result.enabled ? result.keys : null);
    } catch (error) {
      if (error instanceof Error && "status" in error && error.status === 401) fail(error);
    }
  }, [fail]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const add = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      setKeys((await api.addDebugKey(key.trim(), name.trim())).keys);
      setKey("");
      setName("");
      notify("SSH key added", "success");
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (item: SshKey) => {
    const warning = keys?.length === 1 ? " This is the last key, so nobody will be able to log in over SSH." : "";
    if (!window.confirm(`Remove SSH key "${item.comment || item.fingerprint}"?${warning}`)) return;
    setBusy(true);
    try {
      setKeys((await api.removeDebugKey(item.id)).keys);
      notify("SSH key removed", "success");
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  };

  if (keys === null) return null;
  return (
    <form className="card" aria-label="Development SSH keys" onSubmit={add}>
      <h2>Development SSH keys</h2>
      <p className="hint">
        Public keys allowed to log in as <code>webdebug</code> with full root access. This
        appears only on development firmware.
      </p>
      {keys.length === 0 ? (
        <p className="hint">No SSH keys are authorised.</p>
      ) : (
        keys.map((item) => (
          <p key={item.id}>
            {item.comment || "(no name)"} <span className="hint">{item.type} {item.fingerprint}</span>{" "}
            <button type="button" disabled={busy} onClick={() => void remove(item)}>
              Remove
            </button>
          </p>
        ))
      )}
      <label>
        Public key
        <textarea
          value={key}
          onChange={(event) => setKey(event.target.value)}
          placeholder="ssh-ed25519 AAAA… name"
          spellCheck={false}
          required
        />
      </label>
      <label>
        Name (optional)
        <input value={name} onChange={(event) => setName(event.target.value)} maxLength={40} />
      </label>
      <div className="actions">
        <button className="primary" disabled={busy || !key.trim()}>
          Add key
        </button>
      </div>
    </form>
  );
}
