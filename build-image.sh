#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PI_GEN_COMMIT="c4f875735c109c658cd5ee99eaaaf70886a853b5"
PI_GEN_DIR="${ROOT}/.build/pi-gen"
RASPBIAN_MIRROR="${RASPBIAN_MIRROR:-https://mirrorservice.org/sites/archive.raspbian.org/raspbian}"
RASPBIAN_MIRROR="${RASPBIAN_MIRROR%/}"

case "${RASPBIAN_MIRROR}" in
http://* | https://*) ;;
*)
	echo "RASPBIAN_MIRROR must be an HTTP or HTTPS URL." >&2
	exit 2
	;;
esac

mkdir -p "${ROOT}/.build"
if [[ ! -d "${PI_GEN_DIR}/.git" ]]; then
	git clone https://github.com/RPi-Distro/pi-gen.git "${PI_GEN_DIR}"
fi

git -C "${PI_GEN_DIR}" fetch --quiet origin "${PI_GEN_COMMIT}"
git -C "${PI_GEN_DIR}" checkout --quiet --detach "${PI_GEN_COMMIT}"
git -C "${PI_GEN_DIR}" clean -ffdqx

cp -a "${ROOT}/image/stage-web-display" "${PI_GEN_DIR}/stage-web-display"
python3 - "${PI_GEN_DIR}" "${RASPBIAN_MIRROR}" <<'PY'
from pathlib import Path
import sys

pi_gen = Path(sys.argv[1])
mirror = sys.argv[2]
default = "http://raspbian.raspberrypi.com/raspbian/"
files = (
    pi_gen / "stage0/prerun.sh",
    pi_gen / "stage0/00-configure-apt/files/raspbian.sources",
)
for path in files:
    content = path.read_text(encoding="utf-8")
    if default not in content:
        raise SystemExit(f"Expected default Raspbian mirror not found in {path}")
    path.write_text(content.replace(default, mirror + "/"), encoding="utf-8")
PY
cp -a "${ROOT}/src/webdisplay" \
	"${PI_GEN_DIR}/stage-web-display/01-install/files/webdisplay"
touch "${PI_GEN_DIR}/stage2/SKIP_IMAGES"

# VARIANT=development keeps key-only SSH for the webdebug account.
# VARIANT=release is hardened: no debug account, SSH masked.
VARIANT="${VARIANT:-development}"
STAGE_FILES="${PI_GEN_DIR}/stage-web-display/01-install/files"
case "${VARIANT}" in
development)
	DEBUG_SSH_PUBLIC_KEY="${DEBUG_SSH_PUBLIC_KEY:-${HOME}/.ssh/id_rsa.pub}"
	[[ -f "${DEBUG_SSH_PUBLIC_KEY}" ]] || {
		echo "Development builds need a public key: set DEBUG_SSH_PUBLIC_KEY." >&2
		exit 2
	}
	cp "${DEBUG_SSH_PUBLIC_KEY}" "${STAGE_FILES}/debug-authorized_keys"
	;;
release) ;;
*)
	echo "VARIANT must be development or release." >&2
	exit 2
	;;
esac
echo "VARIANT=${VARIANT}" >>"${STAGE_FILES}/web-display-release"

# Fast path (development only): keep the pi-gen container with the finished
# stage0-2 base and rebuild only stage-web-display. FRESH=1 forces a full
# build; the base is also rebuilt after BASE_MAX_AGE_DAYS to pick up updates.
# Release builds always start clean and use xz.
CONTAINER_NAME="web_display_pigen_${VARIANT}"
STAMP="${ROOT}/.build/base-${VARIANT}.stamp"
BASE_MAX_AGE_DAYS="${BASE_MAX_AGE_DAYS:-14}"
BASE_ID="${PI_GEN_COMMIT}:${RASPBIAN_MIRROR}"
FAST=0
if [[ "${VARIANT}" == development ]]; then
	DEPLOY_COMPRESSION="${DEPLOY_COMPRESSION:-gz}"
	base_ok() {
		[[ "${FRESH:-0}" != 1 ]] || { echo "base: FRESH=1" >&2; return 1; }
		[[ -f "${STAMP}" && "$(cat "${STAMP}")" == "${BASE_ID}" ]] ||
			{ echo "base: no matching stamp" >&2; return 1; }
		[[ -z "$(find "${STAMP}" -mtime "+${BASE_MAX_AGE_DAYS}")" ]] ||
			{ echo "base: older than ${BASE_MAX_AGE_DAYS} days" >&2; return 1; }
		docker image inspect pi-gen >/dev/null 2>&1 ||
			{ echo "base: pi-gen image missing" >&2; return 1; }
		# /config is a bind mount of a file that is rewritten on every build
		docker run --rm --volumes-from "${CONTAINER_NAME}" --volume /dev/null:/config:ro pi-gen \
			test -d "/pi-gen/work/web-display-${VARIANT}/stage2/rootfs/etc" ||
			{ echo "base: preserved stage2 rootfs missing" >&2; return 1; }
	}
	if base_ok; then
		FAST=1
	fi
else
	DEPLOY_COMPRESSION="xz"
fi
if [[ "${FAST}" == 0 ]]; then
	docker rm -v "${CONTAINER_NAME}" >/dev/null 2>&1 || true
	rm -f "${STAMP}"
fi

cat >"${PI_GEN_DIR}/config" <<EOF
IMG_NAME="web-display-${VARIANT}"
PI_GEN_RELEASE="Web Display milestone 2"
RELEASE="trixie"
TARGET_HOSTNAME="web-display"
LOCALE_DEFAULT="en_GB.UTF-8"
KEYBOARD_KEYMAP="gb"
KEYBOARD_LAYOUT="English (UK)"
TIMEZONE_DEFAULT="Europe/London"
FIRST_USER_NAME="image-build"
FIRST_USER_PASS="build-only-account-is-deleted-before-export"
DISABLE_FIRST_BOOT_USER_RENAME=1
ENABLE_SSH=0
STAGE_LIST="stage0 stage1 stage2 stage-web-display"
DEPLOY_COMPRESSION="${DEPLOY_COMPRESSION}"
EOF

if [[ "${FAST}" == 1 ]]; then
	echo "Fast build: reusing stage0-2 base (FRESH=1 for a full build)."
	for stage in stage0 stage1 stage2; do
		touch "${PI_GEN_DIR}/${stage}/SKIP"
	done
	echo "CLEAN=1" >>"${PI_GEN_DIR}/config"
	export CONTINUE=1
fi
[[ "${VARIANT}" == development ]] && export PRESERVE_CONTAINER=1

cd "${PI_GEN_DIR}"
time CONTAINER_NAME="${CONTAINER_NAME}" ./build-docker.sh
[[ "${VARIANT}" == development && "${FAST}" == 0 ]] && echo "${BASE_ID}" >"${STAMP}"
mkdir -p "${ROOT}/deploy"
cp -a deploy/. "${ROOT}/deploy/"
