# Iteration workflow

- After each implementation iteration, update `README.md` with the current
  user-facing features, settings and relevant limitations.
- Validate the change and rebuild both UI bundles when changing UI code.
- Deploy completed iterations automatically to both development displays:
  Pi 2 `webdebug@192.168.0.249` and Pi 3 `webdebug@192.168.0.251`.
  Do not require a separate deployment request. Use pinned SSH host verification.
- Use `tools/push-to-device.sh --restart-kiosk` for kiosk/UI changes and verify
  services and the deployed assets afterwards. Development pushes preserve API
  keys and playlists but replace signed installations and clear update state;
  if either device is no longer a development target, ask before replacing it.
- Do not commit, push or publish releases unless requested.
