#!/usr/bin/env python3
"""Builds trader-scanner's SYNTHETIC fixtures. Not market data.

    python3 tests/fixtures/trader-scanner/make_synthetic.py

Seeded, so the same files come out on any machine. It borrows the helpers and
DEMO's Monday from tests/fixtures/make_synthetic.py.

  wall5m-2026-10-19-synthetic.json.gz   a bundle {SYMBOL: v8 chart} of 33 made-up
      NSE symbols on Monday 2026-10-19, "fetched" at 10:49:45 IST (the 10:45 bar
      still forming). Each chart is written to a plan, so every outcome is known:
        hits (bar index k: the bar that first CLOSES above the range high, at
        9:15 + 5 x (k + 1) minutes): SYNA k=3 (9:35 AM, the first possible),
        SYNB 4, PENNY 4 (Rs 12, under the Rs 20 floor), SYNC 5, SYN&SONS 5,
        SYND 6, SYNE 7, PREOPEN 7, DEMO 8 (10:00 AM, 1366.40), SYNF 9, SYNG 10,
        SYNH 11 and SYNI 11 (10:15 AM), LATEBO 12 (10:20 AM), SYNJ 14 (10:30 AM)
        no breakout, every close after the range under the range's best close:
        SYNK SYNL SYNM SYNN SYNO SYNP SYNQ SYNR SYNS SYN-AUTO, and TICKY (its
        live tick at 10:49:45 is above the range high; a tick is not a bar)
        candidates without a hit: ATHIGH (a close exactly AT the range high),
        WICKY (wicks above, closes under)
        unusable: GAPPY (no 9:50 bar), HALTED (nothing after the 9:40 bar),
        NULLY (a null close at 9:45), EMPTY (no bars at all)
        no data: DELISTED (Yahoo's "No data found" answer)
        PREOPEN also has a 9:05 bar from the pre-open session, far above
        everything: it must be ignored.
  daily-spark-2026-10-19-synthetic.json  one spark answer (range 3mo, interval
      1d, closes only) "fetched" at 10:50 IST on Monday 2026-10-19: 61 daily
      bars each, the last one Monday's, still forming; meta volume is Monday's
      so far. One symbol per setup: BRKOUT (breakout_20d), FLAGGY (bull_flag),
      RECLAIM (reclaim_ma20), WEAKLING (oversold), BOUNCE (oversold_bounce),
      plus FLAT1, FLAT2, SYN&SONS (no setup), THIN (a breakout on 50,000 shares
      a day), CHEAP (a breakout at Rs 15), SHORTY (12 days), NULLS (null
      closes, flat) and DEMO (Friday closes of the shared DEMO history).
  spark-5m-2026-10-19-1001-synthetic.json and DEMO-5m-2026-10-19-1001-synthetic.json
      a 5-minute spark answer for DEMO, SYNA and PENNY and DEMO's v8 chart as if
      fetched at 10:01:47 IST: the 10:00 bar is still forming and a live tick
      sits at 10:01:47.
  lists/   NSE's list files in their own CSV shapes, trimmed: ind_nifty500list.csv
      (six real large caps by public name and ISIN, made-up synthetic rows, a BE
      row and a bad symbol), EQUITY_L.csv (padded headers; EQ, BE, BZ, SM and ST
      rows, all made up) and fo_mktlots.csv (space-padded cells, index rows, a
      separator row; made-up lots).
"""

import datetime
import gzip
import importlib.util
import json
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("shared_synth", os.path.join(os.path.dirname(HERE),
                                                                           "make_synthetic.py"))
ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ms)

DAY = ms.DAY                                              # Monday 2026-10-19
FETCHED = ms.FETCHED                                      # 10:49:45 IST
SLOTS = 19                                                # 9:15 ... 10:45 (the last still forming)


def at(hhmm, day=DAY):
    hh, mm = map(int, hhmm.split(":"))
    return ms.ts(datetime.datetime.combine(day, datetime.time(hh, mm)))


def slot_ts(k):
    return ms.ts(datetime.datetime.combine(DAY, datetime.time(9, 15)) + datetime.timedelta(minutes=5 * k))


def designed_day(rng, base, k_break=None, vol=40000, kind="plain"):
    """Monday's bars (slots 0..18) for one symbol, to plan. Returns [(ts, o, h, l, c, v)]."""
    t = ms.tick_of(base)
    on = ms.on_tick
    # the range: three bars, the best close well under the high
    closes = [on(base * (1 + rng.uniform(-0.002, 0.002))) for _ in range(3)]
    H = on(max(closes) + max(6 * t, base * 0.004))
    L = on(min(closes) - max(6 * t, base * 0.004))
    top = max(closes)
    bars, prev = [], on(base)
    for k in range(3):
        c = closes[k]
        h = H if k == 1 else on(max(prev, c) + t)
        lo = L if k == 0 else on(min(prev, c) - t)
        bars.append([slot_ts(k), prev, h, lo, c, int(vol * 2.2 * rng.uniform(0.8, 1.2)) // 100 * 100])
        prev = c
    lo_band = on(L + 0.25 * (top - L))
    for k in range(3, SLOTS):
        v = int(vol * rng.uniform(0.6, 1.2)) // 100 * 100
        if k_break is not None and k == k_break:
            c = on(H + max(t, H * 0.003))
            bars.append([slot_ts(k), prev, on(c + t), on(min(prev, c) - t), c, v * 2])
        elif k_break is not None and k > k_break:
            c = on(prev * (1 + rng.uniform(-0.0015, 0.0015)))
            bars.append([slot_ts(k), prev, on(max(prev, c) + t), on(min(prev, c) - t), c, v])
        else:
            c = on(rng.uniform(float(lo_band), float(top - t)))
            h = on(min(max(prev, c) + t, H - t))
            lo = on(min(prev, c) - t)
            if kind == "athigh" and k == 6:
                c, h = H, H
            if kind == "wicky" and k in (5, 7):
                c, h = on(top + t), on(H + 3 * t)
            bars.append([slot_ts(k), prev, max(h, prev, c), min(lo, prev, c), c, v])
        prev = c
    bars[-1][5] = bars[-1][5] // 3                            # the 10:45 bar is still forming
    return [tuple(b) for b in bars], H


def name_of(sym):
    return "%s Ltd. (synthetic)" % sym.title()


def v8(sym, bars, prev_close, rng_name="1d", fetched=FETCHED):
    doc = ms.chart_doc(sym, bars, fetched, rng_name, prev_close)
    meta = doc["chart"]["result"][0]["meta"]
    meta["longName"] = meta["shortName"] = name_of(sym)
    return doc


def wall():
    rng = random.Random(1019)
    plan = [
        ("SYNA", 412.0, 3), ("SYNB", 233.5, 4), ("PENNY", 12.4, 4), ("SYNC", 1520.0, 5), ("SYN&SONS", 688.0, 5),
        ("SYND", 96.2, 6), ("SYNE", 2410.0, 7), ("SYNF", 318.0, 9), ("SYNG", 54.75, 10), ("SYNH", 1088.0, 11),
        ("SYNI", 742.0, 11), ("LATEBO", 166.0, 12), ("SYNJ", 3105.0, 14),
        ("SYNK", 145.0, None), ("SYNL", 520.0, None), ("SYNM", 77.3, None), ("SYNN", 1999.0, None),
        ("SYNO", 260.0, None), ("SYNP", 905.0, None), ("SYNQ", 33.6, None), ("SYNR", 1245.0, None),
        ("SYNS", 410.0, None), ("SYN-AUTO", 2875.0, None),
    ]
    bundle = {}
    for sym, base, k in plan:
        bars, _ = designed_day(rng, base, k, vol=rng.choice([20000, 30000, 45000, 60000]))
        bundle[sym] = v8(sym, bars, ms.on_tick(base * 0.995))
    # DEMO: the shared Monday, live tick included
    _, hist, monday = ms.demo_history()
    bundle["DEMO"] = v8("DEMO", monday, ms.PREV_CLOSE)
    bundle["DEMO"]["chart"]["result"][0]["meta"]["longName"] = "Demo Industries Limited (synthetic)"
    # the odd ones
    bars, _ = designed_day(rng, 560.0, None, kind="athigh")
    bundle["ATHIGH"] = v8("ATHIGH", bars, 556.0)
    bars, _ = designed_day(rng, 1210.0, None, kind="wicky")
    bundle["WICKY"] = v8("WICKY", bars, 1203.0)
    bars, H = designed_day(rng, 880.0, None)
    tick = ms.on_tick(H + 2.0)
    bundle["TICKY"] = v8("TICKY", bars + [(ms.ts(FETCHED), tick, tick, tick, tick, 0)], 874.0)
    bars, _ = designed_day(rng, 450.0, 5)
    bundle["GAPPY"] = v8("GAPPY", [b for b in bars if b[0] != at("09:50")], 447.0)
    bars, _ = designed_day(rng, 199.0, None)
    bundle["HALTED"] = v8("HALTED", [b for b in bars if b[0] <= at("09:40")], 205.0)
    bars, _ = designed_day(rng, 1333.0, 6)
    doc = v8("NULLY", bars, 1325.0)
    q = doc["chart"]["result"][0]["indicators"]["quote"][0]
    i = doc["chart"]["result"][0]["timestamp"].index(at("09:45"))
    for key in ("open", "high", "low", "close"):
        q[key][i] = None
    q["volume"][i] = None
    bundle["NULLY"] = doc
    bars, H = designed_day(rng, 620.0, 7)
    pre = (at("09:05"), 640.0, 700.0, 640.0, 650.0, 9000)       # a pre-open print, never a bar of the session
    bundle["PREOPEN"] = v8("PREOPEN", [pre] + bars, 612.0)
    empty = v8("EMPTY", [(slot_ts(0), 100.0, 100.0, 100.0, 100.0, 0)], 100.0)
    r = empty["chart"]["result"][0]
    r["timestamp"] = []
    r["indicators"]["quote"] = [{}]
    bundle["EMPTY"] = empty
    bundle["DELISTED"] = {"chart": {"result": None, "error": {"code": "Not Found",
                                                              "description": "No data found, symbol may be delisted"}}}
    return dict(sorted(bundle.items()))


# ---------------------------------------------------------------- daily closes

def daily_series(kind, rng):
    """60 closes through Fri 2026-10-16, to plan, oldest first."""
    n = 60
    if kind == "breakout":
        c = [100 + 0.12 * i + rng.uniform(-0.6, 0.6) for i in range(n - 1)]
        c.append(max(c[-20:]) + 1.6)
    elif kind == "flag":
        c = [100 + rng.uniform(-0.5, 0.5) for _ in range(n - 15)]
        c += [100 + 1.6 * (i + 1) for i in range(10)]               # 101.6 ... 116.0
        c += [114.2, 115.1, 114.6, 115.3, 114.9]                     # a 1 % range, under the 116.0 high
    elif kind == "reclaim":
        c = [100 + rng.uniform(-0.4, 0.4) for _ in range(n - 21)]
        x = c[-1]
        for i in range(20):                                          # a choppy slide: down 1.0, up 0.7
            x += -1.0 if i % 2 == 0 else 0.7
            c.append(x)
        c.append(sum(c[-19:]) / 19.0 + 1.2)                          # back over the 20-day average
    elif kind == "oversold":
        c = [100 + rng.uniform(-0.4, 0.4) for _ in range(n - 26)]
        x = c[-1]
        for i in range(26):
            x -= -0.3 if i % 5 == 2 else 1.1                         # small bounces; the last day is down
            c.append(x)
    elif kind == "bounce":
        c = [100 + rng.uniform(-0.4, 0.4) for _ in range(n - 26)]
        x = c[-1]
        for i in range(25):
            x -= 1.1 if i % 6 else -0.25
            c.append(x)
        c.append(x + 6.5)
    else:                                                            # flat: no setup
        c = [100 + 0.05 * i + rng.uniform(-0.15, 0.15) for i in range(n - 1)]
        c.append(c[-1] - 0.4)                                        # a small dip: no new high
    return [round(x, 2) for x in c]


def spark_item(sym, closes, days, live_close, live_volume, price=None, fetched=None):
    fetched = fetched or datetime.datetime.combine(DAY, datetime.time(10, 50))
    tss = [ms.ts(datetime.datetime.combine(d, datetime.time(9, 15))) for d in days]
    meta = {"currency": "INR", "symbol": sym + ".NS", "exchangeName": "NSI", "fullExchangeName": "NSE",
            "instrumentType": "EQUITY", "regularMarketTime": ms.ts(fetched), "gmtoffset": 19800,
            "timezone": "IST", "exchangeTimezoneName": "Asia/Kolkata",
            "regularMarketPrice": price if price is not None else live_close,
            "regularMarketVolume": live_volume, "chartPreviousClose": closes[0], "previousClose": closes[-1],
            "dataGranularity": "1d", "range": "3mo"}
    return {"symbol": sym + ".NS", "response": [{"meta": meta, "timestamp": tss + [at("09:15")],
                                                 "indicators": {"quote": [{"close": closes + [live_close]}]}}]}


def daily():
    rng = random.Random(1016)
    days = ms.trading_days(datetime.date(2026, 10, 16), 60)
    items = []
    plan = [("BRKOUT", "breakout", 1.0, 900000), ("FLAGGY", "flag", 2.0, 650000),
            ("RECLAIM", "reclaim", 4.0, 1200000), ("WEAKLING", "oversold", 3.0, 480000),
            ("BOUNCE", "bounce", 6.0, 300000), ("FLAT1", "flat", 2.5, 700000), ("FLAT2", "flat", 0.9, 2500000),
            ("SYN&SONS", "flat", 6.9, 260000), ("THIN", "breakout", 3.2, 50000), ("CHEAP", "breakout", 0.15, 5000000)]
    for sym, kind, scale, vol in plan:
        closes = [round(x * scale, 2) for x in daily_series(kind, rng)]
        items.append(spark_item(sym, closes, days, round(closes[-1] * 1.004, 2), vol))
    short = [round(240 + i, 2) for i in range(12)]
    items.append(spark_item("SHORTY", short, days[-12:], 252.5, 900000))
    closes = daily_series("flat", rng)
    closes = [round(x * 3, 2) for x in closes]
    item = spark_item("NULLS", closes, days, closes[-1], 800000)
    q = item["response"][0]["indicators"]["quote"][0]["close"]
    q[10] = q[33] = None
    items.append(item)
    _, hist, monday = ms.demo_history()
    demo_closes = [sess[-1][4] for sess in hist]
    items.append(spark_item("DEMO", demo_closes, ms.trading_days(datetime.date(2026, 10, 16), len(demo_closes)),
                            monday[-1][4], sum(b[5] for b in monday)))
    items.sort(key=lambda x: x["symbol"])
    return {"spark": {"result": items, "error": None}}


def live_five():
    """DEMO, SYNA and PENNY as a spark 5-minute answer at 10:01:47, and DEMO's v8."""
    fetched = datetime.datetime.combine(DAY, datetime.time(10, 1, 47))
    cut = ms.ts(fetched)
    wall_doc = wall()
    _, _, monday = ms.demo_history()
    demo = [b for b in monday if b[0] < at("10:05")]
    o, h, lo, c, v = demo[-1][1:]
    demo[-1] = (demo[-1][0], o, min(h, 1367.6), max(lo, 1365.6), 1367.2, 41000)   # the 10:00 bar, 1:47 in
    demo.append((cut, 1367.2, 1367.2, 1367.2, 1367.2, 0))
    items = []
    for sym in ("DEMO", "PENNY", "SYNA"):
        if sym == "DEMO":
            bars = demo
        else:
            r = wall_doc[sym]["chart"]["result"][0]
            q = r["indicators"]["quote"][0]
            bars = [(t, q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i])
                    for i, t in enumerate(r["timestamp"]) if t < at("10:05")]
            bars.append((cut,) + (bars[-1][4],) * 4 + (0,))
        meta = {"symbol": sym + ".NS", "currency": "INR", "exchangeTimezoneName": "Asia/Kolkata",
                "regularMarketTime": cut, "regularMarketPrice": bars[-1][4],
                "regularMarketVolume": sum(b[5] for b in bars), "dataGranularity": "5m", "range": "1d"}
        items.append({"symbol": sym + ".NS", "response": [{"meta": meta, "timestamp": [b[0] for b in bars],
                                                           "indicators": {"quote": [{"close": [b[4] for b in bars]}]}}]})
    spark = {"spark": {"result": items, "error": None}}
    return spark, v8("DEMO", demo, ms.PREV_CLOSE, fetched=fetched)


# ---------------------------------------------------------------- NSE list files

NIFTY500 = """Company Name,Industry,Symbol,Series,ISIN Code
Bajaj Auto Ltd.,Automobile and Auto Components,BAJAJ-AUTO,EQ,INE917I01010
Brkout Ltd. (synthetic),Capital Goods,BRKOUT,EQ,INESYN000011
Demo Industries Ltd. (synthetic),Capital Goods,DEMO,EQ,INESYN000001
Flaggy Ltd. (synthetic),Chemicals,FLAGGY,EQ,INESYN000012
HDFC Bank Ltd.,Financial Services,HDFCBANK,EQ,INE040A01034
Infosys Ltd.,Information Technology,INFY,EQ,INE009A01021
Mahindra & Mahindra Ltd.,Automobile and Auto Components,M&M,EQ,INE101A01026
Reclaim Ltd. (synthetic),Healthcare,RECLAIM,EQ,INESYN000013
Reliance Industries Ltd.,Oil Gas & Consumable Fuels,RELIANCE,EQ,INE002A01018
Syn & Sons Ltd. (synthetic),Consumer Durables,SYN&SONS,EQ,INESYN000014
Syna Ltd. (synthetic),Power,SYNA,EQ,INESYN000015
Tata Consultancy Services Ltd.,Information Technology,TCS,EQ,INE467B01029
Tftonly Ltd. (synthetic),Textiles,TFTONLY,BE,INESYN000016
Bad Row Ltd. (synthetic),Services,BAD SYM,EQ,INESYN000017
"""

NIFTY50 = """Company Name,Industry,Symbol,Series,ISIN Code
HDFC Bank Ltd.,Financial Services,HDFCBANK,EQ,INE040A01034
Infosys Ltd.,Information Technology,INFY,EQ,INE009A01021
Reliance Industries Ltd.,Oil Gas & Consumable Fuels,RELIANCE,EQ,INE002A01018
"""

EQUITY_L = """SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE
3SYNTH,3 Synth Limited (synthetic),EQ,02-JAN-2001,10,1,INESYN000021,10
DEMO,Demo Industries Limited (synthetic),EQ,15-MAR-1999,10,1,INESYN000001,10
SYN&SONS,Syn & Sons Limited (synthetic),EQ,09-AUG-2010,2,1,INESYN000014,2
SYN-AUTO,Syn-Auto Limited (synthetic),EQ,21-JUN-2015,5,1,INESYN000022,5
SYNA,Syna Limited (synthetic),EQ,11-NOV-2011,10,1,INESYN000015,10
TFTONLY,Tftonly Limited (synthetic),BE,01-APR-2005,10,1,INESYN000016,10
ZEROS,Zeros Limited (synthetic),BZ,07-JUL-2007,10,1,INESYN000023,10
SMEONE,Sme One Limited (synthetic),SM,30-SEP-2020,10,1,INESYN000024,10
SMETWO,Sme Two Limited (synthetic),ST,04-DEC-2021,10,1,INESYN000025,10
DEMO,Demo Industries Limited (synthetic),EQ,15-MAR-1999,10,1,INESYN000001,10
"""


def pad(cells, widths):
    return ",".join(c.ljust(w) for c, w in zip(cells, widths))


def fo_mktlots():
    widths = [50, 10, 10, 10, 10]
    rows = [["UNDERLYING", "SYMBOL", "OCT-26", "NOV-26", "DEC-26"],
            ["NIFTY 50", "NIFTY", "75", "75", "75"],
            ["NIFTY BANK", "BANKNIFTY", "35", "35", "35"],
            ["NIFTY FINANCIAL SERVICES", "FINNIFTY", "65", "65", "65"],
            ["NIFTY MIDCAP SELECT", "MIDCPNIFTY", "140", "140", "140"],
            ["NIFTY NEXT 50", "NIFTYNXT50", "25", "25", "25"],
            ["Derivatives on Individual Securities", "Symbol", "", "", ""],
            ["DEMO INDUSTRIES LTD (SYNTHETIC)", "DEMO", "550", "550", "550"],
            ["SYN & SONS LTD (SYNTHETIC)", "SYN&SONS", "1200", "1200", "1200"],
            ["SYN-AUTO LTD (SYNTHETIC)", "SYN-AUTO", "300", "300", "300"],
            ["SYNA LTD (SYNTHETIC)", "SYNA", "2000", "2000", "2000"]]
    return "\n".join(pad(r, widths) for r in rows) + "\n"


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text.replace("\n", "\r\n") if path.endswith(".csv") else text)


def main():
    ms.write(os.path.join(HERE, "wall5m-2026-10-19-synthetic.json.gz"), wall())
    ms.write(os.path.join(HERE, "daily-spark-2026-10-19-synthetic.json"), daily())
    spark, demo = live_five()
    ms.write(os.path.join(HERE, "spark-5m-2026-10-19-1001-synthetic.json"), spark)
    ms.write(os.path.join(HERE, "DEMO-5m-2026-10-19-1001-synthetic.json"), demo)
    lists = os.path.join(HERE, "lists")
    write_text(os.path.join(lists, "ind_nifty500list.csv"), NIFTY500)
    write_text(os.path.join(lists, "ind_nifty50list.csv"), NIFTY50)
    write_text(os.path.join(lists, "EQUITY_L.csv"), EQUITY_L)
    write_text(os.path.join(lists, "fo_mktlots.csv"), fo_mktlots())
    print("wrote trader-scanner fixtures to %s" % HERE)


if __name__ == "__main__":
    main()
