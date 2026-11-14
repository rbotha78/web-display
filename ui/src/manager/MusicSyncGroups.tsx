import { useState, type FormEvent } from "react";
import { api, type Device, type SyncReply, type SyncResult } from "./api";

interface Props {
  devices: Device[];
  refresh: () => Promise<void>;
  close: () => void;
  notify: (text: string, kind?: "info" | "success" | "error") => void;
}

function state(device?: Device) {
  return device?.music_sync ?? device?.music_sync_last_known;
}

function unavailable(device: Device) {
  return !device.online ? "offline" : !device.music_sync?.supported
    ? device.music_sync?.supported === false ? "unsupported — update firmware" : "sync state unavailable"
    : "";
}

export function MusicSyncGroups({ devices, refresh, close, notify }: Props) {
  const [leader, setLeader] = useState("");
  const [followers, setFollowers] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<SyncResult[]>([]);
  const grouped = devices.filter(device => state(device)?.group_id && state(device)?.role !== "off");
  const groups = [...new Set(grouped.map(device => state(device)!.group_id!))];
  const selected = devices.filter(device => device.id === leader || followers.includes(device.id));
  const reassigned = selected.filter(device => state(device)?.group_id && state(device)?.role !== "off");

  const run = async (work: () => Promise<SyncReply>) => {
    setBusy(true);
    setResults([]);
    try {
      const reply = await work();
      setResults(reply.results);
      notify(reply.results.some(result => !result.ok) ? "Some music sync changes failed" : "Music sync updated",
        reply.results.some(result => !result.ok) ? "error" : "success");
      await refresh();
    } catch (error) {
      notify(error instanceof Error ? error.message : "Music sync request failed", "error");
    } finally {
      setBusy(false);
    }
  };

  const dissolve = (group_id: string) => {
    if (window.confirm("Dissolve this music sync group? Each display will use its own music settings.")) {
      void run(() => api.dissolveSyncGroup(group_id));
    }
  };

  const create = (event: FormEvent) => {
    event.preventDefault();
    if (!leader || !followers.length || selected.some(device => unavailable(device))) return;
    if (reassigned.length && !window.confirm("Reassign " + reassigned.map(device => device.name).join(", ")
      + " to a new music sync group? Existing followers of a reassigned leader will lose their leader. "
      + "Cancel and use Dissolve first to leave the old group cleanly.")) return;
    void run(() => api.createSyncGroup(leader, followers));
  };

  return <section className="card manager-sync-panel" aria-label="Music sync groups">
    <div className="actions manager-sync-heading">
      <h2>Music sync groups</h2>
      <button type="button" onClick={close}>Close music sync groups</button>
    </div>
    <p className="muted">Followers play the leader&apos;s music, using the leader&apos;s music settings;
      followers&apos; music settings are ignored while following. All members must use the same Plex server.
      Controls on any display act on the whole group. Volume, video audio and sleep are per display.</p>
    <p className="muted">Only enrolled members with known sync state are listed. Offline members may
      need retrying after they reconnect; members not enrolled in this manager must be managed separately.</p>
    <h3>Existing groups</h3>
    {!groups.length && <p className="muted">No music sync groups.</p>}
    {groups.map(group_id => {
      const members = grouped.filter(device => state(device)?.group_id === group_id);
      const lead = members.find(device => state(device)?.role === "leader");
      const following = members.filter(device => state(device)?.role === "follower");
      return <section className="manager-sync-group" key={group_id} aria-label={"Group led by " + (lead?.name || state(following[0])?.leader?.name || "unknown leader")}>
        <p><strong>Leader: {lead?.name || state(following[0])?.leader?.name || "not enrolled"}</strong>
          {lead && (!lead.online || !lead.music_sync) && " — last known (unavailable)"}</p>
        <ul>{following.map(device => {
          const sync = state(device);
          const error = !device.online ? "offline (last known)" : !device.music_sync ? "sync state unavailable"
            : sync?.error || (sync?.connected === false ? "leader disconnected" : "");
          return <li key={device.id}>
            <span>{device.name}{error && " — " + error}</span>
            <button type="button" disabled={busy} onClick={() => {
              if (window.confirm("Remove " + device.name + " from this music sync group?")) {
                void run(() => api.leaveSyncGroup(device.id));
              }
            }} aria-label={"Remove follower " + device.name}>Remove follower</button>
          </li>;
        })}</ul>
        {!following.length && <p className="muted">No enrolled followers currently known.</p>}
        <button type="button" disabled={busy} onClick={() => dissolve(group_id)}>Dissolve</button>
      </section>;
    })}
    <form onSubmit={create}>
      <h3>Create a group</h3>
      <label>Leader
        <select value={leader} disabled={busy} onChange={event => {
          setLeader(event.target.value);
          setFollowers(current => current.filter(id => id !== event.target.value));
        }}>
          <option value="">Choose leader</option>
          {devices.map(device => <option key={device.id} value={device.id} disabled={!!unavailable(device)}>
            {device.name}{unavailable(device) && " — " + unavailable(device)}
          </option>)}
        </select>
      </label>
      <fieldset disabled={busy}>
        <legend>Followers (choose at least one)</legend>
        {devices.map(device => <label className="manager-sync-choice" key={device.id}>
          <input type="checkbox" checked={followers.includes(device.id)}
            disabled={device.id === leader || !!unavailable(device)}
            onChange={event => setFollowers(current => event.target.checked
              ? [...current, device.id] : current.filter(id => id !== device.id))} />
          <span>{device.name}{device.id === leader ? " — selected leader"
            : unavailable(device) ? " — " + unavailable(device) : ""}</span>
        </label>)}
      </fieldset>
      {!!reassigned.length && <div className="manager-sync-warning">
        <p>Already grouped: {reassigned.map(device => device.name).join(", ")}.
          Creating a group replaces their current roles. Reassigning a leader strands its old followers.</p>
        {reassigned.filter(device => state(device)?.role === "leader").map(device =>
          <button type="button" key={device.id} disabled={busy}
            onClick={() => dissolve(state(device)!.group_id!)}>Dissolve {device.name}&apos;s group first</button>)}
      </div>}
      <button className="primary" type="submit" disabled={busy || !leader || !followers.length
        || selected.some(device => unavailable(device))}>Create group</button>
    </form>
    {!!results.length && <div aria-live="polite"><h3>Results</h3><ul>{results.map(result =>
      <li key={result.id}>{result.name}: {result.ok ? "updated" : result.error || "failed"}</li>)}</ul></div>}
  </section>;
}
