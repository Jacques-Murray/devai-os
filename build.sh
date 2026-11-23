#!/usr/bin/env bash
set -euo pipefail

# Build configuration constants
DISTRO_NAME="DevAI-OS"
DEBIAN_SUITE="trixie"
ARCH="amd64"
MIRROR="http://deb.debian.org/debian/"
ARCHIVES_DIR="config/archives"

require_root() {
    if [[ "${EUID}" -ne 0 ]]; then
        echo "Error: Run this build script as root." >&2
        exit 1
    fi
}

fetch_apt_keys() {
    # Download ASCII-armored GPG keys so live-build trusts third-party APT repos
    mkdir -p "${ARCHIVES_DIR}"

    curl -fsSL "https://packages.microsoft.com/keys/microsoft.asc" \
        -o "${ARCHIVES_DIR}/microsoft.key.chroot"
    cp "${ARCHIVES_DIR}/microsoft.key.chroot" "${ARCHIVES_DIR}/vscode.key.chroot"

    curl -fsSL "https://download.docker.com/linux/debian/gpg" \
        -o "${ARCHIVES_DIR}/docker.key.chroot"

    curl -fsSL "https://downloads.claude.ai/desktop/debian/gpg.key" \
        -o "${ARCHIVES_DIR}/claude.key.chroot"
}

configure_live_build() {
    lb clean --purge
    lb config \
        --distribution "${DEBIAN_SUITE}" \
        --architectures "${ARCH}" \
        --archive-areas "main contrib non-free non-free-firmware" \
        --mirror-bootstrap "${MIRROR}" \
        --mirror-binary "${MIRROR}" \
        --bootloaders "syslinux,grub-efi" \
        --bootappend-live "bool=live components nottyautologin console=tty0 console=ttyS0,115200" \
        --debian-installer "live" \
        --iso-application "${DISTRO_NAME}" \
        --iso-volume "${DISTRO_NAME}" \
        --apt-recommends true \
        --security true \
        --updates true
}

main() {
    require_root
    fetch_apt_keys
    configure_live_build
    lb build
}

main "$@"