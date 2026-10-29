#!/usr/bin/env python3
"""Offline tests for quotes.py. No network, no dependencies.

    python3 -m unittest discover -s tests -v

Every upstream response is injected, so these keep passing when Yahoo or
Google News are unreachable -- and start failing the moment our own parsing
breaks.
"""

import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import quotes  # noqa: E402


CHART_OK = json.dumps({
    "chart": {"result": [{"meta": {
        "regularMarketPrice": 4120.5,
        "chartPreviousClose": 4086.2,
    }}]}
}).encode()

CHART_NO_PREV = json.dumps({
    "chart": {"result": [{"meta": {"regularMarketPrice": 4120.5}}]}
}).encode()

CHART_BROKEN = b'{"chart": {"result": []}}'

NEWS_OK = b"""<?xml version="1.0"?>
<rss><channel>
  <item><title>Gold holds near record</title><source>Reuters</source></item>
  <item><title>Silver slips after Barrick &amp; Newmont results</title>
        <source>Bloomberg</source></item>
  <item><title>Copper steady</title></item>
  <item><title></title><source>Nobody</source></item>
</channel></rss>"""


class TestParseQuote(unittest.TestCase):

    def test_computes_overnight_move(self):
        row = quotes.parse_quote("Gold", "GC=F", CHART_OK)
        self.assertEqual(row["last"], 4120.5)
        self.assertEqual(row["prev"], 4086.2)
        self.assertEqual(row["pct"], 0.84)
        self.assertEqual(row["symbol"], "GC=F")

    def test_missing_previous_close_gives_null_pct(self):
        row = quotes.parse_quote("Gold", "GC=F", CHART_NO_PREV)
        self.assertEqual(row["last"], 4120.5)
        self.assertIsNone(row["prev"])
        self.assertIsNone(row["pct"])

    def test_unexpected_shape_raises(self):
        with self.assertRaises((KeyError, IndexError)):
            quotes.parse_quote("Gold", "GC=F", CHART_BROKEN)


class TestParseHeadlines(unittest.TestCase):

    def test_joins_title_and_source(self):
        got = quotes.parse_headlines(NEWS_OK)
        self.assertEqual(got[0], "Gold holds near record - Reuters")

    def test_unescapes_html_entities(self):
        got = quotes.parse_headlines(NEWS_OK)
        self.assertIn("Barrick & Newmont", got[1])

    def test_source_is_optional(self):
        self.assertEqual(quotes.parse_headlines(NEWS_OK)[2], "Copper steady")

    def test_skips_empty_titles(self):
        self.assertEqual(len(quotes.parse_headlines(NEWS_OK)), 3)

    def test_respects_limit(self):
        self.assertEqual(len(quotes.parse_headlines(NEWS_OK, limit=2)), 2)


class TestBuildReport(unittest.TestCase):

    def test_happy_path(self):
        report = quotes.build_report(
            fetch=lambda url, timeout=20:
                NEWS_OK if "news.google" in url else CHART_OK)
        self.assertEqual(len(report["quotes"]), len(quotes.SYMBOLS))
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["biggest_move"], 0.84)
        self.assertEqual(len(report["headlines"]), 3)

    def test_one_bad_symbol_does_not_kill_the_run(self):
        def fetch(url, timeout=20):
            if "news.google" in url:
                return NEWS_OK
            if "SI%3DF" in url or "SI=F" in url:
                raise OSError("boom")
            return CHART_OK

        report = quotes.build_report(fetch=fetch)
        self.assertEqual(len(report["quotes"]), len(quotes.SYMBOLS) - 1)
        self.assertEqual(len(report["errors"]), 1)
        self.assertIn("Silver", report["errors"][0])

    def test_news_failure_is_reported_not_raised(self):
        def fetch(url, timeout=20):
            if "news.google" in url:
                raise OSError("boom")
            return CHART_OK

        report = quotes.build_report(fetch=fetch)
        self.assertEqual(report["headlines"], [])
        self.assertEqual(report["errors"], ["news: OSError"])

    def test_total_outage_still_returns_a_report(self):
        def fetch(url, timeout=20):
            raise OSError("boom")

        report = quotes.build_report(fetch=fetch)
        self.assertEqual(report["quotes"], [])
        self.assertEqual(report["biggest_move"], 0.0)
        self.assertEqual(len(report["errors"]), len(quotes.SYMBOLS) + 1)

    def test_biggest_move_is_absolute(self):
        """A 2% fall must open the gate exactly like a 2% rise."""
        def fetch(url, timeout=20):
            if "news.google" in url:
                return NEWS_OK
            return json.dumps({"chart": {"result": [{"meta": {
                "regularMarketPrice": 90.0,
                "chartPreviousClose": 100.0,
            }}]}}).encode()

        report = quotes.build_report(fetch=fetch)
        self.assertEqual(report["biggest_move"], 10.0)


if __name__ == "__main__":
    unittest.main()
