#!/usr/bin/env bash
#
# Pre-run script for the Hermes "commodities" cron job.
#
# One script, three output modes, chosen with MODE:
#
#   MODE=raw    (default)  the full report JSON. For a Mode B cron job, where
#                          the agent writes the briefing from this context.
#   MODE=plain             a finished plain-text briefing. For a --no-agent
#                          cron job: no model is called, so this costs nothing.
#   MODE=gate              {"wakeAgent": false} on a quiet day, or
#                          {"wakeAgent": true, "context": {...}} when something
#                          moved more than MOVE_THRESHOLD percent.
#
# Environment:
#   MODE              raw | plain | gate            (default: raw)
#   MOVE_THRESHOLD    percent, MODE=gate only       (default: 1.5)
#   FIXTURE           read a saved report instead of the network, for testing
#   WORK_DIR          where report.json is kept     (default: ~/.hermes/work/commodities)
#
# Hermes refuses `python3 -c` and `bash -c` inside cron runs, because nobody
# is present to approve them. Everything here lives in a file for that reason.

set -euo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

mode="${MODE:-raw}"
threshold="${MOVE_THRESHOLD:-1.5}"
work_dir="${WORK_DIR:-$HOME/.hermes/work/commodities}"
report="$work_dir/report.json"

mkdir -p "$work_dir"

# Absolute paths throughout: the scheduler does not source your interactive
# shell profile, so a PATH you rely on interactively may not be there.
if [ -n "${FIXTURE:-}" ]; then
  python3 "$here/quotes.py" --fixture "$FIXTURE" > "$report"
else
  python3 "$here/quotes.py" > "$report"
fi

case "$mode" in
  raw)
    cat "$report"
    ;;

  plain)
    jq -r -f "$here/format_plain.jq" "$report"
    ;;

  gate)
    # The cheap deterministic script decides whether there is work. The model
    # does the work. Inverting those is how a scheduled agent quietly runs up
    # a bill doing nothing.
    jq -c --argjson threshold "$threshold" '
      if .biggest_move < $threshold
      then {wakeAgent: false}
      else {wakeAgent: true, context: .}
      end' "$report"
    ;;

  *)
    echo "unknown MODE: $mode (expected raw, plain or gate)" >&2
    exit 2
    ;;
esac
