#!/usr/bin/env python3
"""Builds trader-alerts' SYNTHETIC quote files. Not market data.

    python3 tests/fixtures/trader-alerts/make_synthetic.py

Yahoo chart JSON v8 answers (range=1d interval=1d), the shape alerts.py reads,
for made-up NSE stocks, with fixed numbers so every run writes the same bytes:

  yahoo-DEMO.json     DEMO on Monday 2026-10-19 as of 10:49:45 IST: Rs 1,369.85,
                      previous close Rs 1,352.40 (the shared DEMO story's live tick)
  yahoo-SAMPLE.json   SAMPLE after Friday 2026-10-16's close: Rs 245.17 (0.01 tick),
                      previous close Rs 248.00, down 1.14 %
  yahoo-TESTCO.json   TESTCO after Friday 2026-10-16's close: Rs 3,210.60,
                      previous close Rs 3,198.40
  yahoo-ZZZZ.json     Yahoo's answer for a symbol it does not know (HTTP 404 body)
  kite-quote-SAMPLE-synthetic.json, kite-quote-TESTCO-synthetic.json
                      the same SAMPLE and TESTCO prices as Kite Connect's /quote
                      answers them (DEMO's is tests/fixtures/shared/kite/)
  plan-demo.json      the shared story's plan as trader-exit writes it
"""

import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import make_synthetic as ms   # noqa: E402  (tests/fixtures/make_synthetic.py)

DT = datetime.datetime

# symbol, long name, fetched (IST), day bar (open, high, low, close, volume), previous close
QUOTES = [
    ("DEMO", "Demo Industries Limited (synthetic)", DT(2026, 10, 19, 10, 49, 45),
     (1355.0, 1372.5, 1351.3, 1369.85, 3845000), 1352.40),
    ("SAMPLE", "Sample Textiles Limited (synthetic)", DT(2026, 10, 16, 15, 29, 58),
     (247.50, 248.90, 244.60, 245.17, 1932400), 248.00),
    ("TESTCO", "Testco Software Limited (synthetic)", DT(2026, 10, 16, 15, 29, 59),
     (3201.0, 3224.9, 3190.2, 3210.6, 486300), 3198.40),
]
NOT_FOUND = {"chart": {"result": None, "error": {"code": "Not Found",
                                                 "description": "No data found, symbol may be delisted"}}}
PLAN = {"created_by": "trader-exit", "symbol": "DEMO", "side": "buy", "date": "2026-10-19", "setup": "orb",
        "exchange": "NSE", "tick": 0.1, "entry": 1366.4, "entry_time": "10:00 AM", "entry_bar": 8,
        "interval": "5m", "opening_range": {"high": 1363.8, "low": 1351.3, "minutes": 15},
        "stop": 1357.5, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
        "target": 1375.3, "target_r": 1.0, "target_label": "1R (one times the risk)",
        "risk_per_share": 8.9, "reward_risk": 1.0, "flat_by": "15:05",
        "note": "A stop is a plan, not a guarantee: a gap or a fast market can fill past it."}


def main():
    for sym, name, fetched, (o, h, lo, c, v), prev in QUOTES:
        bar = (ms.ts(DT.combine(fetched.date(), datetime.time(9, 15))), o, h, lo, c, v)
        doc = ms.chart_doc(sym, [bar], fetched, "1d", prev, gran="1d")
        meta = doc["chart"]["result"][0]["meta"]
        meta["longName"], meta["shortName"] = name, name.upper()
        ms.write(os.path.join(HERE, "yahoo-%s.json" % sym), doc)
    ms.write(os.path.join(HERE, "yahoo-ZZZZ.json"), NOT_FOUND)
    for sym, name, fetched, (o, h, lo, c, v), prev in QUOTES[1:]:
        bar = (ms.ts(DT.combine(fetched.date(), datetime.time(9, 15))), o, h, lo, c, v)
        doc = ms.kite_quote(sym, ms.KITE_TOKENS[sym], c, prev, [bar], fetched)
        q = doc["data"]["NSE:" + sym]
        q["average_price"] = round((o + h + lo + c) / 4, 2)          # sane numbers for these stocks
        q["lower_circuit_limit"], q["upper_circuit_limit"] = round(prev * 0.8, 2), round(prev * 1.2, 2)
        ms.write(os.path.join(HERE, "kite-quote-%s-synthetic.json" % sym), doc)
    with open(os.path.join(HERE, "plan-demo.json"), "w", encoding="utf-8") as fh:
        json.dump(PLAN, fh, indent=2)
        fh.write("\n")
    print("wrote %d quote files and plan-demo.json to %s" % (len(QUOTES) + 1, HERE))


if __name__ == "__main__":
    main()
