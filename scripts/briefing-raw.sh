#!/usr/bin/env bash
# Mode B: full report JSON as context; the agent writes the briefing.
# Use with: hermes cron create ... --script briefing-raw.sh
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODE=raw exec "$here/commodities.sh"
