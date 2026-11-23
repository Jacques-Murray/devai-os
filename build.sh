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

download_key() {
    local url="$1"
    local destination="$2"

    echo "Fetching APT signing key: ${url}"
    if ! curl -fSL "${url}" -o "${destination}"; then
        echo "Error: Failed to download GPG key from ${url}" >&2
        exit 1
    fi
}

fetch_apt_keys() {
    mkdir -p "${ARCHIVES_DIR}"
    rm -f "${ARCHIVES_DIR}/claude.key.chroot"

    download_key "https://packages.microsoft.com/keys/microsoft.asc" \
        "${ARCHIVES_DIR}/microsoft.key.chroot"
    cp "${ARCHIVES_DIR}/microsoft.key.chroot" "${ARCHIVES_DIR}/vscode.key.chroot"

    download_key "https://download.docker.com/linux/debian/gpg" \
        "${ARCHIVES_DIR}/docker.key.chroot"

    download_key "https://us-central1-apt.pkg.dev/doc/repo-signing-key.gpg" \
        "${ARCHIVES_DIR}/antigravity.key.chroot"
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
        --bootappend-live "boot=live components nottyautologin console=tty0 console=ttyS0,115200" \
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