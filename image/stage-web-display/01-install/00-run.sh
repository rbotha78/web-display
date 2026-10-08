#!/bin/bash -e

install -d -m 0755 "${ROOTFS_DIR}/usr/lib/python3/dist-packages"
cp -a files/webdisplay "${ROOTFS_DIR}/usr/lib/python3/dist-packages/"

install -d -m 0755 "${ROOTFS_DIR}/usr/lib/web-display"
install -m 0755 files/system-action "${ROOTFS_DIR}/usr/lib/web-display/system-action"
install -m 0755 files/create-certificate \
	"${ROOTFS_DIR}/usr/lib/web-display/create-certificate"
install -m 0755 files/wifi-action "${ROOTFS_DIR}/usr/lib/web-display/wifi-action"
install -m 0755 files/kiosk-session "${ROOTFS_DIR}/usr/lib/web-display/kiosk-session"

install -m 0644 files/web-display.service \
	"${ROOTFS_DIR}/etc/systemd/system/web-display.service"
install -m 0644 files/web-display-certificate.service \
	"${ROOTFS_DIR}/etc/systemd/system/web-display-certificate.service"
install -m 0644 files/web-display-network.service \
	"${ROOTFS_DIR}/etc/systemd/system/web-display-network.service"
install -m 0644 files/web-display-kiosk.service \
	"${ROOTFS_DIR}/etc/systemd/system/web-display-kiosk.service"
install -m 0440 files/web-display-sudoers \
	"${ROOTFS_DIR}/etc/sudoers.d/web-display"
install -m 0644 files/Xwrapper.config "${ROOTFS_DIR}/etc/X11/Xwrapper.config"
install -d -m 0755 "${ROOTFS_DIR}/etc/X11/xorg.conf.d"
install -m 0644 files/20-web-display-fbdev.conf \
	"${ROOTFS_DIR}/etc/X11/xorg.conf.d/20-web-display-fbdev.conf"
install -m 0644 files/web-display-release "${ROOTFS_DIR}/etc/web-display-release"

if [ -f files/debug-authorized_keys ]; then
	install -d -m 0755 "${ROOTFS_DIR}/usr/local/sbin"
	install -m 0755 files/disable-web-display-debug \
		"${ROOTFS_DIR}/usr/local/sbin/disable-web-display-debug"
	install -m 0440 files/web-display-debug-sudoers \
		"${ROOTFS_DIR}/etc/sudoers.d/web-display-debug"
	install -d -m 0755 "${ROOTFS_DIR}/etc/ssh/sshd_config.d"
	install -m 0644 files/90-web-display-debug.conf \
		"${ROOTFS_DIR}/etc/ssh/sshd_config.d/90-web-display-debug.conf"
	install -m 0600 files/debug-authorized_keys "${ROOTFS_DIR}/tmp/debug-authorized_keys"
	on_chroot <<'EOF'
id webdebug >/dev/null 2>&1 || useradd --create-home --shell /bin/bash webdebug
passwd --lock webdebug
install -d -o webdebug -g webdebug -m 0700 /home/webdebug/.ssh
install -o webdebug -g webdebug -m 0600 /tmp/debug-authorized_keys \
	/home/webdebug/.ssh/authorized_keys
rm -f /tmp/debug-authorized_keys
EOF
fi

on_chroot <<'EOF'
getent group web-display >/dev/null || groupadd --system web-display
getent group input >/dev/null || groupadd --system input
getent group render >/dev/null || groupadd --system render
id webdisplay >/dev/null 2>&1 || useradd --system --gid web-display \
	--home-dir /var/lib/web-display --shell /usr/sbin/nologin webdisplay
id kiosk >/dev/null 2>&1 || useradd --system --gid web-display \
	--groups video,input,render --home-dir /var/lib/web-display-kiosk \
	--create-home --shell /usr/sbin/nologin kiosk

if id image-build >/dev/null 2>&1; then
	userdel --remove image-build || true
fi
passwd --lock root

if [ -x /usr/local/sbin/disable-web-display-debug ]; then
	systemctl unmask ssh.service ssh.socket
	systemctl disable ssh.socket 2>/dev/null || true
	systemctl enable ssh.service
else
	systemctl disable --now ssh.service ssh.socket 2>/dev/null || true
	systemctl mask ssh.service ssh.socket
fi
systemctl disable getty@tty1.service
systemctl enable NetworkManager.service
systemctl enable avahi-daemon.service
systemctl enable web-display-certificate.service
systemctl enable web-display.service
systemctl enable web-display-network.service
systemctl enable web-display-kiosk.service

rm -f /etc/systemd/system/getty@tty1.service.d/autologin.conf
EOF
