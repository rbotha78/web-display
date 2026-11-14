import { useEffect, useState } from "react";

export interface NowPlaying {
  state: string;
  title: string;
  artist: string;
  album: string;
  art?: string;
}

export function NowPlayingContent({ value, artUrl, onArtError }: {
  value: NowPlaying | null;
  artUrl?: string;
  onArtError?: () => void;
}) {
  const active = value && (value.state === "playing" || value.state === "paused") && value.title;
  return <section className="now-playing-scene" aria-label="Now playing">
    {active && artUrl ?
      <img className="now-playing-cover" src={artUrl} alt={`Cover of ${value.album || value.title}`}
        onError={onArtError} /> :
      <div className="now-playing-cover now-playing-placeholder" aria-hidden="true">
        <svg viewBox="0 0 24 24" focusable="false">
          <path d="M9 18V5l12-2v13M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm12-2a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
        </svg>
      </div>}
    <div className="now-playing-text">
      {active ? <>
        <p className="now-playing-label">{value.state === "paused" ? "Paused" : "Now playing"}</p>
        <h1>{value.title}</h1>
        {value.artist && <p className="now-playing-artist">{value.artist}</p>}
        {value.album && <p className="now-playing-album">{value.album}</p>}
      </> : <h1>{!value ? "Loading music..." : value.state === "waiting" ? "Loading the next song..." : "No music playing"}</h1>}
    </div>
  </section>;
}

/** Kiosk scene: polls the loopback scene server, which proxies the cover without exposing the relay. */
export function NowPlayingDisplay() {
  const [value, setValue] = useState<NowPlaying | null>(null);
  const [brokenArt, setBrokenArt] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    const poll = async () => {
      try {
        const response = await fetch("/api/music", { signal: controller.signal });
        if (!response.ok) throw new Error("Could not load music status");
        const next: NowPlaying = await response.json();
        if (!controller.signal.aborted) setValue(next);
      } catch {
        // Keep the last details; the next poll retries.
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(poll, 2000);
      }
    };
    void poll();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, []);

  const art = value?.art && value.art !== brokenArt ? value.art : "";
  return <NowPlayingContent value={value} artUrl={art ? `/api/music/art?v=${art}` : undefined}
    onArtError={() => setBrokenArt(art)} />;
}
