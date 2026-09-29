#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
exec "$root/chapters/support/launch" "$root/factory" "$@"
