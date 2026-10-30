"""Saved upstream response bodies, in one place.

These are DISPOSABLE and you are expected to edit them. When a provider
changes shape, replace the body here with the real new one from the canary's
`diagnosis/` artifact. Keeping the old shape around forces the parser to carry
a branch for a format nothing will ever send again.

Both test files read from here, which is the point:

    tests/test_quotes.py    asserts the parsers handle these bodies
    tests/test_contract.py  asserts report.json's SHAPE, whatever the bodies
                            happen to look like

Without this file, `test_contract.py` would have to embed a Yahoo-shaped body
of its own, and a real format change would force editing the one file that
must never be edited to make a fix pass. The fixtures move; the contract
does not.
"""

import json

# --- Yahoo -----------------------------------------------------------------

def chart_body(price=4120.5, previous=4086.2):
    """A Yahoo chart response. Edit this when Yahoo changes shape."""
    meta = {"regularMarketPrice": price}
    if previous is not None:
        meta["chartPreviousClose"] = previous
    return json.dumps({"chart": {"result": [{"meta": meta}]}}).encode()


YAHOO_OK = chart_body()
YAHOO_NO_PREV = chart_body(previous=None)
YAHOO_BROKEN = b'{"chart": {"result": []}}'

# --- Google News -----------------------------------------------------------

NEWS_OK = b"""<?xml version="1.0"?>
<rss><channel>
  <item><title>Gold holds near record</title><source>Reuters</source></item>
  <item><title>Silver slips after Barrick &amp; Newmont results</title>
        <source>Bloomberg</source></item>
  <item><title>Copper steady</title></item>
  <item><title></title><source>Nobody</source></item>
</channel></rss>"""

NEWS_EMPTY = b'<?xml version="1.0"?><rss><channel></channel></rss>'
