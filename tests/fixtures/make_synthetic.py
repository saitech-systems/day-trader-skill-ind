#!/usr/bin/env python3
"""Builds the SYNTHETIC market data the tests share. Not market data.

    python3 tests/fixtures/make_synthetic.py      # rewrites tests/fixtures/shared/

The India edition's tests could not reach Yahoo or NSE when they were written,
so the trade-path fixtures are made here from a seeded random walk, in Yahoo's
chart JSON v8 shape, for a made-up NSE stock: DEMO (Yahoo "DEMO.NS"). Same seed,
same files, on any machine.

  shared/DEMO-60d5m-synthetic.json.gz  60 sessions of 5-minute bars, 2026-07-24
                                       to Monday 2026-10-19, as a range=60d answer
                                       "fetched" at 10:49:45 IST on 2026-10-19: it
                                       ends with the in-progress 10:45 bar and a
                                       live tick at 10:49:45.
  shared/DEMO-2026-10-19-5m-synthetic.json   the same Monday only, as range=1d.

Monday 2026-10-19 is written by hand so the story is easy to follow: the
opening range (9:15, 9:20, 9:25 bars) is 1351.30 to 1363.80; the 9:35 bar
wicks above the high, the 9:40 bar closes exactly AT it, and the 9:55 bar
(bar 8) closes above it at 1366.40, at 10:00 AM. The day before (Friday
2026-10-16) closed at 1352.40. Tuesday 2026-10-20 is an NSE holiday (Dussehra).

Other skills build their own synthetic files with the helpers here (see each
test module's docstring).
"""

import datetime
import gzip
import json
import math
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "shared")
IST = datetime.timedelta(hours=5, minutes=30)
EPOCH = datetime.datetime(1970, 1, 1)

NSE_HOLIDAYS = {"2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31", "2026-04-03",
                "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26", "2026-09-14", "2026-10-02",
                "2026-10-20", "2026-11-10", "2026-11-24", "2026-12-25"}
BARS = 75                     # 9:15 to 15:30, five-minute bars
DAY = datetime.date(2026, 10, 19)
PREV_CLOSE = 1352.40
FETCHED = datetime.datetime(2026, 10, 19, 10, 49, 45)    # IST

# Monday 2026-10-19, by hand: (start IST, open, high, low, close, volume)
MONDAY = [
    ("09:15", 1355.0, 1362.0, 1351.3, 1359.4, 412000),
    ("09:20", 1359.4, 1363.8, 1356.0, 1361.1, 268000),
    ("09:25", 1361.1, 1362.5, 1357.3, 1358.9, 231000),
    ("09:30", 1358.9, 1362.9, 1358.1, 1360.2, 190000),
    ("09:35", 1360.2, 1364.5, 1359.8, 1362.7, 205000),   # wick above the range high
    ("09:40", 1362.7, 1364.0, 1361.9, 1363.8, 176000),   # closes AT the range high
    ("09:45", 1363.8, 1364.2, 1360.6, 1361.5, 158000),
    ("09:50", 1361.5, 1363.3, 1360.9, 1362.9, 149000),
    ("09:55", 1362.9, 1367.0, 1362.4, 1366.4, 254000),   # closes above: the trigger, 10:00 AM
    ("10:00", 1366.4, 1368.9, 1365.2, 1368.1, 221000),
    ("10:05", 1368.1, 1369.0, 1365.9, 1366.6, 174000),
    ("10:10", 1366.6, 1367.8, 1364.7, 1365.3, 139000),
    ("10:15", 1365.3, 1366.9, 1363.5, 1366.2, 133000),
    ("10:20", 1366.2, 1370.4, 1365.8, 1369.7, 266000),
    ("10:25", 1369.7, 1372.5, 1369.0, 1371.6, 351000),   # 1.16x the range's volume
    ("10:30", 1371.6, 1372.0, 1368.4, 1369.0, 188000),
    ("10:35", 1369.0, 1370.2, 1367.1, 1368.5, 142000),
    ("10:40", 1368.5, 1369.9, 1367.7, 1369.3, 127000),
    ("10:45", 1369.3, 1370.0, 1368.6, 1369.8, 61000),    # still forming at 10:49:45
]
LIVE_TICK = 1369.85


def ts(local):
    """India time -> Unix seconds."""
    return int((local - IST - EPOCH).total_seconds())


def tick_of(p):
    for upto, t in ((250, 0.01), (1000, 0.05), (5000, 0.10), (10000, 0.50), (20000, 1.00)):
        if p < upto:
            return t
    return 5.00


def on_tick(p):
    t = tick_of(p)
    return round(round(p / t) * t, 2)


def trading_days(end, n):
    """The n NSE sessions up to and including `end`, oldest first."""
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5 and d.isoformat() not in NSE_HOLIDAYS:
            out.append(d)
        d -= datetime.timedelta(days=1)
    return out[::-1]


def walk_session(rng, day, open_price, vol_scale=1.0, base_volume=150000):
    """One full session of 75 five-minute bars from open_price: a random walk with
    a U-shaped volatility and volume over the day. Returns [(ts, o, h, l, c, v)]."""
    bars, price = [], open_price
    start = datetime.datetime.combine(day, datetime.time(9, 15))
    for k in range(BARS):
        u = abs(k - (BARS - 1) / 2.0) / ((BARS - 1) / 2.0)          # 1 at the ends, 0 midday
        sigma = (0.0011 + 0.0016 * u * u) * vol_scale
        o = price
        c = o * math.exp(rng.gauss(0.00002, sigma))
        h = max(o, c) * (1 + abs(rng.gauss(0, sigma * 0.6)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, sigma * 0.6)))
        o, h, lo, c = on_tick(o), on_tick(h), on_tick(lo), on_tick(c)
        h, lo = max(h, o, c), min(lo, o, c)
        v = int(base_volume * (0.45 + 1.6 * u * u) * rng.uniform(0.7, 1.3)) // 100 * 100
        bars.append((ts(start + datetime.timedelta(minutes=5 * k)), o, h, lo, c, v))
        price = c
    return bars


def chart_doc(symbol, bars, fetched_ist, rng_name, prev_close, chart_prev_close=None, gran="5m"):
    """A Yahoo chart JSON v8 answer for SYMBOL.NS from [(ts, o, h, l, c, v)]."""
    day = fetched_ist.date()
    start = ts(datetime.datetime.combine(day, datetime.time(9, 15)))
    end = ts(datetime.datetime.combine(day, datetime.time(15, 30)))
    last = bars[-1]
    today = [b for b in bars if b[0] >= start]
    period = {"timezone": "IST", "gmtoffset": 19800}
    meta = {
        "currency": "INR", "symbol": symbol + ".NS", "exchangeName": "NSI", "fullExchangeName": "NSE",
        "instrumentType": "EQUITY", "regularMarketTime": ts(fetched_ist), "hasPrePostMarketData": False,
        "gmtoffset": 19800, "timezone": "IST", "exchangeTimezoneName": "Asia/Kolkata",
        "regularMarketPrice": last[4], "longName": "Demo Industries Limited (synthetic)",
        "shortName": "DEMO INDUSTRIES (SYNTHETIC)",
        "regularMarketDayHigh": max(b[2] for b in today) if today else last[2],
        "regularMarketDayLow": min(b[3] for b in today) if today else last[3],
        "regularMarketVolume": sum(b[5] for b in today),
        "chartPreviousClose": chart_prev_close if chart_prev_close is not None else prev_close,
        "previousClose": prev_close, "scale": 3, "priceHint": 2,
        "currentTradingPeriod": {"pre": dict(period, start=start - 900, end=start),
                                 "regular": dict(period, start=start, end=end),
                                 "post": dict(period, start=end, end=end + 1800)},
        "dataGranularity": gran, "range": rng_name,
        "validRanges": ["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"],
    }
    q = {"open": [b[1] for b in bars], "high": [b[2] for b in bars], "low": [b[3] for b in bars],
         "close": [b[4] for b in bars], "volume": [b[5] for b in bars]}
    return {"chart": {"result": [{"meta": meta, "timestamp": [b[0] for b in bars],
                                  "indicators": {"quote": [q]}}], "error": None}}


def demo_history(seed=2026):
    """59 full sessions before Monday, scaled so Friday closes at 1352.40, then Monday."""
    rng = random.Random(seed)
    days = trading_days(DAY, 60)
    raw, price = [], 1250.0
    for d in days[:-1]:
        gap = math.exp(rng.gauss(0.0004, 0.006))
        sess = walk_session(rng, d, price * gap)
        raw.append(sess)
        price = sess[-1][4]
    scale = PREV_CLOSE / raw[-1][-1][4]
    hist = []
    for sess in raw:
        out = []
        for t, o, h, lo, c, v in sess:
            o, h, lo, c = (on_tick(x * scale) for x in (o, h, lo, c))
            out.append((t, o, max(h, o, c), min(lo, o, c), c, v))
        hist.append(out)
    hist[-1][-1] = hist[-1][-1][:4] + (PREV_CLOSE,) + hist[-1][-1][5:]
    t, o, h, lo, c, v = hist[-1][-1]
    hist[-1][-1] = (t, o, max(h, c), min(lo, c), c, v)
    monday = []
    for hhmm, o, h, lo, c, v in MONDAY:
        hh, mm = map(int, hhmm.split(":"))
        monday.append((ts(datetime.datetime.combine(DAY, datetime.time(hh, mm))), o, h, lo, c, v))
    monday.append((ts(FETCHED), LIVE_TICK, LIVE_TICK, LIVE_TICK, LIVE_TICK, 0))
    return days, hist, monday


def write(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = json.dumps(doc, separators=(",", ":"))
    if path.endswith(".gz"):
        with gzip.GzipFile(path, "wb", mtime=0) as fh:      # mtime 0: the same bytes every run
            fh.write(text.encode("utf-8"))
    else:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")


def main():
    days, hist, monday = demo_history()
    bars = [b for sess in hist for b in sess] + monday
    first_close = hist[0][0][1]
    write(os.path.join(OUT, "DEMO-60d5m-synthetic.json.gz"),
          chart_doc("DEMO", bars, FETCHED, "60d", PREV_CLOSE, chart_prev_close=first_close))
    write(os.path.join(OUT, "DEMO-2026-10-19-5m-synthetic.json"),
          chart_doc("DEMO", monday, FETCHED, "1d", PREV_CLOSE))
    print("wrote %d sessions (%s to %s) to %s" % (len(days), days[0], days[-1], OUT))


if __name__ == "__main__":
    main()
