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


import fixtures  # noqa: E402

YAHOO_OK = fixtures.YAHOO_OK
YAHOO_NO_PREV = fixtures.YAHOO_NO_PREV
YAHOO_BROKEN = fixtures.YAHOO_BROKEN
NEWS_OK = fixtures.NEWS_OK

GOLD = {"label": "Gold (COMEX, $/oz)", "yahoo": "GC=F"}


def router(**bodies):
    """Build a fetch() that answers by which provider the URL belongs to.

    A body may be an Exception instance, which is raised instead of returned.
    """
    def fetch(url, timeout=20):
        key = "news" if "news.google" in url else "yahoo"
        if key not in bodies:
            raise OSError(f"no stub for {key}")
        body = bodies[key]
        if isinstance(body, Exception):
            raise body
        return body
    return fetch


# ------------------------------------------------------------------- URLs

class TestUrls(unittest.TestCase):

    def test_yahoo_url_escapes_equals(self):
        self.assertIn("GC%3DF", quotes.yahoo_url("GC=F"))


# ----------------------------------------------------------------- parsing

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

    def test_records_the_source(self):
        """Every row says which provider answered."""
        row, err = quotes.fetch_quote(GOLD, fetch=router(yahoo=YAHOO_OK))
        self.assertIsNone(err)
        self.assertEqual(row["source"], "yahoo")
        self.assertEqual(row["symbol"], "GC=F")

    def test_error_only_when_every_provider_fails(self):
        row, err = quotes.fetch_quote(GOLD, fetch=router(yahoo=OSError("down")))
        self.assertIsNone(row)
        self.assertIn("Gold", err)
        self.assertIn("yahoo OSError", err)

    def test_no_provider_configured_is_an_error(self):
        row, err = quotes.fetch_quote({"label": "Nothing"},
                                      fetch=router(), order=("stooq",))
        self.assertIsNone(row)
        self.assertIn("no provider configured", err)


# ------------------------------------------------------------------ report

class TestBuildReport(unittest.TestCase):

    def test_happy_path(self):
        report = quotes.build_report(fetch=router(yahoo=YAHOO_OK, news=NEWS_OK))
        self.assertEqual(len(report["quotes"]), len(quotes.SYMBOLS))
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["biggest_move"], 0.84)
        self.assertEqual(len(report["headlines"]), 3)
        self.assertTrue(all(q["source"] == "yahoo" for q in report["quotes"]))

    def test_news_failure_is_reported_not_raised(self):
        report = quotes.build_report(
            fetch=router(yahoo=YAHOO_OK, news=OSError("down")))
        self.assertEqual(report["headlines"], [])
        self.assertEqual(report["errors"], ["news: OSError"])

    def test_total_outage_still_returns_a_report(self):
        report = quotes.build_report(fetch=router())
        self.assertEqual(report["quotes"], [])
        self.assertEqual(report["biggest_move"], 0.0)
        self.assertEqual(len(report["errors"]), len(quotes.SYMBOLS) + 1)

    def test_biggest_move_is_absolute(self):
        """A 2% fall must open the gate exactly like a 2% rise."""
        def fetch(url, timeout=20):
            if "news.google" in url:
                return NEWS_OK
            # From fixtures.py like everything else: a test that embeds its
            # own upstream body silently stops tracking the real shape.
            return fixtures.chart_body(90.0, 100.0)

        report = quotes.build_report(fetch=fetch)
        self.assertEqual(report["biggest_move"], 10.0)


if __name__ == "__main__":
    unittest.main()


# ------------------------------------------------------------------- probe

import probe  # noqa: E402


class TestProbe(unittest.TestCase):
    """The canary's whole job is to leave evidence behind. Prove it does."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.out = pathlib.Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_records_a_good_response(self):
        rec = probe.probe_one(GOLD, "yahoo", self.out,
                              fetch=router(yahoo=YAHOO_OK))
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["stage"], "ok")
        self.assertEqual(rec["parsed"]["pct"], 0.84)

    def test_keeps_the_bytes_when_parsing_fails(self):
        """A parse break is only actionable if the raw body was saved."""
        rec = probe.probe_one(GOLD, "yahoo", self.out,
                              fetch=router(yahoo=YAHOO_BROKEN))
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["stage"], "parse")
        saved = self.out / rec["raw_file"]
        self.assertTrue(saved.exists())
        self.assertEqual(saved.read_bytes(), YAHOO_BROKEN)

    def test_no_raw_file_when_the_fetch_itself_failed(self):
        rec = probe.probe_one(GOLD, "yahoo", self.out,
                              fetch=router(yahoo=OSError("down")))
        self.assertEqual(rec["stage"], "fetch")
        self.assertIsNone(rec["raw_file"])

    def test_raw_body_is_capped(self):
        huge = b"x" * (probe.RAW_CAP * 3)
        rec = probe.probe_one(GOLD, "yahoo", self.out, fetch=router(yahoo=huge))
        self.assertEqual((self.out / rec["raw_file"]).stat().st_size,
                         probe.RAW_CAP)

    def test_filename_is_safe_for_yahoo_symbols(self):
        """GC=F must not produce a filename with an '=' in it."""
        rec = probe.probe_one(GOLD, "yahoo", self.out,
                              fetch=router(yahoo=YAHOO_OK))
        self.assertNotIn("=", rec["raw_file"])

    def test_unconfigured_provider_is_skipped_not_failed(self):
        rec = probe.probe_one({"label": "Spot"}, "yahoo",
                              self.out, fetch=router())
        self.assertEqual(rec["stage"], "skipped")
        self.assertIsNone(rec["symbol"])

    def test_empty_feed_counts_as_a_parse_failure(self):
        rec = probe.probe_news(self.out,
                               fetch=router(news=fixtures.NEWS_EMPTY))
        self.assertFalse(rec["ok"])
        self.assertIn("no headlines", rec["error"])

    def test_summary_table_names_the_broken_provider(self):
        recs = [probe.probe_one(GOLD, "yahoo", self.out,
                                fetch=router(yahoo=YAHOO_BROKEN))]
        text = probe.summarise(recs)
        self.assertIn("FAILED", text)
        self.assertIn("yahoo", text)
