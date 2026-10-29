# Hermes helper scripts — daily commodities briefing

Pre-run scripts for a [Hermes Agent](https://hermes-agent.nousresearch.com)
cron job that sends a gold and commodities briefing to Telegram every morning.

Companion code for *Run Your Own AI Agent for $5 a Month*.

No API keys. No dependencies beyond `python3`, `jq` and `curl`.

```
Commodities - Thu 29 Oct

Gold (COMEX, $/oz): 4120.5  (+0.84%)
Silver (COMEX, $/oz): 52.11  (-1.92%)
WTI crude ($/bbl): 61.44  (+0.07%)
USD/INR: 88.42  (+0.05%)

Headlines:
- Gold holds near record as rate-cut bets firm - Reuters
- Silver slips after three-day rally - Bloomberg
```

## The idea

A Hermes cron job has three pieces: a schedule, an optional **pre-run script**,
and a prompt. The script runs first, on every tick, with no model involved.
What happens next is up to the script — and that is where the cost of a
scheduled agent is decided.

**The cheap deterministic script decides whether there is work. The model does
the work.** Inverting those — letting the model check whether there is anything
to do — is how a scheduled agent quietly runs up a bill doing nothing.

One script here, three modes, each with a thin wrapper to use as `--script`:

| Wrapper | `MODE` | Output | Model called? |
|---|---|---|---|
| `briefing-plain.sh` | `plain` | a finished plain-text briefing | **no** — `--no-agent` sends stdout verbatim |
| `briefing-raw.sh` | `raw` | the full report JSON, as context | yes, every tick |
| `briefing-gate.sh` | `gate` | `{"wakeAgent": false}`, or `true` plus context | **only when something moved** |

The wrappers exist because the mode has to be set when the *scheduled run*
happens. `MODE=plain hermes cron create ...` sets it for the create command and
nothing else, and the job would quietly run in the default mode every morning.

`gate` is the interesting one. Hermes reads the *last line* of a pre-run
script's output and expects JSON: `{"wakeAgent": false}` skips the tick
entirely, and `{"wakeAgent": true, "context": {...}}` wakes the agent with that
context already in hand. So a two-line `jq` filter, not a language model,
decides whether today is worth paying for.

## Quick start

```bash
git clone <this repo> && cd hermes-helper-repo

make test     # offline test suite, no network
make demo     # all three modes, using the committed fixture
make check    # hit the live endpoints once, print the report
make install  # copy into ~/.hermes/scripts/
```

Then pick a cron job from [`examples/cron-commands.sh`](examples/cron-commands.sh):

```bash
hermes cron create "0 7 * * *" "Daily commodities briefing" \
  --name commodities --no-agent --script briefing-plain.sh --deliver telegram
```

**Set the box's timezone before you schedule anything.** Cron expressions are
interpreted in local time, and a server that thinks it is in UTC will deliver
your 7am briefing at 12:30pm:

```bash
sudo timedatectl set-timezone Asia/Kolkata
```

## Layout

```
scripts/commodities.sh     pre-run script; MODE picks the output
scripts/quotes.py          fetches quotes and headlines, prints report JSON
scripts/format_plain.jq    report JSON -> plain-text briefing
examples/cron-commands.sh  the three cron jobs, with the prompts
examples/report.sample.json  fixture, so the formatter and gate run offline
tests/test_quotes.py       13 offline tests
```

## Configuration

Everything is an environment variable; there is no config file.

| Variable | Default | Meaning |
|---|---|---|
| `MODE` | `raw` | `raw`, `plain` or `gate` |
| `MOVE_THRESHOLD` | `1.5` | percent move that opens the gate (`MODE=gate`) |
| `WORK_DIR` | `~/.hermes/work/commodities` | where `report.json` is kept |
| `FIXTURE` | — | read a saved report instead of the network |

Which instruments appear is the `SYMBOLS` dict at the top of
`scripts/quotes.py`. Yahoo symbols: `GC=F` gold, `SI=F` silver, `HG=F` copper,
`CL=F` WTI, `NG=F` natural gas, `INR=X` USD/INR.

The threshold is the whole design of the gate: too low and you have built a
notification you will mute, too high and you hear about it from the news first.

## Data sources, and their honest caveats

Quotes come from Yahoo Finance's chart endpoint; headlines from Google News
RSS. Neither needs a key, and **neither is an official, supported API.**
Yahoo's response shape has changed before; Google News RSS carries no
stability guarantee. That is the price of no API key.

The script is built for that. A failed symbol lands in `errors` rather than
killing the run, so a partial outage still produces a briefing, and the
briefing says which instruments are missing. The tests inject every upstream
response, so they keep passing when the endpoints are down and start failing
the moment our own parsing breaks.

What they cannot catch is a silent upstream format change. **Run `make check`
once a month.** If you would rather have something more stable, Stooq serves
the same quotes as CSV:

```
https://stooq.com/q/l/?s=xauusd&f=sd2t2ohlc&h&e=csv
```

Swap it into `parse_quote()` and nothing else changes.

## Notes from running this

**Hermes refuses inline scripts in cron runs.** `python3 -c` and `bash -c` are
rejected, because nobody is present to approve them. Everything here lives in
a file for that reason. A heredoc *inside* a script file is fine; a `python3
-c` in the prompt is not.

**Use absolute paths.** The scheduler does not source your interactive shell
profile, so a job that works under `hermes cron run` and fails on schedule is
almost always a `PATH` difference.

**Name every job.** `hermes cron pause 3` is a bet on the ordering being what
you remember.

**Check `hermes logs` the morning after, not the week after.** A job that has
been silently failing for six days looks exactly like a job that had nothing
to report.

## Licence

MIT
