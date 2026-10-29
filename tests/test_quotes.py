#!/usr/bin/env python3
"""Offline tests for quotes.py. No network, no dependencies.

    python3 -m unittest discover -s tests -v

Every upstream response is injected, so these keep passing when Yahoo, Stooq
or Google News are unreachable -- and start failing the moment our own
parsing or fallback logic breaks.
"""

import datetime as dt
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import quotes  # noqa: E402


# --------------------------------------------------------------- fixtures

STOOQ_OK = (b"Date,Open,High,Low,Close,Volume\n"
            b"2026-10-27,4080.1,4095.0,4071.2,4086.2,120345\n"
            b"2026-10-28,4087.0,4131.4,4085.9,4120.5,133990\n")

STOOQ_ONE_ROW = (b"Date,Open,High,Low,Close,Volume\n"
                 b"2026-10-28,4087.0,4131.4,4085.9,4120.5,133990\n")

STOOQ_NO_DATA = b"No data\n"

STOOQ_BLANK_CLOSE = (b"Date,Open,High,Low,Close,Volume\n"
                     b"2026-10-28,4087.0,4131.4,4085.9,-,0\n")

YAHOO_OK = json.dumps({
    "chart": {"result": [{"meta": {
        "regularMarketPrice": 4120.5,
        "chartPreviousClose": 4086.2,
    }}]}
}).encode()

YAHOO_NO_PREV = json.dumps({
    "chart": {"result": [{"meta": {"regularMarketPrice": 4120.5}}]}
}).encode()

YAHOO_BROKEN = b'{"chart": {"result": []}}'

NEWS_OK = b"""<?xml version="1.0"?>
<rss><channel>
  <item><title>Gold holds near record</title><source>Reuters</source></item>
  <item><title>Silver slips after Barrick &amp; Newmont results</title>
        <source>Bloomberg</source></item>
  <item><title>Copper steady</title></item>
  <item><title></title><source>Nobody</source></item>
</channel></rss>"""

GOLD = {"label": "Gold (COMEX, $/oz)", "stooq": "xauusd", "yahoo": "GC=F"}


def router(**bodies):
    """Build a fetch() that answers by which provider the URL belongs to.

    A body may be an Exception instance, which is raised instead of returned.
    """
    def fetch(url, timeout=20):
        if "news.google" in url:
            key = "news"
        elif "stooq.com" in url:
            key = "stooq"
        else:
            key = "yahoo"
        if key not in bodies:
            raise OSError(f"no stub for {key}")
        body = bodies[key]
        if isinstance(body, Exception):
            raise body
        return body
    return fetch


# ------------------------------------------------------------------- URLs

class TestUrls(unittest.TestCase):

    def test_stooq_url_shape(self):
        url = quotes.stooq_url("XAUUSD", today=dt.date(2026, 10, 29))
        self.assertIn("stooq.com/q/d/l/", url)
        self.assertIn("s=xauusd", url)          # lowercased
        self.assertIn("d2=20261029", url)
        self.assertIn("d1=20261019", url)       # 10-day lookback
        self.assertIn("i=d", url)

    def test_stooq_url_escapes_dotted_symbols(self):
        url = quotes.stooq_url("hg.f", today=dt.date(2026, 10, 29))
        self.assertIn("s=hg.f", url)

    def test_yahoo_url_escapes_equals(self):
        self.assertIn("GC%3DF", quotes.yahoo_url("GC=F"))


# ----------------------------------------------------------------- parsing

class TestParseStooq(unittest.TestCase):

    def test_uses_last_two_closes(self):
        got = quotes.parse_stooq(STOOQ_OK)
        self.assertEqual(got["last"], 4120.5)
        self.assertEqual(got["prev"], 4086.2)
        self.assertEqual(got["pct"], 0.84)

    def test_single_row_gives_null_pct(self):
        got = quotes.parse_stooq(STOOQ_ONE_ROW)
        self.assertEqual(got["last"], 4120.5)
        self.assertIsNone(got["prev"])
        self.assertIsNone(got["pct"])

    def test_no_data_response_raises(self):
        """Stooq answers an unknown symbol with 'No data'; must not parse."""
        with self.assertRaises(Exception):
            quotes.parse_stooq(STOOQ_NO_DATA)

    def test_placeholder_close_raises(self):
        with self.assertRaises(ValueError):
            quotes.parse_stooq(STOOQ_BLANK_CLOSE)


class TestParseYahoo(unittest.TestCase):

    def test_computes_overnight_move(self):
        got = quotes.parse_yahoo(YAHOO_OK)
        self.assertEqual(got["last"], 4120.5)
        self.assertEqual(got["prev"], 4086.2)
        self.assertEqual(got["pct"], 0.84)

    def test_missing_previous_close_gives_null_pct(self):
        got = quotes.parse_yahoo(YAHOO_NO_PREV)
        self.assertIsNone(got["pct"])

    def test_unexpected_shape_raises(self):
        with self.assertRaises((KeyError, IndexError)):
            quotes.parse_yahoo(YAHOO_BROKEN)


class TestParseHeadlines(unittest.TestCase):

    def test_joins_title_and_source(self):
        self.assertEqual(quotes.parse_headlines(NEWS_OK)[0],
                         "Gold holds near record - Reuters")

    def test_unescapes_html_entities(self):
        self.assertIn("Barrick & Newmont", quotes.parse_headlines(NEWS_OK)[1])

    def test_source_is_optional(self):
        self.assertEqual(quotes.parse_headlines(NEWS_OK)[2], "Copper steady")

    def test_skips_empty_titles(self):
        self.assertEqual(len(quotes.parse_headlines(NEWS_OK)), 3)

    def test_respects_limit(self):
        self.assertEqual(len(quotes.parse_headlines(NEWS_OK, limit=2)), 2)


# ---------------------------------------------------------------- fallback

class TestFetchQuote(unittest.TestCase):

    def test_prefers_yahoo(self):
        """Yahoo is primary; Stooq must not answer while Yahoo is healthy."""
        row, err = quotes.fetch_quote(
            GOLD, fetch=router(stooq=STOOQ_OK, yahoo=YAHOO_OK))
        self.assertIsNone(err)
        self.assertEqual(row["source"], "yahoo")
        self.assertEqual(row["symbol"], "GC=F")

    def test_falls_back_to_stooq_on_network_error(self):
        row, err = quotes.fetch_quote(
            GOLD, fetch=router(stooq=STOOQ_OK, yahoo=OSError("down")))
        self.assertIsNone(err)
        self.assertEqual(row["source"], "stooq")
        self.assertEqual(row["symbol"], "xauusd")

    def test_falls_back_to_stooq_on_unparseable_body(self):
        """A format change is as survivable as an outage."""
        row, err = quotes.fetch_quote(
            GOLD, fetch=router(stooq=STOOQ_OK, yahoo=YAHOO_BROKEN))
        self.assertIsNone(err)
        self.assertEqual(row["source"], "stooq")

    def test_error_only_when_every_provider_fails(self):
        row, err = quotes.fetch_quote(
            GOLD, fetch=router(stooq=STOOQ_NO_DATA, yahoo=OSError("down")))
        self.assertIsNone(row)
        self.assertIn("Gold", err)
        self.assertIn("yahoo OSError", err)
        self.assertIn("stooq", err)

    def test_order_is_configurable(self):
        row, _ = quotes.fetch_quote(
            GOLD, fetch=router(stooq=STOOQ_OK, yahoo=YAHOO_OK),
            order=("stooq", "yahoo"))
        self.assertEqual(row["source"], "stooq")

    def test_skips_a_provider_the_symbol_lacks(self):
        spec = {"label": "Gold spot", "stooq": "xauusd"}
        row, _ = quotes.fetch_quote(spec, fetch=router(stooq=STOOQ_OK))
        self.assertEqual(row["source"], "stooq")

    def test_no_provider_configured_is_an_error(self):
        row, err = quotes.fetch_quote({"label": "Nothing"},
                                      fetch=router(), order=("stooq",))
        self.assertIsNone(row)
        self.assertIn("no provider configured", err)


# ------------------------------------------------------------------ report

class TestBuildReport(unittest.TestCase):

    def test_happy_path(self):
        report = quotes.build_report(
            fetch=router(stooq=STOOQ_OK, yahoo=YAHOO_OK, news=NEWS_OK))
        self.assertEqual(len(report["quotes"]), len(quotes.SYMBOLS))
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["biggest_move"], 0.84)
        self.assertEqual(len(report["headlines"]), 3)
        self.assertTrue(all(q["source"] == "yahoo" for q in report["quotes"]))

    def test_whole_provider_outage_degrades_to_the_other(self):
        """Yahoo down must cost us nothing but the source label."""
        report = quotes.build_report(
            fetch=router(stooq=STOOQ_OK, yahoo=OSError("down"), news=NEWS_OK))
        self.assertEqual(len(report["quotes"]), len(quotes.SYMBOLS))
        self.assertEqual(report["errors"], [])
        self.assertTrue(all(q["source"] == "stooq" for q in report["quotes"]))

    def test_news_failure_is_reported_not_raised(self):
        report = quotes.build_report(
            fetch=router(stooq=STOOQ_OK, yahoo=YAHOO_OK,
                         news=OSError("down")))
        self.assertEqual(report["headlines"], [])
        self.assertEqual(report["errors"], ["news: OSError"])

    def test_total_outage_still_returns_a_report(self):
        report = quotes.build_report(fetch=router())
        self.assertEqual(report["quotes"], [])
        self.assertEqual(report["biggest_move"], 0.0)
        self.assertEqual(len(report["errors"]), len(quotes.SYMBOLS) + 1)

    def test_biggest_move_is_absolute(self):
        """A 2% fall must open the gate exactly like a 2% rise."""
        falling = json.dumps({"chart": {"result": [{"meta": {
            "regularMarketPrice": 90.0, "chartPreviousClose": 100.0,
        }}]}}).encode()
        report = quotes.build_report(
            fetch=router(yahoo=falling, news=NEWS_OK))
        self.assertEqual(report["biggest_move"], 10.0)


if __name__ == "__main__":
    unittest.main()
