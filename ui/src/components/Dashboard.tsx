import { useCallback, useEffect, useState } from "react";
import { ApiError, api, type NetworkStatus, type Status } from "../api";
import { DebugKeysCard } from "./DebugKeysCard";
import { DisplayCard } from "./DisplayCard";
import { PlaylistCard } from "./PlaylistCard";
import { PlexCard } from "./PlexCard";
import { HostnameCard } from "./HostnameCard";
import { StatusCard } from "./StatusCard";
import type { Notice } from "./Toast";
import { ManagerKeysCard } from "./ManagerKeysCard";
import { UpdatesCard } from "./UpdatesCard";
import { WifiCard } from "./WifiCard";

interface Props {
  notify: (text: string, kind?: Notice["kind"]) => void;
  onExpired: () => void;
  canManageKeys: boolean;
}

export function Dashboard({ notify, onExpired, canManageKeys }: Props) {
  const [status, setStatus] = useState<Status | null>(null);
  const [network, setNetwork] = useState<NetworkStatus | null>(null);

  const fail = useCallback(
    (error: unknown) => {
      if (error instanceof ApiError && error.status === 401) {
        notify("Your session expired. Please log in again.", "error");
        onExpired();
      } else {
        notify(error instanceof Error ? error.message : "Request failed", "error");
      }
    },
    [notify, onExpired],
  );

  const refreshNetwork = useCallback(async () => {
    try {
      setNetwork(await api.network());
    } catch (error) {
      fail(error);
    }
  }, [fail]);

  useEffect(() => {
    api.status().then(setStatus).catch(fail);
    void refreshNetwork();
    const timer = window.setInterval(() => void refreshNetwork(), 15000);
    return () => window.clearInterval(timer);
  }, [fail, refreshNetwork]);

  return (
    <div className="grid">
      <StatusCard status={status} network={network} />
      <DisplayCard notify={notify} fail={fail} />
      <PlexCard notify={notify} fail={fail} />
      <PlaylistCard notify={notify} fail={fail} />
      <HostnameCard current={status?.hostname ?? null} notify={notify} fail={fail} />
      <WifiCard network={network} notify={notify} fail={fail} refresh={refreshNetwork} />
      <UpdatesCard notify={notify} fail={fail} />
      {canManageKeys ? <ManagerKeysCard notify={notify} fail={fail} /> : (
        <section className="card">
          <h2>Manager access</h2>
          <p className="muted">Signed in through the display manager. Sign out and log in with the administrator password to manage API keys.</p>
        </section>
      )}
      <DebugKeysCard notify={notify} fail={fail} />
    </div>
  );
}
