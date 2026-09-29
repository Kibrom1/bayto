#!/usr/bin/env bash
# Shared setup helpers; no host supervisor.
CONTROL_DIR="${FACTORY_CONTROL_DIR:-$ROOT/.local/chapters}"
BIN_DIR="$CONTROL_DIR/bin"
info() { printf '%s\n' "$*" >&2; }
ok() { info "$@"; }
warn() { info "$@"; }
error() { info "$@"; }
die() { error "$*"; exit 1; }
host_os() {
  case "$(uname -s)" in
    Darwin) printf 'Darwin' ;;
    Linux) printf 'Linux' ;;
    MINGW*|MSYS*|CYGWIN*) printf 'Windows' ;;
    *) die "unsupported operating system: $(uname -s)" ;;
  esac
}
host_arch() {
  case "$(uname -m)" in
    arm64|aarch64) printf 'arm64' ;;
    x86_64|amd64) printf 'amd64' ;;
    *) die "unsupported CPU architecture: $(uname -m)" ;;
  esac
}
executable_suffix() {
  local os
  os="$(host_os)" || return
  [ "$os" != Windows ] || printf '.exe'
}
beans_bin() { printf '%s' "$BIN_DIR/beans$(executable_suffix)"; }
beans_mcp_asset() {
  local os arch platform
  os="$(host_os)" || return
  arch="$(host_arch)" || return
  case "$os" in
    Darwin) platform=darwin ;;
    Linux) platform=linux ;;
    Windows)
      [ "$arch" = amd64 ] || die 'The published Windows adapter supports x64 hosts only.'
      platform=windows
      ;;
  esac
  printf 'beans-mcp_%s_%s_%s%s' "$BEANS_MCP_VERSION" "$platform" "$arch" "$(executable_suffix)"
}
sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    die 'SHA-256 verification requires sha256sum or shasum.'
  fi
}
require_cmd() {
  [ -x "$1" ] || command -v "$1" >/dev/null 2>&1 || die "$2 unavailable. Run $3"
}
copy_file() {
  mkdir -p "$(dirname "$2")"
  cat "$1" > "$2" || return 1
  cmp -s "$1" "$2"
}
