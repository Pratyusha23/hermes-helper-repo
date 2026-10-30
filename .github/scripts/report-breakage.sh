#!/usr/bin/env bash
#
# Open -- or update -- the single `canary` issue describing what broke.
#
# One issue, reused. A workflow that opens a fresh issue every morning trains
# you to ignore the label within a week.

set -euo pipefail

LABEL="canary"
TITLE="Data source check failing"

offline="${OFFLINE:-unknown}"
probe="${PROBE:-unknown}"
run_url="${RUN_URL:-}"

# The two-signal matrix is the whole reason both checks run.
if [ "$offline" = "failure" ] && [ "$probe" = "failure" ]; then
  verdict="**Both checks failed.** Almost certainly our own code — a bad commit
breaks the offline suite and the live probe together. Start with the most
recent change to \`scripts/\`, not with Yahoo."
elif [ "$offline" = "failure" ]; then
  verdict="**Offline suite failed, live probe passed.** The endpoints are fine;
our parsing logic is not self-consistent. This is a code problem introduced by
a commit, not an upstream change."
else
  verdict="**Offline suite passed, live probe failed.** Our logic is unchanged
and still self-consistent, so an upstream response changed shape. Fix the
parser to match what the \`diagnosis\` artifact actually contains — do not
guess at the new format."
fi

summary="(no probe output: the probe step did not get far enough)"
if [ -f diagnosis/probe.json ]; then
  summary=$(python3 - <<'PY'
import json, pathlib
data = json.loads(pathlib.Path("diagnosis/probe.json").read_text())
rows = [r for r in data["records"] if not r["ok"] and r["stage"] != "skipped"]
if not rows:
    print("every provider answered")
else:
    width = max(len(r["label"]) for r in rows)
    for r in rows:
        print(f"{r['label']:<{width}}  {r['provider']:<12}  "
              f"{r['stage']:<6}  {r['error']}")
    print()
    print(f"every symbol still usable via some provider: "
          f"{data['every_symbol_usable']}")
PY
)
fi

body=$(cat <<EOF
$verdict

| check | result |
|---|---|
| offline suite | \`$offline\` |
| live probe | \`$probe\` |

### What failed

\`\`\`
$summary
\`\`\`

### Evidence

The \`diagnosis\` artifact on [this run]($run_url) holds \`probe.json\` plus the
first 4 KB of every response body, including the ones that failed to parse.
That is what to read before changing a parser.

_Updated automatically by the canary workflow. Closes itself when both checks
pass again._
EOF
)

gh label create "$LABEL" --description "Automated data-source check" \
  --color B60205 2>/dev/null || true

existing=$(gh issue list --label "$LABEL" --state open --limit 1 \
             --json number --jq '.[0].number // empty')

if [ -n "$existing" ]; then
  gh issue comment "$existing" --body "$body"
  echo "updated issue #$existing"
else
  gh issue create --title "$TITLE" --label "$LABEL" --body "$body"
fi
