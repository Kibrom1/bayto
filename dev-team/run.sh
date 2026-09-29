#!/usr/bin/env bash
# HOST: launch the Bayto build team in a sandbox with THIS repo mounted read-write.
# Usage: dev-team/run.sh <sandbox-name>     e.g. dev-team/run.sh bayto-dev
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"
src="$repo/vendor/wad-sbx-workshop"
name="${1:?sandbox name (letters, digits, '.', '-')}"
[ -x "$src/.local/chapters/bin/beans" ] || { echo "Run vendor/wad-sbx-workshop/scripts/install-beans.sh and backlog-init.sh --disposable first"; exit 2; }
build="$here/.build"
rm -rf "$build"; mkdir -p "$build"
tar -C "$src" --exclude=.local -cf - . | tar -C "$build" -xf -
ln -s "$src/.local" "$build/.local"
cp "$here/roles/"*.md "$build/chapters/support/roles/"
cp "$here/PROMPT.md" "$build/factory/PROMPT.md"
cp "$here/team.tsv" "$build/factory/team.tsv"
printf 'TASK=wad-102\nMODE=manual\nUSE_ACR=0\nSESSION=shell\n' > "$build/factory/chapter.env"
# Claude-only, no Pi kit, no ACR kit; no ports.
cat > "$build/factory/sbxenv.yaml" <<'YML'
schemaVersion: "1"
name: "${{ env.args.name }}"
agent: claude
workspace: "${{ env.args.app }}"
args:
  name:
    required: true
  app:
    default: ../..
sandboxOptions:
  skills: off
  cpus: 4
  memory: 8g
kits:
  - source: ../chapters/kits/herdr
YML
git -C "$repo" switch main >/dev/null 2>&1 || true
echo "Mounting $repo read-write. Agents commit to sbx/<task> branches; push from the host with scripts/push-sbx-branches.sh"
exec "$build/scripts/launch-factory.sh" "$name" "$repo"
