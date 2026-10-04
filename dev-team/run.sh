#!/usr/bin/env bash
# HOST: launch the Bayto build team in a sandbox with THIS repo mounted read-write.
# Usage: [ROSTER=file] dev-team/run.sh <sandbox-name>     e.g. dev-team/run.sh bayto-dev
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
# Seats and briefs come from dev-team/roster.txt + the roles/ catalog, not the workshop's fixed coordinator/developer/qa.
command -v uv >/dev/null || { echo "uv is required (https://docs.astral.sh/uv/)"; exit 2; }
gen="$build/generated"
uv run --no-project --quiet --with pyyaml --with pydantic "$here/build-roster.py" "$gen" "${ROSTER:-$here/roster.txt}"
rm -f "$build/chapters/support/roles/"*.md
cp "$gen/roles/"*.md "$build/chapters/support/roles/"
install -m 0755 "$repo/agent-comms/src/acp/handoff_compat.py" "$build/chapters/support/bin/handoff"
install -m 0755 "$repo/agent-comms/src/acp/crew_notify_fallback.py" "$build/chapters/support/bin/crew-notify-fallback"
# The workshop blanks GH_TOKEN/GITHUB_TOKEN for seats; Bayto engineers open PRs, so let them inherit the proxy-managed placeholder.
sed -i.bak "s/--env GH_TOKEN= --env GITHUB_TOKEN= //" "$build/chapters/support/bin/start-team" && rm "$build/chapters/support/bin/start-team.bak"
grep -q -- "--env GH_TOKEN=" "$build/chapters/support/bin/start-team" && { echo "start-team still blanks GH_TOKEN"; exit 1; } || true
# M1.9: the workshop never passes tool permissions to a session. For harness=claude seats, start-team
# reads the seat's flags from $FACTORY_DIR/tool-flags/<seat> (written by build-roster.py from tools.json;
# the sandbox has no `acp` CLI or its dependencies) and refuses to start a seat that has none, rather
# than silently launching it unrestricted. --add-dir keeps ~/work (briefs, factory) readable under dontAsk.
sed -i.bak '/if \[ -z "\$ws" \]; then/i\
  if [ "$harness" = claude ]; then\
    [ -s "$FACTORY_DIR/tool-flags/$role" ] || { echo "No tool-permission flags for $role ($FACTORY_DIR/tool-flags/$role); refusing to start it unrestricted." >&2; exit 2; }\
    while IFS= read -r flag; do args+=("$flag"); done < "$FACTORY_DIR/tool-flags/$role"\
    args+=(--add-dir "$WORKSPACE_DIR")\
  fi' "$build/chapters/support/bin/start-team" && rm "$build/chapters/support/bin/start-team.bak"
grep -q -- 'tool-flags/\$role"' "$build/chapters/support/bin/start-team" \
  || { echo "start-team missing the tool-permission patch"; exit 1; }
# The launcher only ships bin/, roles/ and a fixed set of files; add tool-flags/ so it lands in the sandbox factory dir.
sed -i.bak '/^cp "\$chapter\/PROMPT.md" "\$payload\/"$/a\
[ ! -d "$chapter/tool-flags" ] || { mkdir -p "$payload/factory"; cp -R "$chapter/tool-flags" "$payload/factory/"; }' "$build/chapters/support/launch" && rm "$build/chapters/support/launch.bak"
grep -q -- 'cp -R "\$chapter/tool-flags"' "$build/chapters/support/launch" \
  || { echo "launch missing the tool-flags transfer"; exit 1; }
cp "$here/PROMPT.md" "$build/factory/PROMPT.md"
cp "$gen/team.tsv" "$build/factory/team.tsv"
cp "$gen/tools.json" "$build/factory/tools.json"
cp -R "$gen/tool-flags" "$build/factory/tool-flags"
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
echo "Mounting $repo read-write. Engineers commit to sbx/<task> branches, push them and open PRs (GitHub secret needed, see dev-team/README.md)"
exec "$build/scripts/launch-factory.sh" "$name" "$repo"
