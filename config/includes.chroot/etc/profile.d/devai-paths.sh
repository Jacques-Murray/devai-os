export CARGO_HOME="/opt/rust/cargo"
export RUSTUP_HOME="/opt/rust/rustup"
export FNM_DIR="/opt/fnm"
export FLUTTER_ROOT="/opt/flutter"
export KOTLIN_HOME="/opt/kotlinc"
export ANDROID_HOME="/opt/android-sdk"
export ANDROID_SDK_ROOT="${ANDROID_HOME}"

export PATH="/usr/local/bin:${CARGO_HOME}/bin:${FLUTTER_ROOT}/bin:${KOTLIN_HOME}/bin:${ANDROID_HOME}/cmdline-tools/latest/bin:${ANDROID_HOME}/platform-tools:${FNM_DIR}:${PATH}"

if command -v fnm >/dev/null 2>&1; then
    eval "$(fnm env --use-on-cd)"
fi