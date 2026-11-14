import { useCallback, useEffect, useState } from "react";
import { ApiError, type MusicConfig, type NetworkStatus, type Status } from "../api";
import { useDeviceApi } from "../DeviceApiContext";
import { DebugKeysCard } from "./DebugKeysCard";
import { BluetoothCard } from "./BluetoothCard";
import { DisplayCard } from "./DisplayCard";
import { PlaylistCard } from "./PlaylistCard";
import { PlexCard } from "./PlexCard";
import { RailDataCard } from "./RailDataCard";
import { WeatherCard } from "./WeatherCard";
import { HostnameCard } from "./HostnameCard";
import { StatusCard } from "./StatusCard";
import type { Notice } from "./Toast";
import { ManagerKeysCard } from "./ManagerKeysCard";
import { UpdatesCard } from "./UpdatesCard";
import { WifiCard } from "./WifiCard";
import { SleepCard } from "./SleepCard";
import { MusicControls } from "./MusicControls";

interface Props {
  notify: (text: string, kind?: Notice["kind"]) => void;
  onExpired: () => void;
  canManageKeys: boolean;
}

export function Dashboard({ notify, onExpired, canManageKeys }: Props) {
  const [page, setPage] = useState<"carousel" | "global">("global");
  const [playbackConfig, setPlaybackConfig] = useState<MusicConfig | undefined>();

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

  return (
    <>
      <nav className="admin-menu" aria-label="Administration">
        <button type="button" className={page === "carousel" ? "" : "ghost"}
          aria-current={page === "carousel" ? "page" : undefined} aria-controls="carousel-settings"
          onClick={() => setPage("carousel")}>Carousel</button>
        <button type="button" className={page === "global" ? "" : "ghost"}
          aria-current={page === "global" ? "page" : undefined} aria-controls="global-settings"
          onClick={() => setPage("global")}>Global settings</button>
      </nav>
      <section id="carousel-settings" aria-label="Carousel configuration" hidden={page !== "carousel"}>
        <PlaylistCard notify={notify} fail={fail} active={page === "carousel"} />
      </section>
      <section id="global-settings" aria-label="Global settings" hidden={page !== "global"}>
        <GlobalSettings notify={notify} fail={fail}
          canManageKeys={canManageKeys} active={page === "global"} playbackConfig={playbackConfig} />
      </section>
      <MusicControls fail={fail} onEnabledChange={setPlaybackConfig} />
    </>
  );
}

function GlobalSettings({ notify, fail, canManageKeys, active, playbackConfig }: {
  notify: Props["notify"];
  fail: (error: unknown) => void;
  canManageKeys: boolean;
  active: boolean;
  playbackConfig?: MusicConfig;
}) {
  const api = useDeviceApi();
  const [status, setStatus] = useState<Status | null>(null);
  const [network, setNetwork] = useState<NetworkStatus | null>(null);
  const refreshNetwork = useCallback(async () => {
    try {
      setNetwork(await api.network());
    } catch (error) {
      fail(error);
    }
  }, [fail]);

  useEffect(() => {
    if (!active) return;
    api.status().then(setStatus).catch(fail);
    void refreshNetwork();
    const timer = window.setInterval(() => void refreshNetwork(), 15000);
    return () => window.clearInterval(timer);
  }, [active, fail, refreshNetwork]);

  return (
    <div className="grid">
      <StatusCard status={status} network={network} />
      <DisplayCard notify={notify} fail={fail} />
      <BluetoothCard notify={notify} fail={fail} active={active} />
      <SleepCard notify={notify} fail={fail} />
      <PlexCard notify={notify} fail={fail} playbackConfig={playbackConfig} />
      <RailDataCard notify={notify} fail={fail} />
      <WeatherCard notify={notify} fail={fail} />
      <HostnameCard current={status?.hostname ?? null} notify={notify} fail={fail} />
      <WifiCard network={network} notify={notify} fail={fail} refresh={refreshNetwork} />
      <UpdatesCard notify={notify} fail={fail} />
      {canManageKeys ? <ManagerKeysCard notify={notify} fail={fail} /> : (
        <section className="card">
          <h2>Manager access</h2>
          <p className="muted">Signed in through the display manager. Manager access cannot create, list or revoke API keys.
            Those operations require an administrator-password session on the device.</p>
        </section>
      )}
      <DebugKeysCard notify={notify} fail={fail} />
    </div>
  );
}
