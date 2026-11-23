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

ensure_debian_live_build() {
    if lb --version 2>&1 | grep -q "^3\.0~a57" || [[ ! -f /usr/share/keyrings/debian-archive-keyring.gpg ]]; then
        echo "Installing Debian Trixie live-build and debian-archive-keyring..."
        local index_data
        index_data=$(curl -fsSL "http://deb.debian.org/debian/dists/trixie/main/binary-amd64/Packages.gz" | gzip -dc)

        for pkg in live-build debian-archive-keyring; do
            local deb_path
            deb_path=$(printf '%s\n' "${index_data}" \
                | awk -v p="${pkg}" '$0 == "Package: " p {found=1} found && /^Filename:/ {print $2; exit}')
            if [[ -z "${deb_path}" ]]; then
                echo "Error: Could not resolve ${pkg} from Debian Trixie." >&2
                exit 1
            fi
            curl -fsSL "http://deb.debian.org/debian/${deb_path}" -o "/tmp/${pkg}.deb"
            dpkg -i "/tmp/${pkg}.deb"
            rm -f "/tmp/${pkg}.deb"
        done
    fi
}

main() {
    require_root
    ensure_debian_live_build
    fetch_apt_keys
    configure_live_build
    lb build
}

main "$@"