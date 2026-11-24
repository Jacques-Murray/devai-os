# DevAI-OS

DevAI-OS is a custom Debian 13 (Trixie) Linux distribution configured for polyglot software engineering, Dev Containers, and AI-assisted development.

## Included Software Stack

### Programming Languages and SDKs

- **Python:** Python 3 with `venv`, `pip`, and `uv` installed system-wide in `/usr/local/bin`
- **C# / .NET:** .NET 10 SDK (`dotnet-sdk-10.0`)
- **Rust:** `rustup` and `cargo` installed in `/opt/rust`
- **Node.js:** Fast Node Manager (`fnm`) in `/opt/fnm` with the latest LTS Node.js release
- **Flutter:** Stable Flutter SDK in `/opt/flutter` with pre-cached Linux and Android artifacts
- **Kotlin and Java:** Standalone Kotlin compiler (`2.1.20`) in `/opt/kotlinc` and OpenJDK 21
- **Android SDK:** Command-line tools, `platform-tools` (`adb`), Android 35 platform, `build-tools` (`35.0.0`), and pre-accepted licenses in `/opt/android-sdk`

### IDEs and Editors

- **Visual Studio Code:** Installed from the official Microsoft APT repository
- **Google Antigravity IDE:** Installed from the official Google Artifact Registry APT repository (`antigravity` package)
- **Android Studio:** Installed in `/opt/android-studio` with desktop launcher integration

### AI Desktop Applications

- **Claude:** Configured as an isolated Chromium desktop web application (`https://claude.ai`)
- **ChatGPT:** Configured as an isolated Chromium desktop web application (`https://chatgpt.com`)
- **Google Gemini:** Configured as an isolated Chromium desktop web application (`https://gemini.google.com`)

### AI Command-Line Tools

- **Claude Code:** `@anthropic-ai/claude-code` installed globally via npm
- **OpenAI Codex CLI:** `@openai/codex` installed globally via npm
- **Antigravity CLI (`agy`):** Installed system-wide in `/usr/local/bin/agy` via the official installer (`https://antigravity.google/cli/install.sh`)

### Containers and Virtualization

- **Docker Engine:** `docker-ce`, Buildx, and Compose plugins enabled at boot
- **Dev Containers:** `@devcontainers/cli` installed globally, with a starter `.devcontainer/devcontainer.json` template in `/etc/skel`
- **KVM and Libvirt:** `qemu-kvm` and `libvirt-daemon-system` configured so new users belong to the `docker`, `kvm`, `libvirt`, and `plugdev` groups automatically

## Repository Layout

```text
devai-os/
├── .github/
│   └── workflows/
│       └── build-and-test-iso.yml   # CI/CD build, QEMU boot test, and GitHub Release workflow
├── config/
│   ├── archives/                    # External APT repository definitions (Antigravity, Docker, Microsoft, VS Code)
│   ├── bootloaders/grub-pc/         # GRUB overrides: 5s autoboot timeout and ttyS0 serial console
│   ├── hooks/
│   │   └── normal/                  # Chroot installation and system configuration hooks
│   ├── includes.chroot/             # Global PATH profile and /etc/skel defaults
│   └── package-lists/               # Debian base, KVM, Docker, and core SDK package lists
├── scripts/
│   ├── package_release.py           # Splits large ISOs and generates SHA256SUMS.txt
│   └── verify_iso_boot.py           # Boots the ISO in headless QEMU and verifies tools
├── tests/
│   ├── test_iso_boot.py             # Unit tests for QEMU runner and serial output parser
│   └── test_package_release.py      # Unit tests for ISO splitter and checksum generator
├── build.sh                         # Main live-build configuration and build entrypoint
├── Makefile                         # Local development, build, and verification targets
└── pyproject.toml                   # Configuration for uv, Ruff, Mypy, and Pytest
```

## Local Build Prerequisites

Run builds on a Debian 13 or Ubuntu 24.04 x86_64 host with at least 40 GB of free disk space.

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  live-build \
  debootstrap \
  squashfs-tools \
  xorriso \
  isolinux \
  syslinux-common \
  grub-pc-bin \
  grub-efi-amd64-bin \
  mtools \
  dosfstools \
  qemu-system-x86 \
  qemu-utils \
  ovmf \
  shellcheck \
  python3
```

Install `uv` to manage Python linting and test dependencies:

```bash
curl -LsSf [https://astral.sh/uv/install.sh](https://astral.sh/uv/install.sh) | sh
uv sync --group dev
```

## Local Build and Verification Commands

Use the `Makefile` targets to lint scripts, verify download URLs, build the ISO, and test the live
environment:

- Verify external download and APT repository URLs are reachable:

  ```bash
  make check-urls
  ```

- Run linters, URL reachability checks, type checks, and unit tests:

  ```bash
  make check
  ```

- Build the bootable hybrid ISO into `artifacts/`:

  ```bash
  make build-iso
  ```

- Boot the ISO in headless UEFI QEMU and verify all binaries, `flutter doctor`, Docker and KVM:

  ```bash
  make test-iso
  ```

- Split the ISO into 1900 MiB release chunks and generate `SHA256SUMS.txt`:

  ```bash
  make package-release TAG=v1.0.0
  ```

- Remove staged artifacts and purge the `live-build` cache:

  ```bash
  make clean
  ```

## Default Live Session Credentials

- Username: `user`
- Password: `live`

## Automated CI/CD and Release Workflow

GitHub Actions automates verification and publishing in `.github/workflows/build-and-test-iso.yml`:

1. *Pre-Flight Checks (`unit-tests`):* Runs `shellcheck` on `build.sh` and all chroot hooks, checks external download links via `make check-urls`, and runs `ruff`, `mypy --strict`, and `pytest` via `uv`.
2. *ISO Build and QEMU Smoke Test (`build-and-boot-test`):* Builds the Debian 13 ISO, boots it in headless QEMU with OVMF UEFI firmware, logs into the live serial console, verifies `--version` output for every installed language and AI CLI, confirms `flutter doctor` Android licenses, and tests unprivileged `docker` and `/dev/kvm` access.
3. *GitHub Release Publishing (`publish-release`):* Pushing a version tag matching `v*` triggers `scripts/package_release.py` to split the verified ISO into 1900 MiB parts and publish them with `SHA256SUMS.txt` to GitHub Releases.

## Reassembling a Split Release ISO

Download all `.part-*` files and `SHA256SUMS.txt` from the release page into one directory, then run:

```bash
cat devai-os-v1.0.0.iso-part-* > devai-os-v1.0.0.iso
sha256sum -c SHA256SUMS.txt --ignore-missing
```
