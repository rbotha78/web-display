#!/usr/bin/env bash
# Push the current source and service files to a development device over SSH.
set -euo pipefail

restart_kiosk=0
if [[ ${1:-} == "--restart-kiosk" ]]; then
	restart_kiosk=1
	shift
fi
if [[ $# -ne 1 ]]; then
	echo "Usage: $0 [--restart-kiosk] webdebug@DEVICE_IP" >&2
	exit 2
fi
target="$1"
root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
files="${root}/image/stage-web-display/01-install/files"
stage="/tmp/web-display-push"

ssh "${target}" "rm -rf ${stage} && mkdir -p ${stage}"
scp -q -r "${root}/src/webdisplay" "${target}:${stage}/webdisplay"
scp -q "${files}/wifi-action" "${files}/hostname-action" "${files}/system-action" \
	"${files}/create-certificate" "${files}/kiosk-session" \
	"${files}/web-display.service" "${files}/web-display-network.service" \
	"${files}/web-display-certificate.service" "${files}/web-display-kiosk.service" \
	"${files}/web-display-sudoers" "${files}/web-display-release" \
	"${files}/Xwrapper.config" "${files}/20-web-display-fbdev.conf" "${target}:${stage}/"

ssh "${target}" "sudo sh -eu" <<EOF
rm -rf /usr/lib/python3/dist-packages/webdisplay
cp -a ${stage}/webdisplay /usr/lib/python3/dist-packages/webdisplay
install -m 0755 ${stage}/wifi-action /usr/lib/web-display/wifi-action
install -m 0755 ${stage}/hostname-action /usr/lib/web-display/hostname-action
install -m 0755 ${stage}/system-action /usr/lib/web-display/system-action
install -m 0755 ${stage}/create-certificate /usr/lib/web-display/create-certificate
install -m 0755 ${stage}/kiosk-session /usr/lib/web-display/kiosk-session
install -m 0644 ${stage}/web-display.service /etc/systemd/system/web-display.service
install -m 0644 ${stage}/web-display-network.service /etc/systemd/system/web-display-network.service
install -m 0644 ${stage}/web-display-certificate.service /etc/systemd/system/web-display-certificate.service
install -m 0644 ${stage}/web-display-kiosk.service /etc/systemd/system/web-display-kiosk.service
install -m 0644 ${stage}/web-display-release /etc/web-display-release
install -m 0644 ${stage}/Xwrapper.config /etc/X11/Xwrapper.config
install -d -m 0755 /etc/X11/xorg.conf.d
install -m 0644 ${stage}/20-web-display-fbdev.conf /etc/X11/xorg.conf.d/20-web-display-fbdev.conf
visudo -cf ${stage}/web-display-sudoers
install -m 0440 ${stage}/web-display-sudoers /etc/sudoers.d/web-display
rm -rf ${stage}
systemctl daemon-reload
systemctl enable web-display-network.service
systemctl restart web-display.service
systemctl restart web-display-network.service
if [ "${restart_kiosk}" = 1 ]; then
	systemctl restart web-display-kiosk.service
fi
EOF
if [[ ${restart_kiosk} == 1 ]]; then
	echo "Pushed. The kiosk was restarted."
else
	echo "Pushed. The kiosk was not restarted; rerun with --restart-kiosk if kiosk code, its unit or the Xorg config changed."
fi
