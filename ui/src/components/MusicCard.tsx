import { useEffect, useState, type FormEvent } from "react";
import { api, type MusicBrowseItem, type MusicBrowseResult, type MusicConfig, type MusicStation } from "../api";
import type { Notice } from "./Toast";
import { SmartPlaylistBuilder } from "./SmartPlaylistBuilder";

export function MusicCard({ configured, notify, fail, playbackConfig }: {
  configured: boolean;
  notify: (text: string, kind?: Notice["kind"]) => void;
  fail: (error: unknown) => void;
  playbackConfig?: MusicConfig;
}) {
  const [config, setConfig] = useState<MusicConfig | null>(null);
  const [stations, setStations] = useState<MusicStation[]>([]);
  const [station, setStation] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [volume, setVolume] = useState(30);
  const [busy, setBusy] = useState(false);
  const [builder, setBuilder] = useState<"artist" | "album" | "">("");
  const [libraries, setLibraries] = useState<MusicBrowseItem[]>([]);
  const [library, setLibrary] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [results, setResults] = useState<MusicBrowseResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<MusicBrowseItem[]>([]);
  const [loadingSaved, setLoadingSaved] = useState(false);
  const [smartBuilder, setSmartBuilder] = useState(false);

  useEffect(() => {
    if (!playbackConfig) return;
    setEnabled(playbackConfig.enabled);
    setConfig(playbackConfig);
  }, [playbackConfig]);

  useEffect(() => {
    let active = true;
    api.musicConfig().then((value) => {
      if (!active) return;
      setConfig(value);
      setStation(value.station_key);
      setEnabled(value.enabled);
      setVolume(value.volume);
      if (configured && value.station_key.startsWith("mix:")) {
        setLoadingSaved(true);
        api.musicBrowse({ key: value.station_key }).then((mix) => {
          if (active) setSelected(mix.items);
        }).catch((error) => { if (active) fail(error); }).finally(() => { if (active) setLoadingSaved(false); });
      }
    }).catch((error) => { if (active) fail(error); });
    if (configured) {
      api.musicStations().then((value) => { if (active) setStations(value.items); }).catch(fail);
    } else {
      setStations([]);
    }
    return () => { active = false; };
  }, [configured, fail]);

  useEffect(() => {
    if (!configured || !builder) return;
    let active = true;
    api.musicBrowse().then((value) => {
      if (!active) return;
      setLibraries(value.items);
      setLibrary((current) => value.items.some((item) => item.id === current) ? current : value.items[0]?.id ?? "");
    }).catch(fail);
    return () => { active = false; };
  }, [configured, builder, fail]);

  useEffect(() => {
    if (!configured || !builder || !library) return;
    let active = true;
    setLoading(true);
    setResults(null);
    api.musicBrowse({ library, kind: builder, search: query, offset }).then((value) => {
      if (active) setResults(value);
    }).catch((error) => { if (active) fail(error); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [configured, builder, library, query, offset, fail]);

  useEffect(() => {
    if (!config?.enabled) return;
    const timer = window.setInterval(() => {
      api.musicConfig().then(setConfig).catch(fail);
    }, 10000);
    return () => window.clearInterval(timer);
  }, [config?.enabled, fail]);

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const saved = await api.saveMusicConfig({ enabled, station_key: station, volume });
      setConfig(saved);
      notify(saved.enabled ? "Background music enabled." : "Background music disabled.", "success");
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  }

  function startMix(kind: "artist" | "album") {
    setSmartBuilder(false);
    setBuilder(kind);
    setOffset(0);
    setSearch("");
    setQuery("");
    if (!station.startsWith(`mix:${kind}:`)) {
      setSelected([]);
      setStation("");
    }
  }

  function selectMix(items: MusicBrowseItem[]) {
    setSelected(items);
    setStation(items.length ? `mix:${builder}:${items.map((item) => item.id).join(",")}` : "");
  }

  return <form className="card wide" onSubmit={save}>
    <h2>Background music</h2>
    <p className="muted">Plex radio, music playlists and custom mixes play across scene changes,
      pause for videos with audio, and resume afterwards. Choose a source and enable music to play it.</p>
    <label>Music source
      <select value={station} disabled={!configured || busy || !config || loadingSaved} onChange={(event) => {
        setStation(event.target.value);
        setBuilder("");
        setSmartBuilder(false);
        setSelected([]);
      }}>
        <option value="">Select a station or playlist</option>
        {station.startsWith("mix:") &&
          <option value={station}>Custom {station.split(":")[1]} mix ({station.split(":")[2].split(",").length} selections)</option>}
        {station && !stations.some((item) => item.key === station) &&
          !station.startsWith("mix:") && <option value={station}>Saved music source (currently unavailable)</option>}
        {stations.map((item) => <option key={item.key} value={item.key}>
          {item.library}: {item.title}{item.smart ? " (smart playlist)" : ""}
        </option>)}
      </select>
    </label>
    <div className="actions">
      <button type="button" disabled={!configured || busy || !config || loadingSaved} onClick={() => startMix("artist")}>Build artist mix</button>
      <button type="button" disabled={!configured || busy || !config || loadingSaved} onClick={() => startMix("album")}>Build album mix</button>
      <button type="button" disabled={!configured || busy || !config || loadingSaved}
        onClick={() => { setBuilder(""); setSmartBuilder(true); }}>Create Plex smart playlist</button>
    </div>
    {smartBuilder && configured && <SmartPlaylistBuilder disabled={busy} fail={fail}
      onClose={() => setSmartBuilder(false)} onCreated={(playlist) => {
        setStations((current) => [...current.filter((item) => item.key !== playlist.key), playlist]);
        setSmartBuilder(false);
        notify("Smart playlist created in Plex. Select it as a music source to use it here.", "success");
      }} />}
    {loadingSaved && <p role="status">Loading saved mix...</p>}
    {builder && <fieldset disabled={busy}>
      <legend>Custom {builder} mix</legend>
      <p className="muted">Choose up to 20 {builder === "artist" ? "artists" : "albums"}.
        Plex shuffles their tracks and builds a fresh queue when the mix ends.</p>
      <label>Music library
        <select value={library} onChange={(event) => { setLibrary(event.target.value); setOffset(0); }}>
          {!libraries.length && <option value="">No music libraries available</option>}
          {libraries.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
        </select>
      </label>
      <label>Search {builder === "artist" ? "artists" : "albums"}
        <input value={search} maxLength={100} onChange={(event) => setSearch(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              setQuery(search.trim());
              setOffset(0);
            }
          }} />
      </label>
      <button type="button" onClick={() => { setQuery(search.trim()); setOffset(0); }}>Search music</button>
      {selected.length > 0 && <div role="group" aria-label="Selected mix items">
        {selected.map((item) => <button type="button" key={item.id}
          onClick={() => selectMix(selected.filter((current) => current.id !== item.id))}>
          Remove {item.artist ? `${item.artist} - ` : ""}{item.title}
        </button>)}
      </div>}
      {loading && <p role="status">Loading music...</p>}
      {results && <div role="group" aria-label="Mix choices">
        {results.items.map((item) => {
          const checked = selected.some((current) => current.id === item.id);
          return <label className="check" key={item.id}>
            <input type="checkbox" checked={checked} disabled={!checked && selected.length >= 20}
              onChange={() => selectMix(checked ? selected.filter((current) => current.id !== item.id) : [...selected, item])} />
            {item.artist ? `${item.artist} - ` : ""}{item.title}
          </label>;
        })}
        {!results.items.length && <p>No matching music found.</p>}
        <div className="actions">
          <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>Previous music page</button>
          <button type="button" disabled={offset + 100 >= results.total} onClick={() => setOffset(offset + 100)}>Next music page</button>
        </div>
      </div>}
      <button type="button" onClick={() => setBuilder("")}>Close mix builder</button>
    </fieldset>}
    <p className="muted">Regular and smart playlists are listed from your Plex account. Saved Plexamp mixes appear
      when Plex exposes them as playlists or stations; app-only mixes must be saved as playlists first.
      Custom mixes shuffle your selected artists/albums, not sonic recommendations.</p>
    <label><input type="checkbox" checked={enabled} disabled={busy || (!enabled && (!configured || !station))}
      onChange={(event) => setEnabled(event.target.checked)} />Enable background music</label>
    <label>Music volume
      <input aria-label="Music volume" type="range" min={0} max={100} step={1} value={volume}
        onChange={(event) => setVolume(Number(event.target.value))} />
      <span>{volume}%</span>
    </label>
    {config && <p role="status">Music: {config.status.state}{config.status.title ? ` — ${config.status.title}` : ""}</p>}
    {config?.status.error && <p role="alert">{config.status.error}</p>}
    {!configured && <p className="muted">Connect Plex above to discover music sources.</p>}
    <button className="primary" disabled={busy || !config || (enabled && (!configured || !station))}>
      Save music settings
    </button>
  </form>;
}
