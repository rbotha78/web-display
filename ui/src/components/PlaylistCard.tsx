import { useEffect, useState, type FormEvent } from "react";
import { api, type PlaybackStatus, type Playlist, type Scene } from "../api";
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
  }, [fail]);

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
        Experimental: scene changes relaunch the browser. Streaming video is muted and
        requires mpv on the base image. Image uploads and the Plex picker are not included yet.
        Use direct HTTP(S) media URLs, without private tokens.
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
            {scene.kind !== "dashboard" ? (
              <label>
                {scene.kind === "video" ? "Stream URL" : "Scene web page URL"}
                <input type="url" required value={scene.url}
                  placeholder={scene.kind === "video" ? "https://example.com/stream.m3u8" : "https://example.com"}
                  onChange={(event) => update(scene.id, { url: event.target.value })} />
              </label>
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
    </form>
  );
}
