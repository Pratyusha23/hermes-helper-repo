#!/usr/bin/env python3
"""Overnight moves for a handful of commodities, plus today's headlines.

Writes a single JSON object to stdout:

    {
      "asof":         ISO-8601 UTC timestamp,
      "quotes":       [{label, symbol, last, prev, pct}, ...],
      "headlines":    ["title - source", ...],
      "errors":       ["Gold ($/oz): HTTPError", ...],
      "biggest_move": 1.92          # max |pct| across quotes
    }

Standard library only, so there is nothing to keep installed on the box.

Both upstream sources are unofficial and unsupported:

  * Yahoo Finance's chart endpoint is not a documented product and its
    response shape has changed before.
  * Google News RSS is generous but carries no stability guarantee.

That is the price of needing no API key. A failed symbol lands in `errors`
rather than killing the run, so a partial outage still produces a briefing.
Run `make check` once a month -- a silent format change upstream is the most
likely way this job dies.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import xml.sax.saxutils as saxutils

# label -> Yahoo symbol. Edit freely; nothing downstream depends on the set.
SYMBOLS: dict[str, str] = {
    "Gold (COMEX, $/oz)": "GC=F",
    "Silver (COMEX, $/oz)": "SI=F",
    "Copper ($/lb)": "HG=F",
    "WTI crude ($/bbl)": "CL=F",
    "USD/INR": "INR=X",
}

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


def quote_url(symbol: str) -> str:
    return ("https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{urllib.parse.quote(symbol)}?range=5d&interval=1d")


def news_url() -> str:
    return ("https://news.google.com/rss/search?q="
            f"{urllib.parse.quote(NEWS_QUERY)}&{NEWS_LOCALE}")


# --------------------------------------------------------------------- parse

def parse_quote(label: str, symbol: str, body: bytes) -> dict:
    """Pull last price and overnight move out of a Yahoo chart response.

    Raises KeyError or IndexError if the shape is not what we expect, which
    the caller turns into an entry in `errors`.
    """
    meta = json.loads(body)["chart"]["result"][0]["meta"]
    last = float(meta["regularMarketPrice"])
    previous = meta.get("chartPreviousClose") or meta.get("previousClose")
    previous = float(previous) if previous else None

    pct = None
    if previous:
        pct = round((last - previous) / previous * 100, 2)

    return {
        "label": label,
        "symbol": symbol,
        "last": round(last, 2),
        "prev": round(previous, 2) if previous else None,
        "pct": pct,
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

def build_report(fetch=http_get) -> dict:
    """Gather every quote and headline. Never raises; failures are reported.

    `fetch` is injected so the tests can run without a network.
    """
    quotes: list[dict] = []
    errors: list[str] = []

    for label, symbol in SYMBOLS.items():
        try:
            quotes.append(parse_quote(label, symbol, fetch(quote_url(symbol))))
        except Exception as exc:                                # noqa: BLE001
            errors.append(f"{label}: {type(exc).__name__}")

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
