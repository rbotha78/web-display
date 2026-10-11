import { useState, type FormEvent } from "react";
import { api, type Found } from "./api";

interface Props {
  notify: (text: string, kind?: "info" | "success" | "error") => void;
  done: () => void;
  initialHost?: string;
  initialPort?: number;
  initialName?: string;
}

export function AddDevice({ notify, done, initialHost = "", initialPort = 8443, initialName = "" }: Props) {
  const [host, setHost] = useState(initialHost);
  const [port, setPort] = useState(String(initialPort));
  const [fingerprint, setFingerprint] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [name, setName] = useState(initialName);
  const [found, setFound] = useState<Found[] | null>(null);
  const [busy, setBusy] = useState(false);

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

  const scan = () =>
    run(async () => {
      setFound((await api.discover()).found);
    });

  const pick = (item: Found) => {
    setHost(item.host);
    setPort(String(item.port));
    setName(item.name);
    setFingerprint(null);
  };

  const lookup = (event: FormEvent) => {
    event.preventDefault();
    return run(async () => {
      const result = await api.fingerprint(host.trim(), Number(port));
      setFingerprint(result.fingerprint);
    });
  };

  const enroll = (event: FormEvent) => {
    event.preventDefault();
    if (!fingerprint) return;
    return run(async () => {
      await api.enroll({
        host: host.trim(),
        port: Number(port),
        fingerprint,
        password,
        name: name.trim() || host.trim(),
      });
      setPassword("");
      notify("Device added", "success");
      done();
    });
  };

  return (
    <section className="card wide" aria-label="Add device">
      <h2>Add a device</h2>
      <div className="actions">
        <button type="button" disabled={busy} onClick={scan}>
          Scan the network
        </button>
      </div>
      {found && found.length === 0 && (
        <p className="muted">No devices found. Add one by address below.</p>
      )}
      {found?.map((item) => (
        <p key={item.host + ":" + item.port}>
          {item.name} ({item.host}){" "}
          {item.enrolled ? (
            <span className="muted">already added</span>
          ) : (
            <button type="button" onClick={() => pick(item)}>
              Use {item.name}
            </button>
          )}
        </p>
      ))}
      <form onSubmit={lookup}>
        <label>
          Address
          <input
            value={host}
            onChange={(event) => {
              setHost(event.target.value);
              setFingerprint(null);
            }}
            placeholder="web-display.local or 192.168.0.20"
            required
          />
        </label>
        <label>
          Port
          <input
            value={port}
            onChange={(event) => {
              setPort(event.target.value);
              setFingerprint(null);
            }}
            inputMode="numeric"
          />
        </label>
        <div className="actions">
          <button disabled={busy || !host.trim()}>Look up certificate</button>
        </div>
      </form>
      {fingerprint && (
        <form onSubmit={enroll}>
          <p>
            Compare this with the TLS certificate SHA-256 shown on the device's screen. Only
            continue if they match.
          </p>
          <p>
            <code>{fingerprint}</code>
          </p>
          <label>
            Name
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder={host}
              maxLength={40}
            />
          </label>
          <label>
            Device administrator password
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="off"
              required
            />
          </label>
          <p className="muted">
            The password is used once to create a key for this PC and is not stored.
          </p>
          <div className="actions">
            <button className="primary" disabled={busy || !password}>
              Trust and add
            </button>
          </div>
        </form>
      )}
      <div className="actions">
        <button type="button" onClick={done}>
          Close
        </button>
      </div>
    </section>
  );
}
