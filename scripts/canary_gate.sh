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
#   GH_TOKEN_FILE path to the token                            (default: ~/.hermes/secrets/gh_token)

set -euo pipefail

repo="${CANARY_REPO:?set CANARY_REPO=owner/name}"
token_file="${GH_TOKEN_FILE:-$HOME/.hermes/secrets/gh_token}"
work="${WORK_DIR:-$HOME/.hermes/work/canary}"

# A file read deliberately, not an exported variable: env vars show up in ps,
# in shell history, and in anything that dumps the environment.
GH_TOKEN="$(cat "$token_file")"
export GH_TOKEN

mkdir -p "$work"

issue=$(gh issue list --repo "$repo" --label canary --state open \
          --limit 1 --json number,title,body,url 2>/dev/null || echo '[]')

if [ "$(printf '%s' "$issue" | jq 'length')" -eq 0 ]; then
  echo '{"wakeAgent": false}'
  exit 0
fi

# Fetch the evidence. Never guess at a new upstream format when the bytes that
# broke the parser are one download away.
rm -rf "$work/diagnosis" 2>/dev/null || true
run=$(gh run list --repo "$repo" --workflow canary.yml --status failure \
        --limit 1 --json databaseId --jq '.[0].databaseId' 2>/dev/null || true)

if [ -n "${run:-}" ]; then
  gh run download "$run" --repo "$repo" --dir "$work/diagnosis" \
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
  --arg dir "$work/diagnosis" \
  '{wakeAgent: true,
    context: {issue: $issue[0], probe: $probe, diagnosis_dir: $dir}}'
