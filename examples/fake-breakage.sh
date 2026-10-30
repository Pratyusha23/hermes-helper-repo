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
#   bash examples/fake-breakage.sh              # issue + diagnosis only
#   bash examples/fake-breakage.sh --with-break  # ALSO break the suite
#   bash examples/fake-breakage.sh --break-live  # full end-to-end, no fake issue
#   hermes cron run canary-fix                   # watch the job work
#   bash examples/fake-breakage.sh --clean       # undo all of it
#
# Without --with-break the parser still works, so a careful agent will read the
# body, run the suite, find nothing wrong and report a stale issue. That is the
# CORRECT answer, and it exercises the gate, the context and the marker -- but
# not the fix path.
#
# --with-break moves the Yahoo fixture to the V2 shape, leaving the parser on
# the old key. The suite goes red and the agent has genuine work.
#
# --break-live is the full chain, with no faked issue at all. It moves the
# parser AND the fixture to V2, so the offline suite stays GREEN and only the
# live probe fails -- which is precisely the "upstream changed shape" signal.
# Commit and push it, and the workflow opens the issue by itself:
#
#   bash examples/fake-breakage.sh --break-live
#   git commit -am "rehearsal: pretend Yahoo moved to V2" && git push
#   # watch Actions -> probe fails -> issue opens
#   hermes cron run canary-fix
#   # review the PR, merge it, next canary run closes the issue
#
# That costs a real commit on your default branch, undone by merging the
# agent's PR (or `git revert`). It is the only way to exercise the workflow,
# the issue, the artifact download and the fix in one pass.
#
# Rehearse before you trust it. An unattended agent that has never been
# watched doing the job is a guess, and the first real break at 3am is a bad
# time to discover the gate never fires.

set -euo pipefail

# CANARY_REPO must be available to an unattended cron run, which has no
# interactive shell and no exported variables. A config file written by
# `make install` is the reliable source; the environment can still override it
# for a one-off.
conf="${CANARY_CONF:-$HOME/.hermes/canary.conf}"
# shellcheck disable=SC1090
[ -z "${CANARY_REPO:-}" ] && [ -r "$conf" ] && . "$conf"

if [ -z "${CANARY_REPO:-}" ]; then
  cat >&2 <<EOF
CANARY_REPO is not set and $conf does not define it.

Fix it once, permanently:
    cd <your clone> && make install

That writes $conf from the clone's git remote, so the cron job
finds it too. For a one-off instead:
    CANARY_REPO=owner/name $0
EOF
  exit 2
fi
repo="$CANARY_REPO"
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

  rm -rf "${WORK_DIR:-$HOME/.hermes/work/canary}/handled" 2>/dev/null || true

  # Undo --with-break if it was used. Only touches this one tracked file.
  for f in tests/fixtures.py scripts/quotes.py; do
    git diff --quiet -- "$f" 2>/dev/null && continue
    if git checkout -- "$f" 2>/dev/null; then
      echo "reverted $f"
    else
      echo "FAILED to revert $f -- check git diff" >&2
      failed=1
    fi
  done

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

if [ "${1:-}" = "--break-live" ]; then
  # Move BOTH the parser and the fixture to the V2 shape. The offline suite
  # agrees with itself and stays green; the live endpoint still sends
  # regularMarketPrice, so only the probe fails. Offline pass + probe fail is
  # the signal that means "upstream changed", which is the whole point.
  python3 - <<'PATCH'
import pathlib, sys

q = pathlib.Path("scripts/quotes.py")
s = q.read_text()
old = '    previous = meta.get("chartPreviousClose") or meta.get("previousClose")'
if old not in s:
    sys.exit("could not find the line to break in scripts/quotes.py")
s = s.replace('_move(float(meta["regularMarketPrice"]),',
              '_move(float(meta["regularMarketPriceV2"]["raw"]),')
q.write_text(s)

f = pathlib.Path("tests/fixtures.py")
t = f.read_text()
t = t.replace('    meta = {"regularMarketPrice": price}',
              '    meta = {"regularMarketPriceV2": {"raw": price, "fmt": str(price)}}')
f.write_text(t)
PATCH
  echo "parser and fixture both moved to the V2 shape."
  echo "  offline suite: should stay GREEN   -> make test"
  echo "  live probe:    should now FAIL     -> make probe"
  echo
  echo "Next:  git commit -am 'rehearsal: pretend Yahoo moved to V2' && git push"
  echo "       then watch Actions open the issue by itself."
  exit 0
fi

if [ "${1:-}" = "--with-break" ]; then
  # Make parse_yahoo read a field the seeded body does not have, so the suite
  # genuinely fails and the fix is a real one-line change.
  # Move the FIXTURE to the new shape, leaving the parser on the old key.
  # That is the real direction of a format change: upstream moves first and
  # the code lags. The fix is to update parse_yahoo, which is exactly the
  # work we want to watch the agent do.
  python3 - <<'PATCH'
import pathlib, sys
p = pathlib.Path("tests/fixtures.py")
s = p.read_text()
old = '    meta = {"regularMarketPrice": price}'
if old not in s:
    sys.exit("could not find the line to break in tests/fixtures.py")
p.write_text(s.replace(
    old,
    '    # REHEARSAL BREAK: Yahoo moved the price into a nested object.\n'
    '    meta = {"regularMarketPriceV2": {"raw": price, "fmt": str(price)}}'))
PATCH
  echo "moved the Yahoo fixture to the V2 shape; 'make test' should now fail"
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
