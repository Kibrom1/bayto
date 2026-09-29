#!/usr/bin/env bash
# HOST: build a debate copy of the vendored workshop (debate roles + prompt) and launch it.
# Usage: spikes/m0-debate/run.sh <sandbox-name>   (letters, digits, '.', '-' only)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
src="$repo/vendor/wad-sbx-workshop"
name="${1:?sandbox name, e.g. bayto-m0}"
build="$here/.build"
rm -rf "$build"; mkdir -p "$build"
# keep the Beans install/backlog (.local) out of the copy; symlink it so it is shared
tar -C "$src" --exclude=.local -cf - . | tar -C "$build" -xf -
[ ! -d "$src/.local" ] || ln -s "$src/.local" "$build/.local"
cp "$here/roles/"*.md "$build/chapters/support/roles/"
cp "$here/PROMPT.md" "$build/factory/PROMPT.md"
mkdir -p "$here/runs"
echo "Topics to paste after start-team:"; head -n1 "$here"/topics/*.txt
exec "$build/scripts/launch-factory.sh" "$name" "$build/workspace"
