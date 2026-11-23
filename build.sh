#!/usr/bin/env bash
set -euo pipefail

# Build configuration constants
DISTRO_NAME="DevAI-OS"
DEBIAN_SUITE="trixie"
ARCH="amd64"
MIRROR="http://deb.debian.org/debian/"
ARCHIVES_DIR="config/archives"
PACKAGE_LISTS_DIR="config/package-lists"

require_root() {
    if [[ "${EUID}" -ne 0 ]]; then
        echo "Error: Run this build script as root." >&2
        exit 1
    fi
}

verify_package_lists() {
    if ! compgen -G "${PACKAGE_LISTS_DIR}/*.list.chroot" > /dev/null; then
        echo "Error: No *.list.chroot files found in ${PACKAGE_LISTS_DIR}/" >&2
        exit 1
    fi
    echo "Found package lists:"
    ls -1 "${PACKAGE_LISTS_DIR}"/*.list.chroot
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

download_file() {
    local url="$1"
    local destination="$2"

    echo "Fetching ${url}"
    if ! curl -fSL "${url}" -o "${destination}"; then
        echo "Error: Failed to download ${url}" >&2
        exit 1
    fi
}

fetch_microsoft_keys() {
    local tmp_dir
    tmp_dir=$(mktemp -d)
    mkdir -p "${tmp_dir}/gnupg" "${tmp_dir}/deb"
    chmod 700 "${tmp_dir}/gnupg"

    download_file "https://packages.microsoft.com/keys/microsoft.asc" \
        "${tmp_dir}/microsoft.asc"
    download_file "https://packages.microsoft.com/config/debian/13/packages-microsoft-prod.deb" \
        "${tmp_dir}/packages-microsoft-prod.deb"

    dpkg-deb -x "${tmp_dir}/packages-microsoft-prod.deb" "${tmp_dir}/deb"

    GNUPGHOME="${tmp_dir}/gnupg" gpg --batch --quiet --import "${tmp_dir}/microsoft.asc"
    while IFS= read -r keyring_file; do
        GNUPGHOME="${tmp_dir}/gnupg" gpg --batch --quiet --import "${keyring_file}"
    done < <(find "${tmp_dir}/deb/usr/share/keyrings" -type f)

    GNUPGHOME="${tmp_dir}/gnupg" gpg --batch --yes --armor --export > "${ARCHIVES_DIR}/microsoft.key.chroot"
    cp "${ARCHIVES_DIR}/microsoft.key.chroot" "${ARCHIVES_DIR}/vscode.key.chroot"

    rm -rf "${tmp_dir}"
}

fetch_apt_keys() {
    mkdir -p "${ARCHIVES_DIR}"
    rm -f "${ARCHIVES_DIR}/claude.key.chroot"

    fetch_microsoft_keys

    download_file "https://download.docker.com/linux/debian/gpg" \
        "${ARCHIVES_DIR}/docker.key.chroot"

    download_file "https://us-central1-apt.pkg.dev/doc/repo-signing-key.gpg" \
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
        --debootstrap-options "--include=ca-certificates,gnupg" \
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
    verify_package_lists
    ensure_debian_live_build
    fetch_apt_keys
    configure_live_build
    lb build
}

main "$@"