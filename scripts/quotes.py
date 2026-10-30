#!/usr/bin/env python3
"""Overnight moves for a handful of commodities, plus today's headlines.

Writes a single JSON object to stdout:

    {
      "asof":         ISO-8601 UTC timestamp,
      "quotes":       [{label, symbol, source, last, prev, pct}, ...],
      "headlines":    ["title - source", ...],
      "errors":       ["Gold ($/oz): stooq HTTPError; yahoo KeyError", ...],
      "biggest_move": 1.92          # max |pct| across quotes
    }

Standard library only, so there is nothing to keep installed on the box.

One price provider:

  * Yahoo      https://query1.finance.yahoo.com/v8/finance/chart/GC=F
               JSON. Free, no key.

It is not an official, supported API and could change shape without notice.
That is the price of needing no key.

There was a Stooq fallback here. It lasted days: Stooq put a JavaScript
browser-verification wall in front of the CSV endpoint, which is not something
a scraper should try to get around. The strict probe is what noticed, while
every briefing still looked perfect because Yahoo covered everything -- which
is the whole argument for checking a backup you are not currently using.

So the protection is the canary, not a second scrape. The multi-provider
plumbing below is left intact because adding a provider is a dict entry; a
provider that works is the hard part.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import xml.sax.saxutils as saxutils

# label -> per-provider symbol. Edit freely; nothing downstream depends on the
# set. A provider may be omitted for a symbol it does not carry.
SYMBOLS: list[dict] = [
    {"label": "Gold (COMEX, $/oz)", "yahoo": "GC=F"},
    {"label": "Silver (COMEX, $/oz)", "yahoo": "SI=F"},
    {"label": "Copper ($/lb)", "yahoo": "HG=F"},
    {"label": "WTI crude ($/bbl)", "yahoo": "CL=F"},
    {"label": "USD/INR", "yahoo": "INR=X"},
]

# Order matters: the first provider to return a usable answer wins.
DEFAULT_PROVIDER_ORDER = ("yahoo",)

NEWS_QUERY = '"gold price" OR bullion OR "commodity prices"'
NEWS_LOCALE = "hl=en-IN&gl=IN&ceid=IN:en"
MAX_HEADLINES = 6
TIMEOUT = 20

# Yahoo rejects the default urllib user agent.
HEADERS = {"User-Agent": "Mozilla/5.0 (commodities-briefing)"}


# --------------------------------------------------------------------- fetch

def http_get(url: str, timeout: int = TIMEOUT) -> bytes:
    """Fetch a URL and return its raw body."""
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def yahoo_url(symbol: str) -> str:
    return ("https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{urllib.parse.quote(symbol)}?range=5d&interval=1d")


def news_url() -> str:
    return ("https://news.google.com/rss/search?q="
            f"{urllib.parse.quote(NEWS_QUERY)}&{NEWS_LOCALE}")


# --------------------------------------------------------------------- parse

def _move(last: float, previous: float | None) -> dict:
    pct = None
    if previous:
        pct = round((last - previous) / previous * 100, 2)
    return {
        "last": round(last, 2),
        "prev": round(previous, 2) if previous else None,
        "pct": pct,
    }


def parse_yahoo(body: bytes) -> dict:
    """Last price and previous close from a Yahoo chart response."""
    meta = json.loads(body)["chart"]["result"][0]["meta"]
    previous = meta.get("chartPreviousClose") or meta.get("previousClose")
    price = meta["regularMarketPrice"]
    return _move(float(price),
                 float(previous) if previous else None)


# name -> (url builder, parser). Add an entry to add a provider; fetch_quote
# tries them in PROVIDER_ORDER and only reports an error when all have failed.
PROVIDERS = {
    "yahoo": (yahoo_url, parse_yahoo),
}


def parse_headlines(body: bytes, limit: int = MAX_HEADLINES) -> list[str]:
    """Pull the first `limit` headlines out of a Google News RSS feed."""
    root = ET.fromstring(body)
    headlines = []
    for item in list(root.iterfind(".//item"))[:limit]:
        title = (item.findtext("title") or "").strip()
        source = (item.findtext("source") or "").strip()
        if not title:
            continue
        # Feed titles arrive HTML-escaped; a stray &amp; in a Telegram
        # message looks like a bug in your own script.
        title = saxutils.unescape(title)
        headlines.append(f"{title} - {source}" if source else title)
    return headlines


# --------------------------------------------------------------------- build

def provider_order() -> tuple[str, ...]:
    raw = os.environ.get("PROVIDER_ORDER", "")
    chosen = tuple(p.strip() for p in raw.split(",") if p.strip() in PROVIDERS)
    return chosen or DEFAULT_PROVIDER_ORDER


def fetch_quote(spec: dict, fetch=http_get,
                order: tuple[str, ...] | None = None) -> tuple[dict | None,
                                                               str | None]:
    """Try each provider in turn. Returns (row, error) -- exactly one is None.

    An error is only produced when every provider that carries this symbol
    has failed, and it names what each one did.
    """
    failures = []
    for name in (order or provider_order()):
        symbol = spec.get(name)
        if not symbol:
            continue
        build_url, parse = PROVIDERS[name]
        try:
            row = parse(fetch(build_url(symbol)))
        except Exception as exc:                                # noqa: BLE001
            failures.append(f"{name} {type(exc).__name__}")
            continue
        row.update(label=spec["label"], symbol=symbol, source=name)
        return row, None

    detail = "; ".join(failures) or "no provider configured"
    return None, f"{spec['label']}: {detail}"


def build_report(fetch=http_get) -> dict:
    """Gather every quote and headline. Never raises; failures are reported.

    `fetch` is injected so the tests can run without a network.
    """
    quotes: list[dict] = []
    errors: list[str] = []

    for spec in SYMBOLS:
        row, error = fetch_quote(spec, fetch=fetch)
        if row:
            quotes.append(row)
        else:
            errors.append(error)

    headlines: list[str] = []
    try:
        headlines = parse_headlines(fetch(news_url()))
    except Exception as exc:                                    # noqa: BLE001
        errors.append(f"news: {type(exc).__name__}")

    moves = [abs(q["pct"]) for q in quotes if q["pct"] is not None]

    return {
        "asof": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "quotes": quotes,
        "headlines": headlines,
        "errors": errors,
        "biggest_move": round(max(moves), 2) if moves else 0.0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--fixture", metavar="FILE",
        help="read a saved report instead of calling the network, for "
             "testing the formatter and gate offline")
    args = parser.parse_args(argv)

    if args.fixture:
        with open(args.fixture, encoding="utf-8") as handle:
            report = json.load(handle)
    else:
        report = build_report()

    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")

    # A run that reached nothing at all is a failure, not an empty briefing.
    return 1 if not report["quotes"] and not report["headlines"] else 0


if __name__ == "__main__":
    sys.exit(main())
