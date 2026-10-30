#!/usr/bin/env bash
#
# Rehearse the canary-fix job without waiting for a real outage.
#
# Creates the `canary` issue the workflow would create, and seeds a diagnosis
# directory holding a probe.json and a raw body with a plausibly-changed Yahoo
# response -- `regularMarketPrice` renamed. That is a realistic break: the
# field moves, the parser raises KeyError, and the fix is one line once you
# have actually read the body.
#
#   bash examples/fake-breakage.sh          # set it up
#   hermes cron run canary-fix              # watch the job work
#   bash examples/fake-breakage.sh --clean  # close the issue, remove the files
#
# Rehearse before you trust it. An unattended agent that has never been
# watched doing the job is a guess, and the first real break at 3am is a bad
# time to discover the gate never fires.

set -euo pipefail

repo="${CANARY_REPO:?set CANARY_REPO=owner/name}"
work="${WORK_DIR:-$HOME/.hermes/work/canary}"
GH="${GH_BIN:-$(command -v gh 2>/dev/null || echo "$HOME/.local/bin/gh")}"

if [ "${1:-}" = "--clean" ]; then
  failed=0
  for n in $("$GH" issue list --repo "$repo" --label canary --state open \
               --limit 10 --json number --jq '.[].number'); do
    # Close WITHOUT a comment: commenting needs Issues:write, and a token that
    # lacks it would fail the whole call and leave the issue open. Closing is
    # the part that matters -- an open canary issue wakes the agent daily.
    if "$GH" issue close "$n" --repo "$repo" 2>/dev/null; then
      echo "closed #$n"
    else
      echo "FAILED to close #$n" >&2
      failed=1
    fi
  done

  rm -rf "$work/diagnosis" 2>/dev/null \
    || { echo "FAILED to remove $work/diagnosis" >&2; failed=1; }

  still_open=$("$GH" issue list --repo "$repo" --label canary --state open \
                 --limit 10 --json number --jq 'length' 2>/dev/null || echo "?")

  if [ "$failed" -ne 0 ] || [ "$still_open" != "0" ]; then
    cat >&2 <<EOF

NOT clean. $still_open canary issue(s) still open.

An open canary issue wakes the agent on every scheduled run, so this is not
cosmetic. Close it in the web UI, or give the token Issues: Read and write
(Settings -> Developer settings -> Fine-grained tokens) and run this again.
EOF
    exit 1
  fi

  echo "cleaned"
  exit 0
fi

dir="$work/diagnosis/diagnosis-rehearsal"
mkdir -p "$dir"

# A Yahoo body that is valid JSON and no longer has the field we read.
cat > "$dir/yahoo-GCF.raw" <<'JSON'
{"chart":{"result":[{"meta":{
  "currency":"USD","symbol":"GC=F","exchangeName":"CMX",
  "regularMarketPriceV2":{"raw":3998.4,"fmt":"3,998.40"},
  "chartPreviousClose":4038.1,
  "priceHint":2
}}],"error":null}}
JSON

cat > "$dir/probe.json" <<JSON
{
  "asof": "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)",
  "all_ok": false,
  "every_symbol_usable": true,
  "records": [
    {"label": "Gold (COMEX, \$/oz)", "provider": "yahoo", "symbol": "GC=F",
     "ok": false, "stage": "parse", "status": 200, "bytes": 211,
     "error": "KeyError: 'regularMarketPrice'", "raw_file": "yahoo-GCF.raw",
     "parsed": null},
    {"label": "Gold (COMEX, \$/oz)", "provider": "stooq", "symbol": "xauusd",
     "ok": true, "stage": "ok", "status": 200,
     "parsed": {"last": 3998.4, "prev": 4038.1, "pct": -0.98}}
  ]
}
JSON

body='**Offline suite passed, live probe failed.** Our logic is unchanged and
still self-consistent, so an upstream response changed shape. Fix the parser to
match what the `diagnosis` artifact actually contains — do not guess at the new
format.

| check | result |
|---|---|
| output contract | `success` |
| offline suite | `success` |
| live probe | `failure` |

### What failed

```
Gold (COMEX, $/oz)  yahoo  parse  KeyError: '"'"'regularMarketPrice'"'"'
```

_Rehearsal issue created by examples/fake-breakage.sh._'

"$GH" label create canary --repo "$repo" \
  --description "Automated data-source check" --color B60205 2>/dev/null || true

"$GH" issue create --repo "$repo" --title "Data source check failing" \
  --label canary --body "$body"

echo
echo "seeded $dir"
echo "now run:  hermes cron run canary-fix"
echo "clean up: bash examples/fake-breakage.sh --clean"
