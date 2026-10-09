import { useEffect, useState, type FormEvent } from "react";
import { api, type Network, type NetworkStatus } from "../api";
import type { Notice } from "./Toast";

interface Props {
  network: NetworkStatus | null;
  notify: (text: string, kind?: Notice["kind"]) => void;
  fail: (error: unknown) => void;
  refresh: () => Promise<void>;
}

export function WifiCard({ network, notify, fail, refresh }: Props) {
  const [country, setCountry] = useState("");
  const [networks, setNetworks] = useState<Network[]>([]);
  const [ssid, setSsid] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const wifi = network?.wifi;

  useEffect(() => {
    if (wifi?.country && wifi.country !== "00") setCountry((current) => current || wifi.country!);
  }, [wifi?.country]);

  const run = async (work: () => Promise<unknown>, done: string) => {
    setBusy(true);
    try {
      await work();
      notify(done, "success");
      await refresh();
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  };

  const scan = async () => {
    setBusy(true);
    try {
      const result = await api.scan();
      setNetworks(result.networks);
      setSsid(result.networks[0]?.ssid ?? "");
      notify(`${result.networks.length} networks found.`, "info");
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  };

  const connect = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      await api.connect(ssid, password);
      setPassword("");
    }, "Connected.");
  };

  const needsCountry = !wifi?.country || wifi.country === "00";

  return (
    <section className="card wide">
      <h2>Wi-Fi</h2>
      <p className="muted">
        Ethernet is preferred whenever it has a route. Wi-Fi needs a country code before it can be
        used.
      </p>
      {wifi?.error && <p className="error">{wifi.error}</p>}
      <div className="row">
        <label className="grow">
          Country code
          <input
            value={country}
            onChange={(event) => setCountry(event.target.value.toUpperCase())}
            maxLength={2}
            placeholder="GB"
          />
        </label>
        <button
          type="button"
          disabled={busy || country.length !== 2}
          onClick={() => void run(() => api.setCountry(country), "Country set.")}
        >
          Set country
        </button>
      </div>
      {needsCountry && <p className="warn">No country is set, so Wi-Fi is disabled.</p>}
      <form onSubmit={connect}>
        <div className="row">
          <button type="button" disabled={busy || needsCountry} onClick={scan}>
            Scan networks
          </button>
          <span className="muted">
            {wifi?.saved_network ? "A network is saved." : "No saved network."}
          </span>
        </div>
        <label>
          Network
          <select value={ssid} onChange={(event) => setSsid(event.target.value)}>
            {networks.length === 0 && <option value="">Scan to list networks</option>}
            {networks.map((entry) => (
              <option key={entry.ssid} value={entry.ssid}>
                {entry.ssid} ({entry.signal}%{entry.secured ? ", secured" : ""})
              </option>
            ))}
          </select>
        </label>
        <label>
          Wi-Fi password
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="off"
          />
        </label>
        <div className="actions">
          <button className="primary" disabled={busy || !ssid}>
            Connect
          </button>
          <button
            type="button"
            className="danger"
            disabled={busy || !wifi?.saved_network}
            onClick={() => void run(() => api.forget(), "Saved network removed.")}
          >
            Forget saved network
          </button>
        </div>
      </form>
      {wifi?.devices && wifi.devices.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Interface</th>
              <th>Type</th>
              <th>State</th>
              <th>Connection</th>
            </tr>
          </thead>
          <tbody>
            {wifi.devices.map((device) => (
              <tr key={device.device}>
                <td>{device.device}</td>
                <td>{device.type}</td>
                <td>{device.state}</td>
                <td>{device.connection || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
