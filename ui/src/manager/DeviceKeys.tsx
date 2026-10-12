import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, type SshKey } from "./api";

interface Props {
  deviceId: string;
  deviceName: string;
  notify: (text: string, kind?: "info" | "success" | "error") => void;
}

export function DeviceKeys({ deviceId, deviceName, notify }: Props) {
  const [keys, setKeys] = useState<SshKey[] | null>(null);
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const result = await api.debugKeys(deviceId);
      setKeys(result.enabled ? result.keys : null);
    } catch {
      // Release firmware, or firmware without SSH key support, simply has no keys to show.
      setKeys(null);
    }
  }, [deviceId]);

  useEffect(() => {
    void load();
  }, [load]);

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

  const add = (event: FormEvent) => {
    event.preventDefault();
    return run(async () => {
      setKeys((await api.addDebugKey(deviceId, key.trim(), name.trim())).keys);
      setKey("");
      setName("");
      notify("SSH key added to " + deviceName, "success");
    });
  };

  const remove = (item: SshKey) => {
    const warning =
      keys?.length === 1 ? " This is the last key, so nobody will be able to log in over SSH." : "";
    if (!window.confirm(`Remove SSH key "${item.comment || item.fingerprint}" from ${deviceName}?${warning}`)) {
      return;
    }
    return run(async () => {
      setKeys((await api.removeDebugKey(deviceId, item.id)).keys);
      notify("SSH key removed from " + deviceName, "success");
    });
  };

  if (keys === null) return null;
  return (
    <details>
      <summary>Development SSH keys ({keys.length})</summary>
      <p className="hint">
        Public keys allowed to log in as <code>webdebug</code> with full root access.
      </p>
      {keys.length === 0 ? (
        <p className="hint">No SSH keys are authorised.</p>
      ) : (
        keys.map((item) => (
          <p key={item.id}>
            {item.comment || "(no name)"}{" "}
            <span className="hint">
              {item.type} {item.fingerprint}
            </span>{" "}
            <button type="button" disabled={busy} onClick={() => void remove(item)}>
              Remove
            </button>
          </p>
        ))
      )}
      <form onSubmit={add} aria-label={"Add SSH key to " + deviceName}>
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
    </details>
  );
}
