import { useEffect, useState, type FormEvent } from "react";
import { api, type WeatherConfig } from "../api";
import type { Notice } from "./Toast";

interface Props {
  notify: (text: string, kind?: Notice["kind"]) => void;
  fail: (error: unknown) => void;
}

export function WeatherCard({ notify, fail }: Props) {
  const [config, setConfig] = useState<WeatherConfig | null>(null);
  const [city, setCity] = useState("St Albans");
  const [country, setCountry] = useState("GB");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.weatherConfig().then((saved) => {
      setConfig(saved);
      setCity(saved.city);
      setCountry(saved.country);
    }).catch(fail);
  }, [fail]);

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      setConfig(await api.saveWeatherConfig({ city, country, api_key: key }));
      setKey("");
      notify("Weather settings saved on this device.", "success");
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  }

  async function clear() {
    if (!window.confirm("Remove the saved OpenWeather API key?")) return;
    setBusy(true);
    try {
      setConfig(await api.saveWeatherConfig({ clear_key: true }));
      setKey("");
      notify("OpenWeather API key removed.", "success");
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card wide" onSubmit={save}>
      <h2>OpenWeather</h2>
      <p className="muted">
        Current conditions and a five-day forecast in Celsius, refreshed every 10 minutes.
        The API key stays private on this device. Add a weather scene or enable weather
        in a widget dashboard to display it.
      </p>
      <label>City<input required maxLength={100} value={city}
        onChange={(event) => setCity(event.target.value)} /></label>
      <label>Country code<input required maxLength={2} pattern="[A-Za-z]{2}" value={country}
        onChange={(event) => setCountry(event.target.value.toUpperCase())} /></label>
      <label>OpenWeather API key<input type="password" autoComplete="new-password" maxLength={512}
        required={!config?.configured} value={key}
        placeholder={config?.configured ? "Leave blank to keep saved key" : "Enter API key"}
        onChange={(event) => setKey(event.target.value)} /></label>
      <div className="actions">
        <button className="primary" disabled={busy || !config}>Save weather settings</button>
        {config?.configured && (
          <button type="button" className="danger" disabled={busy} onClick={() => void clear()}>
            Remove OpenWeather key
          </button>
        )}
        {config && <span role="status">OpenWeather {config.configured ? "configured" : "not configured"}</span>}
      </div>
    </form>
  );
}
