import type { NetworkStatus, Status } from "../api";

function Pill({ ok, children }: { ok: boolean | null; children: string }) {
  const kind = ok === null ? "unknown" : ok ? "ok" : "bad";
  return <span className={`pill ${kind}`}>{children}</span>;
}

export function StatusCard({
  status,
  network,
}: {
  status: Status | null;
  network: NetworkStatus | null;
}) {
  const online = network ? network.internet.online : null;
  const site = network ? network.website.reachable : null;
  return (
    <section className="card">
      <h2>Status</h2>
      <dl>
        <dt>Device</dt>
        <dd>{status?.hostname ?? "…"}</dd>
        <dt>Addresses</dt>
        <dd>{status ? status.addresses.join(", ") || "none" : "…"}</dd>
        <dt>Internet</dt>
        <dd>
          <Pill ok={online}>
            {network
              ? online
                ? `Online (${network.internet.reachable_endpoints}/${network.internet.total_endpoints})`
                : "Offline"
              : "Checking"}
          </Pill>
        </dd>
        <dt>Website</dt>
        <dd>
          <Pill ok={network?.website.host ? site : null}>
            {network
              ? network.website.host
                ? `${network.website.host} ${site ? "reachable" : "unreachable"}`
                : "No URL set"
              : "Checking"}
          </Pill>
        </dd>
        <dt>Default route</dt>
        <dd>{network ? network.default_route || "none" : "…"}</dd>
      </dl>
    </section>
  );
}
