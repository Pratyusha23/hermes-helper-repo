#!/usr/bin/env bash
#
# Pre-run script for the Hermes "canary-fix" cron job.
#
# Wakes the agent only when the canary workflow has an open issue -- which is
# almost never. On every other tick this prints {"wakeAgent": false} and no
# model is called, exactly like the commodities gate.
#
# When it does wake, it hands the agent the issue plus the diagnosis artifact
# from the failing run: probe.json and the first 4 KB of each response body.
# That artifact is the reason the GitHub token exists.
#
# Environment:
#   CANARY_REPO   owner/name of the repo running the workflow  (required)
#   GH_BIN        path to the gh binary      (default: found on PATH, then ~/.local/bin/gh)
#   GH_TOKEN_FILE path to the token          (default: ~/.hermes/secrets/gh_token)
#   WORK_DIR      scratch directory          (default: ~/.hermes/work/canary)

set -euo pipefail

repo="${CANARY_REPO:?set CANARY_REPO=owner/name}"
token_file="${GH_TOKEN_FILE:-$HOME/.hermes/secrets/gh_token}"
work="${WORK_DIR:-$HOME/.hermes/work/canary}"

# The scheduler does not source your interactive shell profile, so a gh
# installed into ~/.local/bin works when you type it and is missing at 08:23.
# Resolve it here rather than trusting PATH.
GH="${GH_BIN:-$(command -v gh 2>/dev/null || true)}"
[ -x "${GH:-}" ] || GH="$HOME/.local/bin/gh"
if [ ! -x "$GH" ]; then
  echo "gh not found (looked on PATH and in $HOME/.local/bin)" >&2
  echo '{"wakeAgent": false}'
  exit 0
fi

# A file read deliberately, not an exported variable: env vars show up in ps,
# in shell history, and in anything that dumps the environment.
GH_TOKEN="$(cat "$token_file")"
export GH_TOKEN

mkdir -p "$work"

issue=$("$GH" issue list --repo "$repo" --label canary --state open \
          --limit 1 --json number,title,body,url 2>/dev/null || echo '[]')

if [ "$(printf '%s' "$issue" | jq 'length')" -eq 0 ]; then
  # No open issue: drop any markers so a future regression is handled afresh.
  rm -rf "$work/handled" 2>/dev/null || true
  echo '{"wakeAgent": false}'
  exit 0
fi

number=$(printf '%s' "$issue" | jq -r '.[0].number')
markers="$work/handled"
mkdir -p "$markers"

# The issue stays open until a later canary run passes, which needs a human to
# merge the fix. Without a marker the agent would wake every morning in the
# meantime and redo work it has already done. The AGENT writes this file as its
# last step, once it has reached a conclusion -- so a run that dies halfway
# leaves no marker and is retried tomorrow, which is the behaviour we want.
if [ -e "$markers/$number" ]; then
  echo '{"wakeAgent": false}'
  exit 0
fi

# Fetch the evidence. Never guess at a new upstream format when the bytes that
# broke the parser are one download away.
run=$("$GH" run list --repo "$repo" --workflow canary.yml --status failure \
        --limit 1 --json databaseId --jq '.[0].databaseId' 2>/dev/null || true)

if [ -n "${run:-}" ]; then
  # Only clear what we have once we know we can replace it. A failed download
  # must not leave the agent with less than it started with -- and a diagnosis
  # seeded by examples/fake-breakage.sh survives when there is no failed run.
  rm -rf "$work/diagnosis"
  mkdir -p "$work/diagnosis"
  "$GH" run download "$run" --repo "$repo" --dir "$work/diagnosis" \
    >/dev/null 2>&1 || true
fi

probe_file=$(find "$work/diagnosis" -name probe.json 2>/dev/null | head -1)
probe='null'
[ -n "$probe_file" ] && probe=$(cat "$probe_file")

# The raw bodies stay on disk rather than going into the context: they can be
# large, and the agent should read only the one it needs.
jq -nc \
  --argjson issue "$issue" \
  --argjson probe "$probe" \
  --arg dir "$(dirname "${probe_file:-$work/diagnosis/none}")" \
  --arg marker "$markers/$number" \
  '{wakeAgent: true,
    context: {issue: $issue[0], probe: $probe, diagnosis_dir: $dir,
              marker_file: $marker}}'
