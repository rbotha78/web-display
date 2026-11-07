export function WeatherIcon({ code }: { code?: string }) {
  if (!code || !/^(01|02|03|04|09|10|11|13|50)[dn]$/.test(code)) return null;
  const condition = code.slice(0, 2);
  const night = code.endsWith("n");
  return (
    <svg className="weather-icon" viewBox="0 0 64 64" aria-hidden="true" focusable="false">
      {(condition === "01" || condition === "02") && (
        night ? <path className="weather-icon-moon" d="M36 8a20 20 0 1 0 20 27A19 19 0 0 1 36 8Z" /> :
          <g className="weather-icon-sun">
            <circle cx="28" cy="25" r="12" />
            <path d="M28 3v5m0 34v5M6 25h5m34 0h5M12 9l4 4m24 24 4 4M12 41l4-4m24-24 4-4" />
          </g>
      )}
      {condition !== "01" && condition !== "50" && (
        <path className="weather-icon-cloud" d="M15 43a9 9 0 0 1-1-18 14 14 0 0 1 26-5 11 11 0 1 1 9 23Z" />
      )}
      {(condition === "09" || condition === "10") && (
        <path className="weather-icon-rain" d="m20 49-3 7m15-7-3 7m15-7-3 7" />
      )}
      {condition === "11" && (
        <path className="weather-icon-lightning" d="m34 39-12 14h9l-4 9 16-16h-10l6-7Z" />
      )}
      {condition === "13" && (
        <path className="weather-icon-snow" d="M22 48v12m-5-9 10 6m-10 0 10-6M43 48v12m-5-9 10 6m-10 0 10-6" />
      )}
      {condition === "50" && (
        <path className="weather-icon-mist" d="M8 20h48M14 30h36M8 40h48M14 50h36" />
      )}
    </svg>
  );
}
