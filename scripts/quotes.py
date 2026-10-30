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

Two price providers, tried in order per symbol:

  * Yahoo      https://query1.finance.yahoo.com/v8/finance/chart/GC=F
               JSON. Free, no key. Primary.
  * Stooq      https://stooq.com/q/d/l/?s=xauusd&d1=...&d2=...&i=d
               Daily CSV. Free, no key, no registration. Fallback only.

Neither is an official, supported API, and either could change shape without
notice. That is the price of needing no key. Using both means a single
provider's outage or format change degrades one symbol to the other provider
instead of emptying the briefing -- a symbol is only reported as an error when
every provider failed for it.

What no amount of fallback catches is both providers changing at once, or one
of them silently returning stale numbers. Run `make check` once a month.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
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
    {"label": "Gold (COMEX, $/oz)", "stooq": "xauusd", "yahoo": "GC=F"},
    {"label": "Silver (COMEX, $/oz)", "stooq": "xagusd", "yahoo": "SI=F"},
    {"label": "Copper ($/lb)", "stooq": "hg.f", "yahoo": "HG=F"},
    {"label": "WTI crude ($/bbl)", "stooq": "cl.f", "yahoo": "CL=F"},
    {"label": "USD/INR", "stooq": "usdinr", "yahoo": "INR=X"},
]

# Order matters: the first provider to return a usable answer wins.
# Yahoo is primary; Stooq only answers when Yahoo fails for that symbol.
# Override with PROVIDER_ORDER=stooq,yahoo to flip it.
DEFAULT_PROVIDER_ORDER = ("yahoo", "stooq")

NEWS_QUERY = '"gold price" OR bullion OR "commodity prices"'
NEWS_LOCALE = "hl=en-IN&gl=IN&ceid=IN:en"
MAX_HEADLINES = 6
TIMEOUT = 20
LOOKBACK_DAYS = 10          # enough calendar days to contain two sessions

# Yahoo rejects the default urllib user agent.
HEADERS = {"User-Agent": "Mozilla/5.0 (commodities-briefing)"}


# --------------------------------------------------------------------- fetch

def http_get(url: str, timeout: int = TIMEOUT) -> bytes:
    """Fetch a URL and return its raw body."""
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def stooq_url(symbol: str, today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    start = today - dt.timedelta(days=LOOKBACK_DAYS)
    return ("https://stooq.com/q/d/l/"
            f"?s={urllib.parse.quote(symbol.lower())}"
            f"&d1={start:%Y%m%d}&d2={today:%Y%m%d}&i=d")


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


def parse_stooq(body: bytes) -> dict:
    """Last close and previous close from a Stooq daily CSV.

    Header is Date,Open,High,Low,Close,Volume, oldest row first. Stooq answers
    a bad symbol with the single word "No data", which has no Close column and
    so raises here -- exactly what we want, so the next provider is tried.
    """
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8", "replace"))))
    closes = [float(r["Close"]) for r in rows
              if r.get("Close") not in (None, "", "-")]
    if not closes:
        raise ValueError("no closes in Stooq response")
    return _move(closes[-1], closes[-2] if len(closes) > 1 else None)


def parse_yahoo(body: bytes) -> dict:
    """Last price and previous close from a Yahoo chart response."""
    meta = json.loads(body)["chart"]["result"][0]["meta"]
    previous = meta.get("chartPreviousClose") or meta.get("previousClose")
    price = meta.get("regularMarketPrice")
    if price is None:
        price = meta.get("regularMarketPriceV2", {}).get("raw")
    return _move(float(price),
                 float(previous) if previous else None)


PROVIDERS = {
    "stooq": (stooq_url, parse_stooq),
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
