import { useEffect, useState, type FormEvent } from "react";
import { api, type PlaybackStatus, type PlexBrowseItem, type Playlist, type Scene } from "../api";
import type { Notice } from "./Toast";

interface Props {
  notify: (text: string, kind?: Notice["kind"]) => void;
  fail: (error: unknown) => void;
}

export function PlaylistCard({ notify, fail }: Props) {
  const [playlist, setPlaylist] = useState<Playlist | null>(null);
  const [busy, setBusy] = useState(false);
  const [kind, setKind] = useState<Scene["kind"]>("web");
  const [status, setStatus] = useState<PlaybackStatus | null>(null);
  const [checking, setChecking] = useState(false);
  const [plexConfigured, setPlexConfigured] = useState(false);
  const [plexDefaultBitrate, setPlexDefaultBitrate] = useState(4000);
  const [pickerScene, setPickerScene] = useState<string | null>(null);
  const [pickerItems, setPickerItems] = useState<PlexBrowseItem[]>([]);
  const [pickerPath, setPickerPath] = useState<{ title: string; key?: string }[]>([]);
  const [pickerBusy, setPickerBusy] = useState(false);

  async function checkPlayback() {
    setChecking(true);
    try {
      setStatus(await api.playlistStatus());
    } catch (error) {
      fail(error);
    } finally {
      setChecking(false);
    }
  }

  useEffect(() => {
    api.playlist().then(setPlaylist).catch(fail);
    api.plexConfig().then((config) => {
      setPlexConfigured(config.configured);
      setPlexDefaultBitrate(config.default_bitrate_kbps);
    }).catch(fail);
  }, [fail]);

  async function openPlexPicker(sceneId: string) {
    setPickerScene(sceneId);
    setPickerPath([{ title: "Libraries" }]);
    setPickerBusy(true);
    try {
      setPickerItems((await api.plexBrowse()).items);
    } catch (error) {
      setPickerScene(null);
      fail(error);
    } finally {
      setPickerBusy(false);
    }
  }

  async function browsePlex(item: PlexBrowseItem) {
    if (!item.container) {
      if (pickerScene && item.id) {
        update(pickerScene, {
          source: "plex", plex_rating_key: item.id, url: "", title: item.title.slice(0, 80),
        });
        setPickerScene(null);
      }
      return;
    }
    const key = item.key ?? item.id;
    if (!key) return;
    setPickerBusy(true);
    try {
      setPickerItems((await api.plexBrowse(key)).items);
      setPickerPath((current) => [...current, { title: item.title, key }]);
    } catch (error) {
      fail(error);
    } finally {
      setPickerBusy(false);
    }
  }

  async function pickerBack() {
    if (pickerPath.length <= 1) return;
    const previous = pickerPath.slice(0, -1);
    setPickerBusy(true);
    try {
      setPickerItems((await api.plexBrowse(previous[previous.length - 1]?.key)).items);
      setPickerPath(previous);
    } catch (error) {
      fail(error);
    } finally {
      setPickerBusy(false);
    }
  }

  function update(id: string, changes: Partial<Scene>) {
    setPlaylist((current) => current && ({
      ...current,
      scenes: current.scenes.map((scene) => scene.id === id ? { ...scene, ...changes } : scene),
    }));
  }

  function add() {
    const scene: Scene = {
      id: crypto.randomUUID(), kind, title: "", enabled: true,
      duration: kind === "video" ? 300 : 60, url: "", layout: "clock-notice", text: "",
    };
    setPlaylist((current) => current && ({ ...current, scenes: [...current.scenes, scene] }));
  }

  function move(index: number, offset: number) {
    setPlaylist((current) => {
      if (!current) return current;
      const scenes = [...current.scenes];
      [scenes[index], scenes[index + offset]] = [scenes[index + offset], scenes[index]];
      return { ...current, scenes };
    });
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!playlist) return;
    setBusy(true);
    try {
      setPlaylist(await api.savePlaylist(playlist));
      notify("Playlist saved and applied. Playback restarts at the first enabled scene.", "success");
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card wide" onSubmit={save}>
      <h2>Carousel</h2>
      <div className="actions">
        <button type="button" disabled={checking} onClick={checkPlayback}>Check playback status</button>
        {status && <p className="muted">
          {status.updated === null ? "No playback status available yet." :
            "Last report: " + new Date(status.updated * 1000).toLocaleString()}
        </p>}
      </div>
      <p className="muted">
        The carousel replaces the single web page while enabled. Edits take effect only
        after Save and apply. Disabling it restores the URL in Display settings.
      </p>
      <p className="warn">
        Experimental: scene changes relaunch the browser. Streaming video is muted by
        default and requires mpv on the base image. Image uploads are not included yet.
        Plex playback uses the private server configuration above.
      </p>
      <fieldset className="playlist-fields" disabled={!playlist || busy}>
        <label className="check">
          <input type="checkbox" checked={playlist?.enabled ?? false}
            onChange={(event) => setPlaylist((current) => current && ({
              ...current, enabled: event.target.checked,
            }))} />
          Enable carousel
        </label>
        {playlist?.scenes.map((scene, index) => (
          <fieldset className="scene-editor" key={scene.id}>
            <legend>Scene {index + 1}: {scene.title || scene.kind}</legend>
            {status?.failures[scene.id] && (
              <p className="warn" role="status">
                Last playback failure: {status.failures[scene.id].message}
                {" (" + new Date(status.failures[scene.id].time * 1000).toLocaleString() + ")"}
              </p>
            )}
            <label>
              Scene title
              <input value={scene.title} maxLength={80}
                onChange={(event) => update(scene.id, { title: event.target.value })} />
            </label>
            <label className="check">
              <input type="checkbox" checked={scene.enabled}
                onChange={(event) => update(scene.id, { enabled: event.target.checked })} />
              Include this scene
            </label>
            {scene.kind === "video" && (
              <label>
                Video source
                <select value={scene.source ?? "url"}
                  onChange={(event) => update(scene.id, {
                    source: event.target.value as Scene["source"],
                    plex_rating_key: "",
                    url: event.target.value === "plex" ? "" : scene.url,
                  })}>
                  <option value="url">Direct stream URL</option>
                  <option value="plex">Plex library</option>
                </select>
              </label>
            )}
            {scene.kind !== "dashboard" && !(scene.kind === "video" && scene.source === "plex") ? (
              <label>
                {scene.kind === "video" ? "Stream URL" : "Scene web page URL"}
                <input type="url" required value={scene.url}
                  placeholder={scene.kind === "video" ? "https://example.com/stream.m3u8" : "https://example.com"}
                  onChange={(event) => update(scene.id, { url: event.target.value })} />
              </label>
            ) : scene.kind === "video" ? (
              <div className="actions">
                <button type="button" disabled={!plexConfigured}
                  onClick={() => void openPlexPicker(scene.id)}>
                  Choose Plex media
                </button>
                {scene.plex_rating_key && <span className="muted">Selected from Plex: {scene.title}</span>}
                {!plexConfigured && <span className="muted">Configure Plex above to browse media.</span>}
              </div>
            ) : (
              <>
                <label>
                  Dashboard layout
                  <select value={scene.layout}
                    onChange={(event) => update(scene.id, { layout: event.target.value as Scene["layout"] })}>
                    <option value="clock">Clock and date</option>
                    <option value="notice">Text notice</option>
                    <option value="clock-notice">Clock, date and text notice</option>
                  </select>
                </label>
                {scene.layout !== "clock" && (
                  <label>
                    Notice text
                    <textarea value={scene.text} maxLength={2000} rows={3}
                      onChange={(event) => update(scene.id, { text: event.target.value })} />
                  </label>
                )}
              </>
            )}
            {scene.kind === "video" && (
              <label className="check">
                <input type="checkbox" checked={scene.audio_enabled ?? false}
                  onChange={(event) => update(scene.id, { audio_enabled: event.target.checked })} />
                Enable audio
              </label>
            )}
            {scene.kind === "video" && scene.source === "plex" && (
              <>
                <label>
                  Plex target bitrate (kbps)
                  <input type="number" min={500} max={20000} step={250}
                    placeholder={`Default: ${plexDefaultBitrate}`}
                    value={scene.plex_bitrate_kbps ?? ""}
                    onChange={(event) => update(scene.id, {
                      plex_bitrate_kbps: event.target.value ? Number(event.target.value) : null,
                    })} />
                </label>
                <span className="muted">Leave blank to use the global Plex default.</span>
              </>
            )}
            {scene.kind === "video" && (
              <label className="check">
                <input type="checkbox" checked={scene.duration === null}
                  onChange={(event) => update(scene.id, { duration: event.target.checked ? null : 300 })} />
                No cutoff (a live stream will not advance automatically)
              </label>
            )}
            {scene.duration !== null && (
              <label>
                {scene.kind === "video" ? "Video cutoff (seconds)" : "Scene duration (seconds)"}
                <input type="number" min={5} max={86400} step={1} required value={scene.duration}
                  onChange={(event) => update(scene.id, { duration: Number(event.target.value) })} />
              </label>
            )}
            <div className="actions">
              <button type="button" disabled={index === 0}
                aria-label={`Move scene ${index + 1} up`} onClick={() => move(index, -1)}>Move up</button>
              <button type="button" disabled={index === playlist.scenes.length - 1}
                aria-label={`Move scene ${index + 1} down`} onClick={() => move(index, 1)}>Move down</button>
              <button type="button" className="danger" aria-label={`Remove scene ${index + 1}`}
                onClick={() => setPlaylist((current) => current && ({
                  ...current, scenes: current.scenes.filter((item) => item.id !== scene.id),
                }))}>Remove</button>
            </div>
          </fieldset>
        ))}
        <div className="actions">
          <label className="grow">
            New scene type
            <select value={kind} onChange={(event) => setKind(event.target.value as Scene["kind"])}>
              <option value="web">Web page</option>
              <option value="dashboard">Widget dashboard</option>
              <option value="video">Streamed video</option>
            </select>
          </label>
          <button type="button" disabled={(playlist?.scenes.length ?? 0) >= 30} onClick={add}>Add scene</button>
        </div>
        <p className="muted">
          Up to 30 scenes. Durations: 5 seconds to 24 hours. Web timing starts when
          Chromium launches (includes page loading); dashboard timing starts after rendering.
          Videos end at completion or the cutoff, whichever comes first.
        </p>
        <button className="primary">Save and apply</button>
      </fieldset>
      {pickerScene && (
        <div className="plex-picker-backdrop">
          <section className="plex-picker" role="dialog" aria-modal="true" aria-labelledby="plex-picker-title">
            <h2 id="plex-picker-title">Choose Plex media</h2>
            <p className="muted">{pickerPath.map((part) => part.title).join(" / ")}</p>
            <div className="actions">
              <button type="button" disabled={pickerBusy || pickerPath.length <= 1}
                onClick={() => void pickerBack()}>Back</button>
              <button type="button" onClick={() => setPickerScene(null)}>Cancel</button>
            </div>
            {pickerBusy ? <p role="status">Loading Plex library…</p> : (
              <ul>
                {pickerItems.map((item) => (
                  <li key={item.id ?? item.key ?? item.title}>
                    <button type="button" onClick={() => void browsePlex(item)}>
                      {item.title}{item.container ? " ›" : ""}
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {!pickerBusy && pickerItems.length === 0 && <p>No media found in this location.</p>}
          </section>
        </div>
      )}
    </form>
  );
}
