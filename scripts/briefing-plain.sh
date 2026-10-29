#!/usr/bin/env bash
# Mode A: finished plain-text briefing, no model called.
# Use with: hermes cron create ... --no-agent --script briefing-plain.sh
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODE=plain exec "$here/commodities.sh"
