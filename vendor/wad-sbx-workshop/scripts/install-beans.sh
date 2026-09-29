#!/usr/bin/env bash
# HOST: install the pinned Beans CLI into the workshop's own bin directory.
#
# Beans stays a separately pinned host dependency: this script downloads the upstream
# release and verifies its published checksum. It never installs into a system path and
# never needs root.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
. "$ROOT/scripts/common.sh"
# shellcheck disable=SC1091
. "$ROOT/scripts/versions.env"

mkdir -p "$BIN_DIR"
TARGET="$(beans_bin)"

if [ -x "$TARGET" ] && [ "${1:-}" != "--force" ]; then
  if installed_version="$("$TARGET" version 2>/dev/null)" && \
    [ "$(printf '%s' "$installed_version" | awk '{print $2}')" = "$BEANS_VERSION" ]; then
    info "beans already installed: $installed_version"
    exit 0
  fi
  warn "installed Beans is not runnable or is not the pinned version; replacing it"
fi

os="$(host_os)"
arch="$(host_arch)"
[ "$arch" != amd64 ] || arch=x86_64

ext=tar.gz
[ "$os" = "Windows" ] && ext=zip
asset="beans_${os}_${arch}.${ext}"
base="https://github.com/${BEANS_REPO}/releases/download/v${BEANS_VERSION}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

info "downloading $asset (beans v$BEANS_VERSION)"
curl -fsSL --retry 3 -o "$work/$asset" "$base/$asset" \
  || die "download failed: $base/$asset"
curl -fsSL --retry 3 -o "$work/checksums.txt" "$base/beans_${BEANS_VERSION}_checksums.txt" \
  || die "checksum download failed"

expected="$(awk -v n="$asset" '$2 == n {print $1}' "$work/checksums.txt")"
actual="$(sha256_file "$work/$asset")"
[ -n "$expected" ] && [ "$actual" = "$expected" ] \
  || die "checksum mismatch for $asset - refusing to install"
ok "checksum verified"

if [ "$ext" = "zip" ]; then unzip -qo "$work/$asset" -d "$work"; else tar xzf "$work/$asset" -C "$work"; fi
binname=beans
[ "$os" = "Windows" ] && binname=beans.exe
[ -f "$work/$binname" ] || die "release archive did not contain $binname"
install -m 0755 "$work/$binname" "$TARGET"
ok "installed $TARGET ($("$TARGET" version 2>&1 | head -1))"
