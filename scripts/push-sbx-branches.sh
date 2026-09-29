#!/usr/bin/env bash
# HOST: review and push branches the sandbox team created (sbx/*). Never pushes main.
# Usage: scripts/push-sbx-branches.sh [--yes]   (without --yes it asks per branch)
set -euo pipefail
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
yes=0; [ "${1:-}" = "--yes" ] && yes=1
branches=$(git for-each-ref --format='%(refname:short)' 'refs/heads/sbx/*')
[ -n "$branches" ] || { echo "No sbx/* branches."; exit 0; }
for b in $branches; do
  echo "=== $b"; git log --oneline main..$b; git diff --stat main...$b | tail -5
  if git diff --name-only main...$b | grep -Eq '(^|/)(\.env|.*\.pem|.*credentials.*)$|^vendor/'; then
    echo "!! touches vendor/ or a secret-looking file: skipping $b"; continue; fi
  if [ $yes -eq 0 ]; then read -r -p "Push $b? [y/N] " a; [ "$a" = y ] || continue; fi
  git push -u origin "$b"
done
echo "Open pull requests on GitHub, then merge there."
