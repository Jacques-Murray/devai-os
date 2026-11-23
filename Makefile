SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

TAG ?= dev
ARTIFACTS_DIR ?= artifacts
RELEASE_DIR ?= release-assets
ISO_PATH ?= $(ARTIFACTS_DIR)/devai-os-$(TAG).iso
LOG_PATH ?= $(ARTIFACTS_DIR)/qemu-boot.log
NOTES_PATH ?= release-notes.md
TIMEOUT ?= 300
CHUNK_MIB ?= 1900

.PHONY: help lint test check build-iso test-iso package-release clean

help: ## Show available targets and default variables
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

lint: ## Run shellcheck, ruff, and mypy strict checks
	shellcheck -x build.sh config/hooks/normal/*.hook.chroot
	uv run ruff check scripts tests
	uv run ruff format --check scripts tests
	uv run mypy scripts tests

test: ## Run pytest unit test suite
	uv run pytest -v tests

check: lint test ## Run all linters, type checks, and unit tests

build-iso: ## Build the Debian live-build ISO and stage it in artifacts/
	chmod +x build.sh scripts/verify_iso_boot.py scripts/package_release.py config/hooks/normal/*.hook.chroot
	sudo ./build.sh
	mkdir -p "$(ARTIFACTS_DIR)"
	@BUILT_ISO=$$(find . -maxdepth 1 \( -name "*.hybrid.iso" -o -name "*.iso" \) | head -n 1); \
	if [ -z "$$BUILT_ISO" ]; then \
		echo "Error: No ISO file produced by live-build." >&2; \
		exit 1; \
	fi; \
	sudo mv "$$BUILT_ISO" "$(ISO_PATH)"; \
	sudo chown "$$(id -u):$$(id -g)" "$(ISO_PATH)"; \
	echo "Staged ISO at $(ISO_PATH)"

test-iso: ## Boot the staged ISO in headless QEMU and verify tools
	@if [ ! -f "$(ISO_PATH)" ]; then \
		echo "Error: ISO not found at $(ISO_PATH). Run 'make build-iso' first." >&2; \
		exit 1; \
	fi
	@KVM_FLAG=""; \
	if [ ! -e /dev/kvm ]; then \
		KVM_FLAG="--allow-missing-kvm"; \
	fi; \
	python3 scripts/verify_iso_boot.py \
		--iso "$(ISO_PATH)" \
		--log "$(LOG_PATH)" \
		--timeout "$(TIMEOUT)" \
		$$KVM_FLAG

package-release: ## Split the ISO into release parts and generate SHA256SUMS.txt
	@if [ ! -f "$(ISO_PATH)" ]; then \
		echo "Error: ISO not found at $(ISO_PATH). Run 'make build-iso' first." >&2; \
		exit 1; \
	fi
	python3 scripts/package_release.py \
		--iso "$(ISO_PATH)" \
		--output-dir "$(RELEASE_DIR)" \
		--tag "$(TAG)" \
		--notes-out "$(NOTES_PATH)" \
		--chunk-mib "$(CHUNK_MIB)"

clean: ## Remove staged artifacts, release chunks, and live-build cache
	rm -rf "$(ARTIFACTS_DIR)" "$(RELEASE_DIR)" "$(NOTES_PATH)"
	rm -f config/archives/*.key.chroot
	if command -v lb >/dev/null 2>&1; then \
		sudo lb clean --purge; \
	fi