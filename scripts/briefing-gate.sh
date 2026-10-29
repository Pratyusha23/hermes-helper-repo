#!/usr/bin/env bash
# Mode C: wake the agent only when something moved more than the threshold.
# Use with: hermes cron create ... --script briefing-gate.sh
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODE=gate MOVE_THRESHOLD="${MOVE_THRESHOLD:-1.5}" exec "$here/commodities.sh"
