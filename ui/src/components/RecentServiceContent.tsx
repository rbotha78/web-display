import type { TrainBoard } from "../SceneScreen";
import { ServiceStationTimes } from "./ServiceStationTimes";

export function RecentServiceContent({ recent, station = "" }: {
  recent: NonNullable<TrainBoard["recent"]>;
  station?: string;
}) {
  return (
    <>
      {recent.stale && <p className="train-warning" role="status">
        Details unavailable{recent.details_error ? `: ${recent.details_error}` : ""}
      </p>}
      <ServiceStationTimes stops={recent.display_stops ?? recent.stops} station={station}
        includeArrivals={recent.display_stops !== undefined}
        lastSeen={recent.last_seen} showLastSeen={false} />
    </>
  );
}
