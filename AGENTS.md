# Agent workflow

## Starting or resuming a session

- Read `.github/copilot-instructions.md` for commands, architecture and invariants,
  then `HANDOFF.md` for current implementation, release, deployment and acceptance
  status. `PLAN.md` records approved scope; `README.md` documents operation.
- Inspect the worktree and recent commits before editing. Preserve existing
  changes; do not assume that deployed development code is a published release.
- Use the dated current-status sections in `HANDOFF.md`, not its historical
  measurements, as the starting point. Verify live state before hardware work.
  Session-local todos and artifacts are not available to every future agent.

## Iteration workflow

- After each implementation iteration, update `README.md` with the current
  user-facing features, settings and relevant limitations.
- Update `HANDOFF.md` with completed work, exact validation, deployment state,
  release boundaries and unresolved acceptance checks. Update `PLAN.md` when
  agreed scope or implementation status changes. Keep durable conventions in
  `.github/copilot-instructions.md`; avoid duplicating transient status there.
- Validate the change and rebuild both UI bundles when changing UI code.
- Deploy completed iterations automatically to both development displays:
  Pi 2 (`web-display-pi2`) and Pi 3 (`web-display-pi3`), user `webdebug`.
  Their addresses change (Ethernet versus Wi-Fi, DHCP), so locate them with mDNS
  before each deployment (`avahi-browse -rpt _web-display._tcp`, IPv4 entries)
  rather than reusing remembered addresses. Do not require a separate deployment request. Use pinned SSH host verification:
  `StrictHostKeyChecking=yes` and `BatchMode=yes` for both SSH and SCP, including
  child commands in the push script. Never silently replace a changed host key.
- Use `tools/push-to-device.sh --restart-kiosk` for kiosk/UI changes and verify
  services and the deployed assets afterwards. Development pushes preserve API
  keys and playlists but replace signed installations and clear update state;
  if either device is no longer a development target, ask before replacing it.
- Do not commit, push or publish releases unless requested.
- Documentation-only handoff updates do not need another device deployment.
