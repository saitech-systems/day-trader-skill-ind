#!/usr/bin/env python3
"""Builds trader-entry's own SYNTHETIC fixture. Not market data.

    python3 tests/fixtures/trader-entry/make_synthetic.py

  TESTCO-2026-10-19-1m-synthetic.json  one-minute bars for a made-up NSE stock,
      TESTCO (Yahoo "TESTCO.NS"), on Monday 2026-10-19, as a range=1d answer
      "fetched" at 9:51:30 IST. Written by hand to test the edges: the opening
      range (9:15 to 9:29) is 99.00 to 100.50; the 9:37 bar wicks to 100.70 and
      closes back inside; the 9:42 bar closes exactly AT the high (not above);
      the 9:43 bar closes above it at 100.62 (bar 28, 9:44 AM); the 9:34 bar
      comes as nulls; the 9:46 bar carries a volume spike (bar 31, 9:47 AM);
      the 9:51 bar is still forming and a live tick sits at 9:51:30.

The 5-minute story (DEMO) lives in tests/fixtures/shared/, from
tests/fixtures/make_synthetic.py, whose helpers this file reuses.
"""

import datetime
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.dont_write_bytecode = True
import make_synthetic as ms  # noqa: E402  (tests/fixtures/make_synthetic.py)

DAY = datetime.date(2026, 10, 19)
FETCHED = datetime.datetime(2026, 10, 19, 9, 51, 30)     # IST
# one-minute bars from 9:15 IST: (open, high, low, close, volume); None = Yahoo sent nulls
BARS = [
    (99.8, 100.2, 99.4, 100.0, 60000), (100.0, 100.1, 99.0, 99.2, 15000), (99.2, 99.9, 99.1, 99.85, 9000),
    (99.85, 100.5, 99.8, 100.3, 12000), (100.3, 100.4, 99.9, 100.0, 8000), (100.0, 100.2, 99.7, 99.75, 7000),
    (99.75, 99.95, 99.5, 99.6, 6000), (99.6, 99.9, 99.55, 99.85, 6000), (99.85, 100.1, 99.8, 100.05, 7000),
    (100.05, 100.25, 100.0, 100.2, 6000), (100.2, 100.35, 100.1, 100.15, 5000), (100.15, 100.3, 100.0, 100.1, 5000),
    (100.1, 100.2, 99.95, 100.0, 4000), (100.0, 100.15, 99.9, 100.05, 5000), (100.05, 100.3, 100.0, 100.25, 5000),
    (100.25, 100.45, 100.2, 100.4, 6000), (100.4, 100.48, 100.3, 100.35, 5000), (100.35, 100.4, 100.1, 100.2, 4000),
    (100.2, 100.3, 100.05, 100.1, 4000), None, (100.1, 100.25, 100.0, 100.2, 4000),
    (100.2, 100.35, 100.15, 100.3, 5000), (100.3, 100.7, 100.25, 100.3, 15000), (100.3, 100.4, 100.2, 100.35, 5000),
    (100.35, 100.45, 100.3, 100.4, 5000), (100.4, 100.5, 100.35, 100.45, 6000), (100.45, 100.5, 100.3, 100.35, 5000),
    (100.35, 100.5, 100.3, 100.5, 6000), (100.5, 100.7, 100.45, 100.62, 9000), (100.62, 100.75, 100.55, 100.7, 8000),
    (100.7, 100.78, 100.6, 100.65, 7000), (100.65, 100.85, 100.6, 100.8, 30000), (100.8, 100.9, 100.7, 100.85, 9000),
    (100.85, 100.95, 100.75, 100.9, 8000), (100.9, 101.0, 100.8, 100.95, 8000), (100.95, 101.05, 100.85, 100.88, 7000),
    (100.88, 100.95, 100.85, 100.9, 2000),
]
LIVE_TICK = 100.9


def main():
    start = datetime.datetime.combine(DAY, datetime.time(9, 15))
    bars, nulls = [], set()
    for k, b in enumerate(BARS):
        t = ms.ts(start + datetime.timedelta(minutes=k))
        if b is None:
            bars.append((t, 0, 0, 0, 0, 0))
            nulls.add(k)
        else:
            bars.append((t,) + b)
    bars.append((ms.ts(FETCHED), LIVE_TICK, LIVE_TICK, LIVE_TICK, LIVE_TICK, 0))
    doc = ms.chart_doc("TESTCO", [b for k, b in enumerate(bars) if k not in nulls], FETCHED, "1d", 99.5, gran="1m")
    r = doc["chart"]["result"][0]
    r["timestamp"] = [b[0] for b in bars]
    q = r["indicators"]["quote"][0]
    for i, key in enumerate(("open", "high", "low", "close", "volume")):
        q[key] = [None if k in nulls else b[i + 1] for k, b in enumerate(bars)]
    r["meta"].update(longName="TestCo Limited (synthetic)", shortName="TESTCO (SYNTHETIC)",
                     note="SYNTHETIC test bars for trader-entry, not market data")
    path = os.path.join(HERE, "TESTCO-2026-10-19-1m-synthetic.json")
    ms.write(path, doc)
    print("wrote %s" % path)


if __name__ == "__main__":
    main()
