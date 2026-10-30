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
scripts/briefing-plain.sh  wrapper: MODE=plain
scripts/briefing-raw.sh    wrapper: MODE=raw
scripts/briefing-gate.sh   wrapper: MODE=gate
scripts/probe.py           live canary: hits every provider, keeps the bytes
scripts/canary_gate.sh     pre-run script for the canary-fix cron job
.github/workflows/canary.yml  daily two-signal check
examples/cron-commands.sh  the cron jobs, with the prompts
examples/report.sample.json  fixture, so the formatter and gate run offline
tests/test_quotes.py       input tests: parsers vs saved fixtures (disposable)
tests/test_contract.py     output contract: report.json's shape (permanent)
```

## Configuration

Everything is an environment variable; there is no config file.

| Variable | Default | Meaning |
|---|---|---|
| `MODE` | `raw` | `raw`, `plain` or `gate` |
| `MOVE_THRESHOLD` | `1.5` | percent move that opens the gate (`MODE=gate`) |
| `WORK_DIR` | `~/.hermes/work/commodities` | where `report.json` is kept |
| `PROVIDER_ORDER` | `yahoo,stooq` | which price provider to try first |
| `FIXTURE` | — | read a saved report instead of the network |

Which instruments appear is the `SYMBOLS` list at the top of
`scripts/quotes.py` — see the symbol table below.

The threshold is the whole design of the gate: too low and you have built a
notification you will mute, too high and you hear about it from the news first.

## Data sources, and their honest caveats

Two price providers, tried in order per symbol, plus Google News RSS for
headlines. Nothing here needs a key or an account.

| Provider | Endpoint | Format |
|---|---|---|
| Yahoo (primary) | `query1.finance.yahoo.com/v8/finance/chart/GC=F` | JSON |
| Stooq (fallback) | `stooq.com/q/d/l/?s=xauusd&d1=…&d2=…&i=d` | daily CSV |
| Google News | `news.google.com/rss/search?q=…` | RSS |

**None of the three is an official, supported API.** Yahoo's response shape
has changed before; Stooq publishes no API contract; Google News RSS carries
no stability guarantee. That is the price of no API key.

Swapping one unofficial source for another does not remove that risk, it just
moves it — so the script uses both. A symbol is only reported as an error when
*every* provider failed for it, and each `quotes[]` row records which provider
answered:

```json
{"label": "Gold (COMEX, $/oz)", "symbol": "GC=F", "source": "yahoo",
 "last": 4120.5, "prev": 4086.2, "pct": 0.84}
```

So Yahoo going down, or changing its JSON shape, costs you the `source` label
and nothing else. A symbol that both providers fail lands in `errors`, the run
still produces a briefing, and the briefing says what is missing.

Flip the order with `PROVIDER_ORDER=stooq,yahoo`. Add or remove instruments in
the `SYMBOLS` list at the top of `scripts/quotes.py`; a provider key may be
omitted for a symbol it does not carry.

| Instrument | Yahoo | Stooq |
|---|---|---|
| Gold | `GC=F` | `xauusd` |
| Silver | `SI=F` | `xagusd` |
| Copper | `HG=F` | `hg.f` |
| WTI crude | `CL=F` | `cl.f` |
| Natural gas | `NG=F` | `ng.f` |
| USD/INR | `INR=X` | `usdinr` |

## The canary

Both providers will change shape eventually. The problem is not that they
break, it is that they break *silently* — and a dead fallback is invisible
until the day the primary fails too.

`.github/workflows/canary.yml` runs two checks every morning and the
combination localises the fault:

| offline suite | live probe | what it means |
|---|---|---|
| pass | pass | nothing to do |
| pass | **fail** | upstream changed shape — fix the parser |
| **fail** | pass | you broke it — look at the last commit |
| **fail** | **fail** | almost certainly you; a bad commit breaks both |

Two design choices make it worth having:

**The probe is strict.** It fails if *any* provider fails, even one whose
symbol the other provider is still serving. A working Yahoo does not excuse a
broken Stooq.

**It keeps the bytes.** `scripts/probe.py` writes the first 4 KB of every
response into `diagnosis/` *before* parsing it. `KeyError:
'regularMarketPrice'` tells you the parse failed, not what the response now
looks like — without the body you are guessing at a new format.

Failures open one reused issue labelled `canary`, and it closes itself when
both checks pass again. Run the probe by hand with:

```bash
make probe                      # strict: every provider must answer
make probe-lenient              # passes if each symbol has one live provider
```

### Two kinds of test, and only one of them is permanent

| | what it pins | when upstream changes |
|---|---|---|
| `tests/test_quotes.py` | parsers against saved Yahoo/Stooq bodies | **replace** the stale fixture — an old one describes a format that no longer exists and forces a dead branch in the parser |
| `tests/test_contract.py` | the shape of `report.json` | **never relaxed** — absorbing upstream change is the entire reason it exists |

`format_plain.jq`, the `MODE=gate` filter and the cron prompt all read
`report.json` by key. That shape is the contract. The input format is allowed
to change; the output format is not.

`test_contract.py` also runs the real consumers rather than just asserting a
schema, because a schema drifts from what consumers actually need. It pipes a
report through `format_plain.jq` and through the gate's `jq` expression and
checks the briefing contains every instrument, the gate opens above the
threshold and stays shut below it, and the verdict is a single line — Hermes
reads only the last line, so a pretty-printed verdict silently never wakes the
agent.

It catches things the input tests cannot. Change `asof` from ISO-8601 to a unix
timestamp and all input tests still pass; the contract fails. Add an instrument
that never reaches the briefing and only the contract notices.

```bash
make contract     # the permanent output contract alone
make test         # everything
```

### Letting the agent take the first pass

`scripts/canary_gate.sh` gates a second Hermes cron job on that issue. On every
morning where nothing is broken it prints `{"wakeAgent": false}` and no model is
called; when something breaks it hands the agent the issue, `probe.json` and the
directory of raw bodies. See `examples/cron-commands.sh` for the prompt — the
two lines that matter are *read the raw body before changing anything* and *add
a test using the real body as a fixture*, so every upstream break becomes a
permanent regression test.

The agent wakes **once** per break, not every morning. The issue stays open
until a later canary run passes, which needs a human to merge — so without a
marker the agent would redo the same work daily while the PR waits. The agent
writes `marker_file` as its **last** action, once it has reached a conclusion:
a run that dies halfway leaves no marker and is retried tomorrow. When the
issue closes, the gate clears the markers, so a regression is handled afresh.

It pushes only when `make test` passes. On a failing suite it pushes nothing,
retries once, then reports the failing test names. It opens a PR and comments
the link on the issue; it never merges and never force-pushes.

Two credentials, because they do different jobs and neither can do the other's:

| capability | credential | why |
|---|---|---|
| clone, push the fix branch | **deploy key** (write), in `~/.ssh/` | git over SSH; no API surface at all |
| read the issue, download the artifact, comment | **fine-grained token**, in `~/.hermes/secrets/gh_token` | REST API; deploy keys cannot authenticate to it |

Splitting them means the token never needs `Contents: write`. It needs
**Issues: Read and write** (comment, close), **Actions: Read** (download the
artifact) and **Pull requests: Read and write** (open the PR). Set
`CANARY_REPO=owner/name` too.

A token missing one of those fails at the step that needs it, often after the
useful work is done — `Resource not accessible by personal access token` is
what that looks like.

### What none of this catches

Both providers changing at once, and either one returning *wrong but
well-formed* numbers — a stale close, a wrong currency. Shape checks cannot see
that. Nothing replaces glancing at the briefing and noticing gold looks off.

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
