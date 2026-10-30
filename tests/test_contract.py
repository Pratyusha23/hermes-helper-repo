#!/usr/bin/env python3
"""The OUTPUT contract of quotes.py. Do not weaken these to make a fix pass.

============================================================================
READ THIS BEFORE EDITING -- this applies to humans and to the canary agent.

Everything downstream depends on the SHAPE of report.json, not on where the
numbers came from:

    scripts/format_plain.jq   renders it into the Telegram briefing
    scripts/commodities.sh    MODE=gate filters it on .biggest_move
    the cron prompt           tells the model to read .quotes[] and .errors

So there are two kinds of test in this repo, and they are not equal:

  tests/test_quotes.py    INPUT tests. Fixtures of Yahoo JSON and Stooq CSV.
                          These are DISPOSABLE. When an upstream provider
                          changes shape, the old fixture describes a format
                          that no longer exists anywhere, and keeping it only
                          forces the parser to carry dead branches. Replace
                          it with the new body. Do not accumulate.

  tests/test_contract.py  OUTPUT tests. THIS FILE. These are PERMANENT. No
                          upstream change can justify relaxing one, because
                          an upstream change is exactly what they exist to
                          absorb. If a parser fix cannot satisfy this file,
                          the fix is wrong -- stop and report rather than
                          editing here.

In short: Yahoo is allowed to change. report.json is not.
============================================================================
"""

import json
import pathlib
import shutil
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import fixtures  # noqa: E402
import quotes  # noqa: E402

REPORT_KEYS = {"asof", "quotes", "headlines", "errors", "biggest_move"}
QUOTE_KEYS = {"label", "symbol", "source", "last", "prev", "pct"}


def fake_fetch(yahoo_price=4120.5, yahoo_prev=4086.2):
    """A fetch() that satisfies every symbol, so we test shape not sourcing.

    The body comes from tests/fixtures.py on purpose. When a provider changes
    shape you edit the fixture; nothing in THIS file should ever need to
    change, because the whole point of it is to survive that.
    """
    chart = fixtures.chart_body(yahoo_price, yahoo_prev)

    def fetch(url, timeout=20):
        return fixtures.NEWS_OK if "news.google" in url else chart
    return fetch


class TestReportShape(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.report = quotes.build_report(fetch=fake_fetch())

    def test_top_level_keys_are_exactly_these(self):
        """Downstream reads these by name. Adding is a change; removing breaks."""
        self.assertEqual(set(self.report), REPORT_KEYS)

    def test_quote_rows_have_every_key(self):
        for row in self.report["quotes"]:
            self.assertEqual(set(row), QUOTE_KEYS, f"row for {row.get('label')}")

    def test_types(self):
        self.assertIsInstance(self.report["asof"], str)
        self.assertIsInstance(self.report["quotes"], list)
        self.assertIsInstance(self.report["headlines"], list)
        self.assertIsInstance(self.report["errors"], list)
        self.assertIsInstance(self.report["biggest_move"], float)
        for row in self.report["quotes"]:
            self.assertIsInstance(row["label"], str)
            self.assertIsInstance(row["symbol"], str)
            self.assertIn(row["source"], quotes.PROVIDERS)
            self.assertIsInstance(row["last"], float)
            for key in ("prev", "pct"):
                self.assertTrue(row[key] is None
                                or isinstance(row[key], float))
        for item in self.report["headlines"] + self.report["errors"]:
            self.assertIsInstance(item, str)

    def test_asof_is_an_iso_timestamp(self):
        import datetime as dt
        dt.datetime.fromisoformat(self.report["asof"])

    def test_biggest_move_is_the_max_absolute_pct(self):
        """MODE=gate compares this against a threshold. It must mean that."""
        pcts = [abs(q["pct"]) for q in self.report["quotes"]
                if q["pct"] is not None]
        self.assertAlmostEqual(self.report["biggest_move"],
                               round(max(pcts), 2) if pcts else 0.0)

    def test_pct_sign_follows_the_move(self):
        down = quotes.build_report(fetch=fake_fetch(90.0, 100.0))
        self.assertTrue(all(q["pct"] < 0 for q in down["quotes"]))
        up = quotes.build_report(fetch=fake_fetch(110.0, 100.0))
        self.assertTrue(all(q["pct"] > 0 for q in up["quotes"]))

    def test_report_is_json_serialisable(self):
        """The gate pipes this straight into jq; a non-serialisable value
        would fail at 3am rather than here."""
        json.loads(json.dumps(self.report))

    def test_a_total_outage_still_produces_the_same_shape(self):
        """The contract holds on the worst day, not just the good one."""
        def dead(url, timeout=20):
            raise OSError("down")
        report = quotes.build_report(fetch=dead)
        self.assertEqual(set(report), REPORT_KEYS)
        self.assertEqual(report["quotes"], [])
        self.assertEqual(report["biggest_move"], 0.0)
        self.assertTrue(report["errors"])


@unittest.skipUnless(shutil.which("jq"), "jq not installed")
class TestDownstreamConsumers(unittest.TestCase):
    """The real contract test: can the things that read report.json read it?

    A schema assertion can drift from what the consumers actually require.
    These run the consumers.
    """

    @classmethod
    def setUpClass(cls):
        cls.report = json.dumps(quotes.build_report(fetch=fake_fetch()))

    def run_jq(self, *args, stdin=None):
        return subprocess.run(["jq", *args], input=stdin or self.report,
                              capture_output=True, text=True)

    def test_plain_formatter_renders_without_error(self):
        result = self.run_jq("-r", "-f",
                             str(ROOT / "scripts" / "format_plain.jq"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Commodities", result.stdout)
        # Every instrument must reach the briefing, not just parse.
        for label in (s["label"] for s in quotes.SYMBOLS):
            self.assertIn(label, result.stdout)

    def test_gate_filter_opens_on_a_big_move(self):
        big = json.dumps(quotes.build_report(fetch=fake_fetch(90.0, 100.0)))
        result = self.run_jq(
            "-c", "--argjson", "threshold", "1.5",
            'if .biggest_move < $threshold then {wakeAgent: false} '
            'else {wakeAgent: true, context: .} end', stdin=big)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["wakeAgent"])

    def test_gate_filter_stays_shut_on_a_quiet_day(self):
        quiet = json.dumps(quotes.build_report(fetch=fake_fetch(100.1, 100.0)))
        result = self.run_jq(
            "-c", "--argjson", "threshold", "1.5",
            'if .biggest_move < $threshold then {wakeAgent: false} '
            'else {wakeAgent: true, context: .} end', stdin=quiet)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["wakeAgent"])

    def test_gate_output_is_a_single_line(self):
        """Hermes reads the LAST LINE of the script's output as the verdict.
        A pretty-printed gate verdict silently never wakes the agent."""
        result = self.run_jq("-c", "{wakeAgent: (.biggest_move >= 1.5)}")
        self.assertEqual(result.stdout.strip().count("\n"), 0)


if __name__ == "__main__":
    unittest.main()
