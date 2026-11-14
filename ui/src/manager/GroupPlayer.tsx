import { useCallback, useEffect, useState } from "react";
import { DeviceApiContext } from "../DeviceApiContext";
import { MusicControls } from "../components/MusicControls";
import type { Notice } from "../components/Toast";
import { settingsApi, type Device } from "./api";

/** Floating player for music sync groups; the leader's controls drive every member. */
export function GroupPlayer({ leaders, notify }: {
  leaders: Device[];
  notify: (text: string, kind?: Notice["kind"]) => void;
}) {
  const [selected, setSelected] = useState(leaders[0]?.id ?? "");
  const leader = leaders.find((device) => device.id === selected) ?? leaders[0];
  const [api, setApi] = useState<ReturnType<typeof settingsApi> | null>(null);

  useEffect(() => {
    if (!leader) return;
    const lifetime = new AbortController();
    setApi(settingsApi(leader.id, lifetime.signal));
    return () => { lifetime.abort(); setApi(null); };
  }, [leader?.id]);

  const fail = useCallback((error: unknown) => {
    notify(error instanceof Error ? error.message : "Music request failed", "error");
  }, [notify]);
  const ignore = useCallback(() => undefined, []);

  if (!leader || !api) return null;
  const describe = (device: Device) => {
    const followers = device.music_sync?.members?.length ?? 0;
    return `${device.name} group` + (followers ? ` (+${followers})` : "");
  };
  const label = leaders.length > 1 ?
    <select className="music-group-select" aria-label="Music group" value={leader.id}
      onChange={(event) => setSelected(event.target.value)}>
      {leaders.map((device) => <option key={device.id} value={device.id}>{describe(device)}</option>)}
    </select> :
    <small className="music-group-label">{describe(leader)}</small>;
  return <DeviceApiContext.Provider value={api}>
    <MusicControls key={leader.id} fail={fail} onEnabledChange={ignore} label={label} />
  </DeviceApiContext.Provider>;
}
