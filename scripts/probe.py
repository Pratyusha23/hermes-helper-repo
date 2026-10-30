#!/usr/bin/env python3
"""Hit every provider for every symbol and record exactly what came back.

This is the canary. The offline test suite proves our parsing logic is
self-consistent; it cannot prove that Yahoo still returns the JSON that logic
expects. Only a live request does that, and only a live request that keeps the
bytes can tell you what to change when it stops.

Writes into --out (default: diagnosis/):

    probe.json              one record per (symbol, provider)
    <provider>-<symbol>.raw the first RAW_CAP bytes of each response

Exit status:

    0   every provider answered for every symbol
    1   something failed

Note the strictness. A run where Yahoo works and Stooq is broken still exits
1, because the entire value of a fallback is that it works on the day the
primary does not. A silently broken backup is invisible until it is needed,
which is the worst possible moment to discover it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
import urllib.error

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import quotes  # noqa: E402

RAW_CAP = 4096          # enough to see a shape change, small enough to read


def probe_one(spec: dict, provider: str, out: pathlib.Path,
              fetch=quotes.http_get) -> dict:
    """One provider, one symbol. Never raises."""
    symbol = spec.get(provider)
    record = {
        "label": spec["label"],
        "provider": provider,
        "symbol": symbol,
        "ok": False,
        "stage": None,
        "error": None,
        "status": None,
        "bytes": 0,
        "raw_file": None,
        "parsed": None,
    }
    if not symbol:
        record["stage"] = "skipped"
        record["error"] = "symbol not configured for this provider"
        return record

    build_url, parse = quotes.PROVIDERS[provider]
    url = build_url(symbol)
    record["url"] = url

    try:
        body = fetch(url)
    except urllib.error.HTTPError as exc:
        record["stage"] = "fetch"
        record["status"] = exc.code
        record["error"] = f"HTTPError {exc.code}"
        return record
    except Exception as exc:                                    # noqa: BLE001
        record["stage"] = "fetch"
        record["error"] = f"{type(exc).__name__}: {exc}"
        return record

    record["status"] = 200
    record["bytes"] = len(body)

    # Keep the bytes BEFORE trying to parse them. When the parse is what
    # broke, this file is the only thing that tells you what to change.
    safe = f"{provider}-{symbol}".replace("/", "_").replace("=", "")
    raw_path = out / f"{safe}.raw"
    raw_path.write_bytes(body[:RAW_CAP])
    record["raw_file"] = raw_path.name

    try:
        record["parsed"] = parse(body)
    except Exception as exc:                                    # noqa: BLE001
        record["stage"] = "parse"
        record["error"] = f"{type(exc).__name__}: {exc}"
        return record

    record["ok"] = True
    record["stage"] = "ok"
    return record


def probe_news(out: pathlib.Path, fetch=quotes.http_get) -> dict:
    record = {"label": "news", "provider": "google-news", "symbol": None,
              "ok": False, "stage": None, "error": None, "bytes": 0,
              "raw_file": None, "parsed": None}
    try:
        body = fetch(quotes.news_url())
    except Exception as exc:                                    # noqa: BLE001
        record["stage"] = "fetch"
        record["error"] = f"{type(exc).__name__}: {exc}"
        return record

    record["bytes"] = len(body)
    raw_path = out / "google-news.raw"
    raw_path.write_bytes(body[:RAW_CAP])
    record["raw_file"] = raw_path.name

    try:
        headlines = quotes.parse_headlines(body)
    except Exception as exc:                                    # noqa: BLE001
        record["stage"] = "parse"
        record["error"] = f"{type(exc).__name__}: {exc}"
        return record

    if not headlines:
        record["stage"] = "parse"
        record["error"] = "feed parsed but produced no headlines"
        return record

    record["ok"] = True
    record["stage"] = "ok"
    record["parsed"] = {"count": len(headlines), "first": headlines[0]}
    return record


def summarise(records: list[dict]) -> str:
    """A table small enough to paste into a GitHub issue."""
    width = max(len(r["label"]) for r in records)
    lines = [f"{'instrument':<{width}}  {'provider':<12}  status"]
    lines.append("-" * (width + 32))
    for r in records:
        mark = "ok" if r["ok"] else f"FAILED ({r['stage']}) {r['error']}"
        lines.append(f"{r['label']:<{width}}  {r['provider']:<12}  {mark}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="diagnosis",
                        help="directory for probe.json and the raw bodies")
    parser.add_argument("--allow-fallback", action="store_true",
                        help="exit 0 if each symbol had at least one working "
                             "provider, instead of requiring all of them")
    args = parser.parse_args(argv)

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    records = []
    for spec in quotes.SYMBOLS:
        for provider in quotes.PROVIDERS:
            records.append(probe_one(spec, provider, out))
    records.append(probe_news(out))

    by_label: dict[str, list[dict]] = {}
    for r in records:
        by_label.setdefault(r["label"], []).append(r)

    all_ok = all(r["ok"] for r in records)
    usable = all(any(r["ok"] for r in group) for group in by_label.values())

    payload = {
        "asof": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "all_ok": all_ok,
        "every_symbol_usable": usable,
        "records": records,
    }
    (out / "probe.json").write_text(json.dumps(payload, indent=2) + "\n")

    print(summarise(records))
    print()
    print(f"all providers ok:      {all_ok}")
    print(f"every symbol usable:   {usable}")
    print(f"details:               {out / 'probe.json'}")

    return 0 if (usable if args.allow_fallback else all_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
