#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
	echo "Run this script with sudo." >&2
	exit 1
fi
if [[ $# -ne 2 ]]; then
	echo "Usage: $0 /dev/SD_ROOT_PARTITION /path/to/public-key.pub" >&2
	exit 2
fi

partition="$(readlink -f "$1")"
public_key="$(readlink -f "$2")"

[[ -b "$partition" ]] || { echo "$partition is not a block device" >&2; exit 2; }
[[ -f "$public_key" ]] || { echo "$public_key is not a file" >&2; exit 2; }
[[ "$(lsblk -ndo TYPE "$partition")" == "part" ]] || {
	echo "Select the SD card root partition, not the whole disk." >&2
	exit 2
}
if findmnt -rn -S "$partition" >/dev/null; then
	echo "$partition is already mounted; unmount it before continuing." >&2
	exit 2
fi

mountpoint="$(mktemp -d /tmp/web-display-debug.XXXXXX)"
cleanup() {
	mountpoint -q "$mountpoint" && umount "$mountpoint"
	rmdir "$mountpoint"
}
trap cleanup EXIT
if ! mount -t ext4 -o nosuid,nodev "$partition" "$mountpoint"; then
	echo "Could not mount $partition as ext4." >&2
	echo "Check the kernel log with: sudo dmesg | tail -50" >&2
	exit 2
fi

[[ -f "$mountpoint/etc/web-display-release" ]] || {
	echo "$partition is not a Web Display root filesystem." >&2
	exit 2
}
[[ -x "$mountpoint/usr/sbin/sshd" ]] || {
	echo "The image does not contain OpenSSH server." >&2
	exit 2
}

if ! grep -q '^webdebug:' "$mountpoint/etc/passwd"; then
	useradd --root "$mountpoint" --create-home --shell /bin/bash webdebug
fi

install -d -o "$(stat -c %u "$mountpoint/home/webdebug")" \
	-g "$(stat -c %g "$mountpoint/home/webdebug")" \
	-m 0700 "$mountpoint/home/webdebug/.ssh"
install -o "$(stat -c %u "$mountpoint/home/webdebug")" \
	-g "$(stat -c %g "$mountpoint/home/webdebug")" \
	-m 0600 "$public_key" "$mountpoint/home/webdebug/.ssh/authorized_keys"

cat >"$mountpoint/etc/ssh/sshd_config.d/90-web-display-debug.conf" <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
AllowUsers webdebug
EOF

cat >"$mountpoint/etc/sudoers.d/web-display-debug" <<'EOF'
webdebug ALL=(root) NOPASSWD: ALL
EOF
chmod 0440 "$mountpoint/etc/sudoers.d/web-display-debug"

rm -f "$mountpoint/etc/systemd/system/ssh.service" \
	"$mountpoint/etc/systemd/system/ssh.socket"
systemctl --root="$mountpoint" enable ssh.service

cat >"$mountpoint/usr/local/sbin/disable-web-display-debug" <<'EOF'
#!/bin/sh
set -eu
rm -f /etc/sudoers.d/web-display-debug
rm -f /etc/ssh/sshd_config.d/90-web-display-debug.conf
systemctl disable --now ssh.service ssh.socket || true
systemctl mask ssh.service ssh.socket
echo "Remote debug access disabled. Reboot recommended."
EOF
chmod 0755 "$mountpoint/usr/local/sbin/disable-web-display-debug"

grep -q '^webdebug:' "$mountpoint/etc/passwd" || {
	echo "The webdebug account was not created." >&2
	exit 1
}
awk -F: '$1 == "webdebug" && $2 ~ /^[!*]/ { found=1 } END { exit !found }' \
	"$mountpoint/etc/shadow" || {
	echo "The webdebug account does not have a locked password." >&2
	exit 1
}
[[ -s "$mountpoint/home/webdebug/.ssh/authorized_keys" ]] || {
	echo "The SSH public key was not installed." >&2
	exit 1
}
[[ -L "$mountpoint/etc/systemd/system/multi-user.target.wants/ssh.service" ]] || {
	echo "ssh.service was not enabled." >&2
	exit 1
}

sync
echo "Temporary key-only SSH enabled for user webdebug."
echo "After debugging, run: sudo disable-web-display-debug"
