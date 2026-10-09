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
manifest="${files}/install-manifest"
sources=()
install_commands=""
sudoers=""
while read -r mode source destination; do
	case "${mode}" in "" | "#"*) continue ;; esac
	sources+=("${files}/${source}")
	install_commands+="install -D -m ${mode} ${stage}/${source} ${destination}"$'\n'
	if [[ ${destination} == /etc/sudoers.d/* ]]; then
		sudoers+="visudo -cf ${stage}/${source}"$'\n'
	fi
done <"${manifest}"
scp -q "${sources[@]}" "${target}:${stage}/"

ssh "${target}" "sudo sh -eu" <<EOF
rm -rf /usr/lib/python3/dist-packages/webdisplay
cp -a ${stage}/webdisplay /usr/lib/python3/dist-packages/webdisplay
${sudoers}${install_commands}rm -rf ${stage}
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
