import { useEffect, useState } from "react";

interface WeatherData {
  location?: string;
  temperature?: number;
  feels_like?: number;
  description?: string;
  humidity?: number;
  wind_kmh?: number;
  observed_at?: number;
  forecast?: { date: string; low: number; high: number; description: string }[];
  forecast_error?: string;
  error?: string;
  stale?: boolean;
}

export function WeatherDisplay({ compact = false, currentOnly = false }: {
  compact?: boolean;
  currentOnly?: boolean;
}) {
  const [weather, setWeather] = useState<WeatherData | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    const poll = async () => {
      try {
        const response = await fetch("/api/weather", { signal: controller.signal });
        if (!response.ok) throw new Error("Could not load weather information");
        const data: WeatherData = await response.json();
        if (!controller.signal.aborted) {
          setWeather(data);
          setError("");
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setError(error instanceof Error ? error.message : "Could not load weather information");
        }
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(poll, 15000);
      }
    };
    void poll();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, []);

  return (
    <section className={`weather-display${compact ? " weather-widget" : ""}${currentOnly ? " weather-summary" : ""}`} aria-label="Weather">
      <h2>{weather?.location || "Weather"}</h2>
      {weather?.stale && <p role="status">Weather data may be out of date.</p>}
      {(error || weather?.error) && <p role="status">{error || weather?.error}</p>}
      {!weather && !error && <p>Waiting for weather information</p>}
      {weather?.temperature !== undefined && (
        <>
          <div className="weather-current">
            <strong>{Math.round(weather.temperature)} °C</strong>
            <span>{weather.description}</span>
          </div>
          <p className="weather-measurements">
            Feels like {Math.round(weather.feels_like ?? weather.temperature)} °C
            {" · "}Humidity {weather.humidity}%{" · "}Wind {weather.wind_kmh} km/h
          </p>
          {!currentOnly && !!weather.forecast?.length && (
            <div className="weather-forecast">
              {weather.forecast.map((day) => (
                <div key={day.date}>
                  <h3>{new Date(`${day.date}T12:00:00`).toLocaleDateString([], {
                    weekday: "short", day: "numeric", month: "short",
                  })}</h3>
                  <p>{Math.round(day.low)}–{Math.round(day.high)} °C</p>
                  <p>{day.description}</p>
                </div>
              ))}
            </div>
          )}
          {!currentOnly && weather.forecast_error && <p role="status">{weather.forecast_error}</p>}
          <footer>
            OpenWeather{!currentOnly && " · Forecast ranges from 3-hour samples"}
            {weather.observed_at && ` · Observed ${new Date(weather.observed_at * 1000).toLocaleTimeString()}`}
          </footer>
        </>
      )}
    </section>
  );
}
