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
- **Google Antigravity IDE:** Installed in `/opt/antigravity-ide` with desktop launcher integration
- **Android Studio:** Installed in `/opt/android-studio` with desktop launcher integration

### AI Desktop Applications

- **Claude Desktop:** Installed from the Anthropic Debian repository
- **ChatGPT Desktop:** Installed from the official OpenAI Debian package
- **Google Gemini:** Configured as an isolated Chromium desktop web application

### AI Command-Line Tools

- **Claude Code:** `@anthropic-ai/claude-code` installed globally via npm
- **OpenAI Codex CLI:** `@openai/codex` installed globally via npm
- **Antigravity CLI (`agy`):** Installed system-wide in `/usr/local/bin/agy`

### Containers and Virtualization

- **Docker Engine:** `docker-ce`, Buildx, and Compose plugins enabled at boot
- **Dev Containers:** `@devcontainers/cli` installed globally, with a starter `.devcontainer/devcontainer.json` template in `/etc/skel`
- **KVM and Libvirt:** `qemu-kvm` and `libvirt-daemon-system` configured so new users belong to the `docker`, `kvm`, `libvirt`, and `plugdev` groups automatically

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

curl -LsSf [https://astral.sh/uv/install.sh](https://astral.sh/uv/install.sh) | sh
uv sync --group dev

## Local Build and Verification Commands

- Run linters, type checks, and unit tests: `make check`
- Build the bootable hybrid ISO into `artifacts/`: `make build-iso`
- Boot the ISO in headless UEFI QEMU and verify all binaries, `flutter doctor`, Docker, and KVM: `make test-iso`
- Split the ISO into 1900 MiB release chunks and generate `SHA256SUMS.txt`: `make package-release TAG=v1.0.0`
- Remove staged artifacts and purge the `live-build` cache: `make clean`

## Default Live Session Credentials

- Username: `user`
- Password: `live`

## Reassembling a Split Release ISO

```bash
cat devai-os-v1.0.0.iso-part-* > devai-os-v1.0.0.iso
sha256sum -c SHA256SUMS.txt --ignore-missing
```
