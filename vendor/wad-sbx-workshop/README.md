# Vendored: wad-sbx-workshop (subset)

Copied from https://github.com/Kibrom1/wad-sbx-workshop (fork of shelajev/wad-sbx-workshop, Apache-2.0, see LICENSE)
at commit 1ade233d919db590ceb56c92095ef4c891619b6c, plus your local fix to chapters/support/bin/prepare
(case-insensitive app path on macOS).

Kept: scripts (launch, Beans install/init), chapters/support (handoff, crew, crew-notify, start-team, roles),
chapters/kits, backlog/seed. Dropped: sample app, slides, MCP adapter, other chapters.
factory/ is a Claude-only, MODE=manual team (coordinator/developer/qa on claude-haiku) mounting ./workspace.
Beans installs into vendor/wad-sbx-workshop/.local (git-ignored).

W0.3 baseline (run from this directory):
  scripts/install-beans.sh
  scripts/backlog-init.sh --disposable
  scripts/launch-factory.sh bayto-w03 "$PWD/workspace"
Then in the sandbox shell: start-team; herdr agent list; crew ask "..."; crew watch
Cleanup: sbx env rm factory/sbxenv.yaml --env-arg name=bayto-w03
