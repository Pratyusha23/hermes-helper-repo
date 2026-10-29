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
