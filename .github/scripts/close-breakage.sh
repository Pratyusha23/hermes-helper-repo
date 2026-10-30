#!/usr/bin/env bash
#
# Close the canary issue once both checks pass again.
#
# Self-closing matters more than it sounds. An issue that only a human can
# close stays open after the real fix lands, and the canary-fix cron job keeps
# waking up to work on a problem that no longer exists.

set -euo pipefail

LABEL="canary"

open=$(gh issue list --label "$LABEL" --state open --limit 10 \
         --json number --jq '.[].number')

for number in $open; do
  gh issue close "$number" \
    --comment "Both checks passed on a later run. Closing automatically."
  echo "closed issue #$number"
done

[ -n "$open" ] || echo "no open $LABEL issue"
