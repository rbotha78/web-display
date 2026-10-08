#!/usr/bin/env bash
# Push the current source and service files to a development device over SSH.
set -euo pipefail

if [[ $# -ne 1 ]]; then
	echo "Usage: $0 webdebug@DEVICE_IP" >&2
	exit 2
fi
target="$1"
root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
files="${root}/image/stage-web-display/01-install/files"
stage="/tmp/web-display-push"

ssh "${target}" "rm -rf ${stage} && mkdir -p ${stage}"
scp -q -r "${root}/src/webdisplay" "${target}:${stage}/webdisplay"
scp -q "${files}/wifi-action" "${files}/system-action" "${files}/kiosk-session" \
	"${files}/web-display.service" "${files}/web-display-network.service" \
	"${files}/web-display-sudoers" "${target}:${stage}/"

ssh "${target}" "sudo sh -eu" <<EOF
rm -rf /usr/lib/python3/dist-packages/webdisplay
cp -a ${stage}/webdisplay /usr/lib/python3/dist-packages/webdisplay
install -m 0755 ${stage}/wifi-action /usr/lib/web-display/wifi-action
install -m 0755 ${stage}/system-action /usr/lib/web-display/system-action
install -m 0755 ${stage}/kiosk-session /usr/lib/web-display/kiosk-session
install -m 0644 ${stage}/web-display.service /etc/systemd/system/web-display.service
install -m 0644 ${stage}/web-display-network.service /etc/systemd/system/web-display-network.service
visudo -cf ${stage}/web-display-sudoers
install -m 0440 ${stage}/web-display-sudoers /etc/sudoers.d/web-display
rm -rf ${stage}
systemctl daemon-reload
systemctl enable web-display-network.service
systemctl restart web-display.service
systemctl restart web-display-network.service
EOF
echo "Pushed. The kiosk service was not restarted; run 'sudo systemctl restart web-display-kiosk' if kiosk code changed."
