#!/usr/bin/env bash
#
# The three cron jobs from the article. Pick one; don't run all three.
#
# Hermes looks for pre-run scripts by filename in ~/.hermes/scripts/, so
# install first:
#
#   make install
#
# Each mode has its own thin wrapper script, because the mode has to be set
# when the *scheduled run* happens, not when you create the job. Writing
# `MODE=plain hermes cron create ...` sets the variable for the create command
# and nothing else -- the job would then run in the default mode every
# morning. The wrappers bake it in.
#
# Check the timezone before you schedule anything. Cron expressions are
# interpreted in the box's local time, and a server that thinks it is in UTC
# will deliver your 7am briefing at 12:30pm:
#
#   sudo timedatectl set-timezone Asia/Kolkata

set -euo pipefail


# --------------------------------------------------------------- Mode A: plain
# No model is called. The script's stdout is delivered verbatim, so this costs
# nothing to run forever. For a fixed table of numbers it is the better
# engineering choice -- ask whether the job needs a model before giving it one.

hermes cron create "0 7 * * *" "Daily commodities briefing" \
  --name commodities \
  --no-agent \
  --script briefing-plain.sh \
  --deliver telegram


# ------------------------------------------------------------ Mode B: the agent
# What the model adds is the part jq cannot do: reading the headlines and
# telling you *why* gold moved, and noticing when headlines and prices
# disagree.
#
# Three things in this prompt are load-bearing, and all three come from
# briefings that came out wrong:
#
#   "Only use numbers present in the context" -- without it, a model that
#   fetched four of five symbols will supply the fifth from memory. The number
#   looks plausible and is months stale.
#
#   "Say the move is unexplained rather than inventing a reason" -- markets
#   move on things that are not in six headlines. A model asked to explain
#   will always explain. Give it an explicit way to decline and it takes it.
#
#   "Plain text only" -- Telegram renders backticks as separate code blocks,
#   which shreds a briefing into confetti. The model will not infer this.

hermes cron create "0 7 * * *" \
  "You are writing my morning commodities briefing from the JSON in the
context. Structure it exactly so:

Line 1: the date.
Then one line per instrument: name, price, and overnight percent move.
Then one short paragraph, at most three sentences, connecting the headlines
to the moves. Name the specific driver if the headlines support one. If they
do not, say the move is unexplained by today's news rather than inventing a
reason.
If the errors list is non-empty, say which instruments are missing.

Only use numbers present in the context. Never estimate a price.
Plain text only: Telegram renders markdown and backticks badly." \
  --name commodities-ai \
  --script briefing-raw.sh \
  --deliver telegram


# ------------------------------------------------- Mode C: only when it matters
# Runs hourly, costs nothing on the hours when metals are flat, and wakes the
# agent only when something moved more than the threshold. Edit the default in
# scripts/briefing-gate.sh.
#
# The threshold is the whole design: too low and you have built a notification
# you will mute, too high and you hear about it from the news first.
#
# Minute 17, not minute 0. Everything in the world runs on the hour, including
# whatever else shares your model provider's rate limit.

hermes cron create "17 * * * *" \
  "Something moved sharply overnight. From the JSON in the context, say in at
most four lines what moved, by how much, and whether the headlines explain it.
If they do not, say so rather than inventing a reason. Only use numbers present
in the context. Plain text only." \
  --name commodities-alert \
  --script briefing-gate.sh \
  --deliver telegram


# ------------------------------------------------------------------ day to day
#
#   hermes cron list --all        # every job, including paused ones
#   hermes cron run commodities   # run it now, ignore the schedule
#   hermes cron pause commodities
#   hermes cron resume commodities
#   hermes logs
#
# `hermes cron run` is the one you will use most. A job that works by hand and
# fails on schedule is almost always an environment difference: the scheduler
# does not source your interactive shell profile. Use absolute paths.


# ------------------------------------------------ Canary: fix the scraper
# Gates on an open `canary` issue from .github/workflows/canary.yml. On every
# morning where nothing is broken this prints {"wakeAgent": false} and costs
# nothing.
#
# `make install` writes ~/.hermes/canary.conf from the clone's git remote, and
# the gate reads it. Do NOT write `CANARY_REPO=owner/name hermes cron create`:
# that sets the variable for the create command only, and the scheduled run at
# 08:23 would exit with "CANARY_REPO is not set" -- the same trap as MODE=.
#
# The token in ~/.hermes/secrets/gh_token needs Issues: Read and write,
# Actions: Read, and Pull requests: Read and write.

hermes cron create "23 8 * * *" \
  "The data-source canary is failing. The context has the issue, the probe
results, a directory of raw response bodies, and a marker_file path.

STEP 0 -- WHERE YOU ARE WORKING.
The git clone is at the repo_dir path in the context. cd there first. Every
file path below is relative to it. Do not search the filesystem for the repo
and do not clone it again; if repo_dir is empty or missing, stop and report
that instead of guessing.

STEP 1 -- READ THE EVIDENCE FIRST.
Read the probe to find which provider and which parse step failed. Then read
the matching .raw file in diagnosis_dir. Never change a parser for a format you
have not read. If the raw body shows the endpoint is gone rather than changed,
skip to step 5 and recommend a replacement instead of writing code.

Then check whether the code actually has this problem: run `make test` before
changing anything. If it already passes and the parser already handles the
shape in the raw body, there is nothing to fix -- the issue is stale. Say so in
step 5, change no code, and still mark handled. Do not invent a change to
justify the run.

STEP 2 -- BRANCH.
git fetch, then create canary-fix/<short-description> from the default branch.
Never commit to main.

STEP 3 -- FIX.
Make the smallest change to scripts/quotes.py that parses the shape you read in
step 1. No refactors, no new dependencies.

Then update tests/test_quotes.py: REPLACE the stale fixture for that provider
with the real new body. Do not keep the old one -- it describes a format that
no longer exists anywhere, and keeping it forces the parser to carry a dead
branch forever.

tests/test_contract.py is different and you must NOT edit it. It asserts the
shape of report.json, which format_plain.jq, the gate and this prompt all
depend on. Yahoo is allowed to change; report.json is not. If your fix cannot
satisfy that file, the fix is wrong: stop, push nothing, go to step 5.

STEP 4 -- PUSH ONLY IF THE SUITE PASSES.
Run: make test
Report the full count. Then:
  - all tests pass  -> commit and push the branch, then open a pull request
                       with gh pr create, titled for the provider and shape
                       change, describing what the raw body showed. Comment on
                       the issue with the PR link.
  - any test fails  -> push NOTHING. Try once more at most. If it still fails,
                       go to step 5 and report the failing test names verbatim.
Never merge. Never force-push. Never edit the workflow or this skill.

If the PUSH itself is rejected -- permissions, authentication, a protected
branch -- do not retry it and do not try another remote, another URL or
another credential. Leave the commit on the local branch, and in step 5
report the exact error and the branch name. Then DO mark handled: the work is
done and waiting, and a human has to fix the credential before anything can
change.

On a later run, before doing anything in step 3: if a canary-fix/* branch
already exists locally and its tests pass, the work is already done. Do not
redo it. Try the push and the pull request again, report the outcome, and
stop.

STEP 5 -- REPORT, THEN MARK HANDLED.
Comment on the issue, and send a short plain-text report: what broke, what the
raw body showed, what you changed, the test count, and the PR link or the
reason there is none. Telegram renders markdown badly, so plain sentences only.

As your VERY LAST action, and only once you have reached a conclusion, create
the empty file at the marker_file path from the context. That marker stops this
job waking again for the same issue every morning while the PR waits for
review. If you stop early or fail, do NOT create it -- the run should be
retried tomorrow." \
  --name canary-fix \
  --script canary_gate.sh \
  --deliver telegram
