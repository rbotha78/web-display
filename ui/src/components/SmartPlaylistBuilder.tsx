import { useEffect, useState } from "react";
import { api, type MusicBrowseItem, type MusicFilters, type MusicStation, type SmartPlaylistCriteria } from "../api";

export function SmartPlaylistBuilder({ disabled, onCreated, onClose, fail }: {
  disabled: boolean;
  onCreated: (playlist: MusicStation) => void;
  onClose: () => void;
  fail: (error: unknown) => void;
}) {
  const [libraries, setLibraries] = useState<MusicBrowseItem[]>([]);
  const [criteria, setCriteria] = useState<SmartPlaylistCriteria>({ library: "", decade: "", genre: "", mood: "" });
  const [filters, setFilters] = useState<MusicFilters | null>(null);
  const [title, setTitle] = useState("");
  const [loading, setLoading] = useState(true);
  const [filtersLoading, setFiltersLoading] = useState(false);
  const [filterAttempt, setFilterAttempt] = useState(0);
  const [working, setWorking] = useState(false);
  const [count, setCount] = useState<number | null>(null);

  useEffect(() => {
    let active = true;
    api.musicBrowse().then((result) => {
      if (!active) return;
      setLibraries(result.items);
      setCriteria({ library: result.items[0]?.id ?? "", decade: "", genre: "", mood: "" });
    }).catch((error) => { if (active) fail(error); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [fail]);

  useEffect(() => {
    if (!criteria.library) return;
    let active = true;
    setFilters(null);
    setFiltersLoading(true);
    api.musicFilters(criteria.library).then((result) => {
      if (active) setFilters(result);
    }).catch((error) => { if (active) fail(error); }).finally(() => { if (active) setFiltersLoading(false); });
    return () => { active = false; };
  }, [criteria.library, filterAttempt, fail]);

  function change(field: keyof SmartPlaylistCriteria, value: string) {
    setCount(null);
    if (field === "library") {
      setFilters(null);
      setCriteria({ library: value, decade: "", genre: "", mood: "" });
    } else {
      setCriteria((current) => ({ ...current, [field]: value }));
    }
  }

  async function submit(create: boolean) {
    setWorking(true);
    try {
      if (create) {
        const playlist = await api.createSmartPlaylist({ ...criteria, title: title.trim() });
        onCreated(playlist);
      } else {
        setCount((await api.previewSmartPlaylist(criteria)).count);
      }
    } catch (error) {
      fail(error);
    } finally {
      setWorking(false);
    }
  }

  const valid = !!filters && !!criteria.library && !!(criteria.decade || criteria.genre || criteria.mood);
  return <fieldset disabled={disabled || working} onKeyDown={(event) => {
    if (event.key === "Enter" && event.target instanceof HTMLInputElement) event.preventDefault();
  }}>
    <legend>Create Plex smart playlist</legend>
    <p className="muted">Saved in the connected Plex account and available to other Plex clients.
      Tracks must match all selected criteria. Plex updates the playlist as library metadata changes,
      with a shuffled order. Creating a playlist does not change this display's playback.</p>
    <label>Playlist name
      <input value={title} maxLength={100} onChange={(event) => setTitle(event.target.value)} />
    </label>
    <label>Smart playlist library
      <select value={criteria.library} disabled={loading} onChange={(event) => change("library", event.target.value)}>
        {!libraries.length && <option value="">No music libraries available</option>}
        {libraries.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
      </select>
    </label>
    {loading && <p role="status">Loading music libraries...</p>}
    {filtersLoading && <p role="status">Loading playlist filters...</p>}
    {criteria.library && !filters && !filtersLoading &&
      <button type="button" onClick={() => setFilterAttempt((current) => current + 1)}>Retry playlist filters</button>}
    {(["decade", "genre", "mood"] as const).map((field) => <label key={field}>
      {{ decade: "Album decade", genre: "Track genre", mood: "Track mood" }[field]}
      <select value={criteria[field]} disabled={!filters} onChange={(event) => change(field, event.target.value)}>
        <option value="">Any {field}</option>
        {filters?.[field].map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
      </select>
    </label>)}
    <p className="muted">Choices come from your library's Plex metadata. Decade uses the album release year;
      genre and mood use track tags. Missing metadata cannot match a selected filter.</p>
    {filters && !filters.mood.length && <p>No track moods are available in this library.</p>}
    {count !== null && <p role="status">{count} tracks match all selected criteria.</p>}
    {working && <p role="status">Contacting Plex...</p>}
    <div className="actions">
      <button type="button" disabled={!valid} onClick={() => void submit(false)}>Preview matching tracks</button>
      <button type="button" disabled={!valid || !title.trim() || count === 0}
        onClick={() => void submit(true)}>Create playlist in Plex</button>
      <button type="button" onClick={onClose}>Close smart playlist builder</button>
    </div>
  </fieldset>;
}
