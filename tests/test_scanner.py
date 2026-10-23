"""trader-scanner tests. Every chart here is SYNTHETIC (not market data), built by
tests/fixtures/trader-scanner/make_synthetic.py to a plan written in its docstring,
so each hit, miss and unusable chart is known before the scanner runs. DEMO's
Monday is the shared story (tests/fixtures/make_synthetic.py): range 1351.30 to
1363.80, the 9:55 bar closes 1366.40 at 10:00 AM. The NSE list files in lists/ are
in NSE's own CSV shapes, trimmed, with made-up rows marked (synthetic).
"""

import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
import urllib.parse
from contextlib import redirect_stdout, redirect_stderr

from _kite import FakeKite
from _load import load, FIX

scan = load("trader-scanner", "scan")

SDIR = os.path.join(FIX, "trader-scanner")
WALL = os.path.join(SDIR, "wall5m-2026-10-19-synthetic.json.gz")          # 33 charts, Mon 2026-10-19
DAILY = os.path.join(SDIR, "daily-spark-2026-10-19-synthetic.json")       # 13 daily charts, one spark answer
LISTS = os.path.join(SDIR, "lists")                                         # NSE's list files, trimmed
SPARK5 = os.path.join(SDIR, "spark-5m-2026-10-19-1001-synthetic.json")    # "fetched" 10:01:47 IST
DEMO5 = os.path.join(SDIR, "DEMO-5m-2026-10-19-1001-synthetic.json")      # DEMO's v8, fetched with it
KITE = os.path.join(SDIR, "kite-2026-10-19-synthetic.json.gz")             # the same charts as Kite answers
TOKEN = "demo_access_token"                                                 # FakeKite's session token
DT = datetime.datetime
D = datetime.date
UNTIL = datetime.time(14, 45)
HITS_1015 = ["SYNA", "PENNY", "SYNB", "SYN&SONS", "SYNC", "SYND", "PREOPEN", "SYNE", "DEMO", "SYNF", "SYNG",
             "SYNH", "SYNI"]
DEMO_HIT = {"symbol": "DEMO", "setups": ["orb"], "price": 1366.4, "at": "10:00 AM", "bar_start": "09:55",
            "bar_index": 8, "or_high": 1363.8, "or_low": 1351.3, "pct_over": 0.19}


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = scan.main(argv)
    return code, out.getvalue(), err.getvalue()


def ts(ist_text):
    """India wall-clock text -> unix seconds (through the tool's own clock)."""
    utc = scan.ist_to_utc(DT.strptime(ist_text, "%Y-%m-%d %H:%M:%S" if ist_text.count(":") == 2
                                      else "%Y-%m-%d %H:%M"))
    return scan.ts_of(utc)


def bars_series(day, closes, highs=None, lows=None, start="09:15", extra=None):
    """A v8-like series of 5-min bars from `start` on `day` (YYYY-MM-DD)."""
    t0 = DT.strptime(day + " " + start, "%Y-%m-%d %H:%M")
    t = [ts((t0 + datetime.timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M")) for i in range(len(closes))]
    s = {"t": t, "c": list(closes), "h": list(highs or [c + 0.1 if c is not None else None for c in closes]),
         "l": list(lows or [c - 0.1 if c is not None else None for c in closes]), "o": None,
         "v": [1000] * len(closes), "meta": {}}
    for when, c in (extra or []):
        s["t"].append(ts(when))
        for k in ("c", "h", "l"):
            s[k].append(c)
        s["v"].append(10)
    return s


def read_list(name):
    with open(os.path.join(LISTS, name), encoding="utf-8", newline="") as fh:
        return fh.read()


class Universe(unittest.TestCase):
    def test_the_nifty_500_list(self):
        kind, kept, counts = scan.build_universe(read_list("ind_nifty500list.csv"))
        self.assertEqual(kind, "index")
        self.assertEqual([s for s, _ in kept], ["BAJAJ-AUTO", "BRKOUT", "DEMO", "FLAGGY", "HDFCBANK", "INFY", "M&M",
                                                "RECLAIM", "RELIANCE", "SYN&SONS", "SYNA", "TCS"])
        self.assertEqual(counts, {"rows": 14, "keep": 12, "series BE (trade for trade: no intraday)": 1,
                                  "not an NSE symbol": 1})
        self.assertEqual(dict(kept)["M&M"], "Mahindra & Mahindra Ltd.")
        self.assertEqual(dict(kept)["RELIANCE"], "Reliance Industries Ltd.")

    def test_every_eq_stock(self):
        # EQUITY_L.csv pads its headers (" SERIES"); BE / BZ trade for trade, SM / ST are SME listings
        kind, kept, counts = scan.build_universe(read_list("EQUITY_L.csv"))
        self.assertEqual(kind, "all")
        self.assertEqual([s for s, _ in kept], ["3SYNTH", "DEMO", "SYN&SONS", "SYN-AUTO", "SYNA"])
        self.assertEqual(counts["series BE (trade for trade: no intraday)"], 1)
        self.assertEqual(counts["series BZ (trade for trade: no intraday)"], 1)
        self.assertEqual((counts["series SM"], counts["series ST"], counts["listed twice"]), (1, 1, 1))

    def test_the_fno_list(self):
        # fo_mktlots.csv: space-padded cells, index derivatives first, a "Symbol" separator row
        kind, kept, counts = scan.build_universe(read_list("fo_mktlots.csv"))
        self.assertEqual(kind, "fno")
        self.assertEqual([s for s, _ in kept], ["DEMO", "SYN&SONS", "SYN-AUTO", "SYNA"])
        self.assertEqual(counts, {"rows": 10, "index derivative": 5, "header row": 1, "keep": 4})

    def test_one_case_per_rule(self):
        cases = [
            ({"SYMBOL": "RELIANCE", "SERIES": "EQ"}, "index", "keep"),
            ({"SYMBOL": "M&M", "SERIES": "EQ"}, "index", "keep"),                  # & is an NSE symbol character
            ({"SYMBOL": "BAJAJ-AUTO", "SERIES": "EQ"}, "all", "keep"),             # and so is -
            ({"SYMBOL": "3MINDIA", "SERIES": "EQ"}, "all", "keep"),                # a digit first
            ({"SYMBOL": "ABC", "SERIES": "BE"}, "all", "series BE (trade for trade: no intraday)"),
            ({"SYMBOL": "ABC", "SERIES": "SM"}, "all", "series SM"),
            ({"SYMBOL": "ABC LTD", "SERIES": "EQ"}, "index", "not an NSE symbol"),
            ({"SYMBOL": "ABC.NS", "SERIES": "EQ"}, "index", "not an NSE symbol"),
            ({"UNDERLYING": "NIFTY BANK", "SYMBOL": "BANKNIFTY"}, "fno", "index derivative"),
            ({"UNDERLYING": "NIFTY IT", "SYMBOL": "NIFTYIT"}, "fno", "index derivative"),
            ({"UNDERLYING": "Derivatives on Individual Securities", "SYMBOL": "Symbol"}, "fno", "header row"),
            ({"UNDERLYING": "SOME LTD", "SYMBOL": "SOME"}, "fno", "keep"),
        ]
        for row, kind, want in cases:
            self.assertEqual(scan.classify(row, kind), want, row)

    def test_not_an_nse_list(self):
        for text in ("<html>Access Denied</html>", "", "a,b,c\n1,2,3\n"):
            with self.assertRaises(scan.ScanError):
                scan.build_universe(text)

    def test_crlf_and_a_byte_order_mark(self):
        text = "﻿Company Name,Industry,Symbol,Series,ISIN Code\r\nX Ltd.,Power,XYZ,EQ,INESYN000099\r\n"
        self.assertEqual(scan.build_universe(text)[1], [("XYZ", "X Ltd.")])


class Time(unittest.TestCase):
    def test_india_time_has_no_daylight_saving(self):
        self.assertEqual(scan.ts_to_ist(ts("2026-10-19 09:15")), DT(2026, 10, 19, 9, 15))
        self.assertEqual(scan.parse_now("2026-10-19 10:15"), DT(2026, 10, 19, 4, 45))   # UTC
        self.assertEqual(scan.parse_now("2026-01-19 10:15"), DT(2026, 1, 19, 4, 45))    # the same in winter
        self.assertEqual(scan.parse_now("2026-10-19T04:45:00Z"), DT(2026, 10, 19, 4, 45))
        self.assertEqual(scan.parse_now("2026-10-19 10:15 IST"), DT(2026, 10, 19, 4, 45))
        self.assertEqual(ts("2026-10-19 09:15") % 300, 0)                                # 9:15 IST is on the grid
        self.assertEqual(scan.clock_label(DT(2026, 10, 19, 10, 15)), "10:15 AM")
        self.assertEqual(scan.clock_label(datetime.time(12, 5)), "12:05 PM")
        with self.assertRaises(scan.ScanError):
            scan.parse_now("tomorrow")
        with self.assertRaises(scan.ScanError):
            scan.parse_hhmm("25:00")

    def test_the_nse_calendar(self):
        self.assertEqual(scan.trading_day(D(2026, 10, 19)), (True, ""))
        self.assertEqual(scan.trading_day(D(2026, 10, 20)), (False, "NSE holiday: Dussehra"))
        self.assertEqual(scan.trading_day(D(2026, 10, 17)), (False, "weekend"))
        self.assertEqual(scan.session(D(2026, 10, 19)), (datetime.time(9, 15), datetime.time(15, 30)))
        self.assertIsNone(scan.session(D(2026, 11, 10)))                                # Diwali Balipratipada
        self.assertEqual(scan.session(D(2026, 11, 8)), (datetime.time(14, 20), datetime.time(15, 30)))  # Muhurat
        self.assertTrue(scan.trading_day(D(2026, 2, 1))[0])                             # the Sunday Budget session
        self.assertEqual(scan.next_open(D(2026, 10, 19)), "2026-10-21 09:15")
        self.assertEqual(scan.next_open(D(2026, 11, 6)), "2026-11-08 14:20")


class Symbols(unittest.TestCase):
    def test_nse_symbols(self):
        self.assertEqual(scan.nse_symbol("reliance"), "RELIANCE")
        self.assertEqual(scan.nse_symbol("RELIANCE.NS"), "RELIANCE")
        self.assertEqual(scan.nse_symbol("NSE:m&m"), "M&M")
        with self.assertRaises(scan.ScanError) as cm:
            scan.nse_symbol("500325.BO")
        self.assertIn("BSE", str(cm.exception))
        with self.assertRaises(scan.ScanError):
            scan.nse_symbol("TSLA US")

    def test_urls(self):
        self.assertEqual(scan.spark_path(["M&M", "bajaj-auto"], "3mo", "1d"),
                         "/v7/finance/spark?symbols=M%26M.NS,BAJAJ-AUTO.NS&range=3mo&interval=1d")
        self.assertEqual(scan.chart_path("RELIANCE", "1d", "5m"),
                         "/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m")


class Spark(unittest.TestCase):
    def test_a_spark_answer(self):
        with open(DAILY, encoding="utf-8") as fh:
            text = fh.read()
        wanted = {scan.yahoo_symbol(s): s for s in ["DEMO", "SYN&SONS", "ZZZZQX"]}
        data = scan.parse_spark(text, wanted)
        self.assertEqual(len(data), 13)
        self.assertNotIn("ZZZZQX", data)          # Yahoo leaves out what it does not know
        self.assertIn("SYN&SONS", data)           # SYN&SONS.NS comes back as ours
        d = data["DEMO"]
        self.assertEqual(len(d["t"]), 60)         # 59 closes to Fri 16 Oct, then Monday's bar
        self.assertIsNone(d["h"])                 # spark is closes only
        self.assertEqual(d["c"][-2], 1352.4)
        self.assertEqual(d["meta"]["regularMarketPrice"], 1369.85)
        self.assertEqual(d["meta"]["regularMarketVolume"], 3845000)
        closes = scan.daily_closes(d, scan.parse_now("2026-10-19 09:00"))
        self.assertEqual(closes[-1], (D(2026, 10, 16), 1352.4))
        # Monday's bar counts once the session is over at 3:30 PM
        self.assertEqual(scan.daily_closes(d, scan.parse_now("2026-10-19 15:29"))[-1][0], D(2026, 10, 16))
        self.assertEqual(scan.daily_closes(d, scan.parse_now("2026-10-19 15:30"))[-1], (D(2026, 10, 19), 1369.85))

    def test_spark_errors(self):
        too_many = '{"spark":{"result":null,"error":{"code":"Bad Request",' \
                   '"description":"Number of symbols needs to be less than or equal to 20"}}}'
        with self.assertRaises(scan.ScanError) as cm:
            scan.parse_spark(too_many)
        self.assertIn("less than or equal to 20", str(cm.exception))
        self.assertEqual(scan.parse_spark('{"spark":{"result":[],"error":null}}'), {})
        with self.assertRaises(scan.ScanError):
            scan.parse_spark('{"chart":{}}')


class Network(unittest.TestCase):
    """429s and 5xx without the network."""

    def setUp(self):
        self.calls, self.sleeps = [], []
        self._get, self._sleep = scan.http_get, scan.SLEEP
        scan.SLEEP = self.sleeps.append
        with open(DAILY, encoding="utf-8") as fh:
            self.body = fh.read()

    def tearDown(self):
        scan.http_get, scan.SLEEP = self._get, self._sleep

    def answers(self, codes):
        codes = list(codes)

        def fake(url, timeout=20, ua=None):
            self.calls.append(url.split("/v")[0])
            code = codes.pop(0) if codes else 200
            if code != 200:
                raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
            return self.body
        scan.http_get = fake

    def test_429_falls_back_then_waits_once(self):
        self.answers([429, 429, 200])
        text = scan.yahoo_get("/v7/finance/spark?symbols=DEMO.NS&range=3mo&interval=1d")
        self.assertEqual(text, self.body)
        self.assertEqual(self.calls, ["https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com",
                                      "https://query2.finance.yahoo.com"])
        self.assertEqual(self.sleeps, [scan.BACKOFF])

    def test_5xx_goes_to_query1_without_waiting(self):
        self.answers([503, 200])
        scan.yahoo_get("/v8/finance/chart/DEMO.NS?range=1d&interval=5m")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.sleeps, [])

    def test_429_for_good_is_reported_as_partial(self):
        self.answers([200] + [429] * 10)
        syms = ["BOUNCE", "BRKOUT", "CHEAP", "DEMO", "FLAGGY", "FLAT1", "FLAT2", "NULLS", "RECLAIM", "SHORTY",
                "SYN&SONS", "THIN", "WEAKLING", "ZZZZQX", "RELIANCE", "TCS", "INFY", "HDFCBANK", "M&M",
                "BAJAJ-AUTO"] + ["X%02d" % i for i in range(20)]
        data, failed = scan.fetch_spark(syms, "3mo", "1d", 0.3)
        self.assertEqual(len(data), 13)
        self.assertEqual(failed["ZZZZQX"], "no data from Yahoo")
        self.assertEqual(len(failed), 27)
        self.assertIn("HTTP 429", failed["X00"])
        self.assertEqual(self.sleeps, [0.3, scan.BACKOFF])  # a pause between batches, one backoff
        hits, counts, _ = scan.scan_daily(data, syms, scan.parse_now("2026-10-19 09:00"), 20, 200000)
        self.assertEqual(counts["requested"], 40)
        self.assertEqual(counts["fetched"], 13)

    def test_a_404_is_final(self):
        self.answers([404])
        with self.assertRaises(scan.ScanError):
            scan.yahoo_get("/v8/finance/chart/NOPE.NS?range=1d&interval=5m")
        self.assertEqual(len(self.calls), 1)

    def test_nse_refuses_the_script(self):
        def fake(url, timeout=20, ua=None):
            self.calls.append((url, ua))
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, io.BytesIO(b""))
        scan.http_get = fake
        code, _, err = run(["universe", "--no-write"])
        self.assertEqual(code, 1)
        self.assertEqual(self.calls[0][0], "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv")
        self.assertIn("Mozilla/5.0 (Windows", self.calls[0][1])       # a browser User-Agent for NSE's files
        self.assertIn("HTTP 403", err)
        self.assertIn("universe --offline FOLDER", err)
        self.assertNotIn("Traceback", err)


class Daily(unittest.TestCase):
    # Each chart is made to plan (see make_synthetic.py); the numbers are from its closes, through Fri 16 Oct.
    EXPECT = {
        "BRKOUT": ["breakout_20d"],      # 108.69 over 107.09, the best close of the 20 before
        "FLAGGY": ["bull_flag"],         # up 16.0 % over the 10 sessions, then the last 5 closes in a 1.0 % range
        "RECLAIM": ["reclaim_ma20"],     # 387.12 under its 391.12 average, then 395.72 over 391.15
        "WEAKLING": ["oversold"],        # RSI 7.3, and the last day is down
        "BOUNCE": ["oversold_bounce"],   # RSI 8.1 on Thursday, closed up Friday: RSI 43.0
        "THIN": ["breakout_20d"],
        "CHEAP": ["breakout_20d"],
    }

    def setUp(self):
        self.data, self.errors = scan.load_offline(DAILY)
        self.syms = sorted(self.data)

    def test_setups_before_the_open(self):
        self.assertEqual(len(self.syms), 13)
        hits, counts, last = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-10-19 09:00"), 0, 0)
        self.assertEqual({h["symbol"]: h["setups"] for h in hits}, self.EXPECT)
        self.assertEqual(last, D(2026, 10, 16))
        self.assertEqual(counts, {"requested": 13, "fetched": 13, "illiquid": 0, "short": 1, "scanned": 12,
                                  "hits": 7})
        brk = [h for h in hits if h["symbol"] == "BRKOUT"][0]
        self.assertEqual((brk["price"], brk["rsi"], brk["close_date"], brk["volume"]),
                         (108.69, 69.6, "2026-10-16", 900000))
        self.assertEqual(hits[0]["symbol"], "RECLAIM")              # most rupees traded first

    def test_the_in_progress_bar_is_dropped(self):
        before = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-10-19 09:00"), 0, 0)
        during = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-10-19 10:50"), 0, 0)
        self.assertEqual(before, during)

    def test_a_special_session_bar_completes_at_its_close(self):
        # Muhurat trading, Sunday 8 Nov 2026, 2:20 to 3:30 PM
        s = {"t": [ts("2026-11-06 09:15"), ts("2026-11-08 14:20")], "c": [10.0, 11.0], "meta": {}}
        self.assertEqual(len(scan.daily_closes(s, scan.parse_now("2026-11-08 15:29"))), 1)
        self.assertEqual(len(scan.daily_closes(s, scan.parse_now("2026-11-08 15:30"))), 2)

    def test_liquidity_filter(self):
        # the defaults: Rs 20 and 2,00,000 shares; meta is Monday's as of 10:50 AM, so the volume is so far
        hits, counts, _ = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-10-19 10:50"), 20, 200000)
        self.assertEqual(sorted(h["symbol"] for h in hits), ["BOUNCE", "BRKOUT", "FLAGGY", "RECLAIM", "WEAKLING"])
        self.assertEqual(counts["illiquid"], 2)     # CHEAP under Rs 20, THIN on 50,000 shares
        self.assertEqual(counts["scanned"], 10)

    def test_short_and_empty_charts(self):
        s = {"t": [ts("2026-10-%02d 09:15" % d) for d in range(5, 17) if d not in (10, 11)][:10],
             "c": [100.0] * 10, "meta": {"regularMarketPrice": 100, "regularMarketVolume": 5000000}}
        hits, counts, last = scan.scan_daily({"X": s, "E": {"t": [], "c": [], "meta": {}}}, ["X", "E", "GONE"],
                                             scan.parse_now("2026-10-19 09:00"), 20, 200000)
        self.assertEqual(hits, [])
        self.assertEqual((counts["fetched"], counts["short"], counts["illiquid"], counts["scanned"]), (2, 1, 1, 0))
        self.assertIsNone(last)

    def test_null_closes_are_dropped(self):
        bars = scan.daily_closes(self.data["NULLS"], scan.parse_now("2026-10-19 09:00"))
        self.assertEqual(len(bars), 58)             # 60 closes, two of them null

    def test_rsi_matches_the_textbook(self):
        self.assertIsNone(scan.rsi([1.0] * 10))
        self.assertEqual(scan.rsi([float(x) for x in range(1, 40)]), 100.0)
        closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61,
                  46.28, 46.28]
        self.assertAlmostEqual(scan.rsi(closes), 70.46, places=1)  # Wilder's textbook example


class Orb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.errors = scan.load_offline(WALL)
        cls.syms = sorted(set(cls.data) | set(cls.errors))

    def scan_at(self, now, min_price=0, syms=None, or_minutes=15):
        day, cutoff = scan.orb_window(scan.parse_now(now), None, or_minutes, UNTIL)
        return scan.scan_orb(self.data, syms or self.syms, day, cutoff, or_minutes, min_price, 0)

    def test_the_1015_scan(self):
        hits, counts, failed = self.scan_at("2026-10-19 10:15")
        self.assertEqual(len(self.syms), 33)
        self.assertEqual(self.errors, {"DELISTED": "no data for DELISTED: No data found, symbol may be delisted"})
        self.assertEqual((counts["fetched"], counts["unusable"], counts["scanned"]), (32, 4, 28))
        self.assertEqual((counts["candidates"], counts["confirmed"]), (15, 15))   # 13 hits, ATHIGH, WICKY
        self.assertEqual([h["symbol"] for h in hits], HITS_1015)                 # by time, then symbol
        self.assertEqual([h for h in hits if h["symbol"] == "DEMO"][0], DEMO_HIT)
        self.assertEqual(hits[0]["at"], "9:35 AM")                               # the first bar after the range
        self.assertEqual([h["at"] for h in hits[-2:]], ["10:15 AM", "10:15 AM"])
        mm = [h for h in hits if h["symbol"] == "SYN&SONS"][0]
        self.assertEqual((mm["at"], mm["bar_index"]), ("9:45 AM", 5))

    def test_price_floor(self):
        hits, counts, _ = self.scan_at("2026-10-19 10:15", min_price=20)
        self.assertEqual(len(hits), 12)                    # PENNY at Rs 12.50 drops out
        self.assertEqual(counts["illiquid"], 1)
        self.assertNotIn("PENNY", [h["symbol"] for h in hits])

    def test_in_progress_bar_and_no_breakout_yet(self):
        hits, _, _ = self.scan_at("2026-10-19 09:59", syms=["DEMO"])
        self.assertEqual(hits, [])                         # the 9:55 bar is not done until 10:00
        hits, _, _ = self.scan_at("2026-10-19 10:00", syms=["DEMO"])
        self.assertEqual(hits, [DEMO_HIT])

    def test_later_breakouts(self):
        hits, counts, _ = self.scan_at("2026-10-19 10:30")
        self.assertEqual(len(hits), 15)
        self.assertEqual([(h["symbol"], h["at"]) for h in hits[-2:]], [("LATEBO", "10:20 AM"), ("SYNJ", "10:30 AM")])

    def test_the_live_tick_is_dropped(self):
        s = self.data["DEMO"]
        self.assertEqual(scan.ts_to_ist(s["t"][-1]), DT(2026, 10, 19, 10, 49, 45))   # off the grid
        bars = scan.session_bars(s, D(2026, 10, 19), DT(2026, 10, 19, 10, 49))
        self.assertEqual(len(bars), 18)                    # 9:15 to 10:40; 10:45 in progress, the tick dropped
        self.assertTrue(scan.usable(bars, D(2026, 10, 19), DT(2026, 10, 19, 10, 49), 3))
        hits, _, _ = self.scan_at("2026-10-19 10:49", syms=["DEMO", "TICKY"])
        self.assertEqual(hits, [DEMO_HIT])                 # still the first close above; TICKY's tick is no bar

    def test_close_at_the_high_and_a_wick_are_not_breakouts(self):
        day, cut = D(2026, 10, 19), DT(2026, 10, 19, 10, 49)
        for sym in ("ATHIGH", "WICKY"):
            bars = scan.session_bars(self.data[sym], day, cut)
            self.assertTrue(scan.orb_candidate(bars, 3), sym)
            self.assertIsNone(scan.orb_hit(bars, 3), sym)
        bars = scan.session_bars(self.data["ATHIGH"], day, cut)
        self.assertEqual(max(b["c"] for b in bars[3:]), max(b["h"] for b in bars[:3]))   # AT the high, not above

    def test_the_pre_open_print_is_ignored(self):
        s = self.data["PREOPEN"]
        self.assertEqual(scan.ts_to_ist(s["t"][0]), DT(2026, 10, 19, 9, 5))
        bars = scan.session_bars(s, D(2026, 10, 19), DT(2026, 10, 19, 10, 15))
        self.assertEqual(bars[0]["start"], DT(2026, 10, 19, 9, 15))
        hits, _, _ = self.scan_at("2026-10-19 10:15", syms=["PREOPEN"])
        self.assertEqual((hits[0]["at"], hits[0]["or_high"]), ("9:55 AM", 622.1))     # not the 9:05 print's 700

    def test_stage_one_is_a_necessary_condition(self):
        day, cut = D(2026, 10, 19), DT(2026, 10, 19, 10, 15)
        for sym, s in self.data.items():
            bars = scan.session_bars(s, day, cut)
            if scan.usable(bars, day, cut, 3) and scan.orb_hit(bars, 3):
                self.assertTrue(scan.orb_candidate(bars, 3), sym)

    def test_confirm_uses_the_v8_highs(self):
        spark_like = {k: dict(v, h=None, l=None) for k, v in self.data.items()}  # closes only, like spark
        asked = []

        def confirm(sym):
            asked.append(sym)
            return self.data[sym]
        hits, counts, failed = scan.scan_orb(spark_like, self.syms, D(2026, 10, 19), DT(2026, 10, 19, 10, 15), 15,
                                             0, 0, confirm)
        self.assertEqual([h["symbol"] for h in hits], HITS_1015)
        self.assertEqual(counts["candidates"], len(asked))
        self.assertEqual(len(asked), 15)                   # stage 1 skipped the other 13 usable charts

        def broken(sym):
            raise scan.ScanError("Yahoo answered HTTP 429 for /v8/finance/chart/%s.NS" % sym)
        hits, counts, failed = scan.scan_orb(spark_like, ["DEMO", "SYNK"], D(2026, 10, 19),
                                             DT(2026, 10, 19, 10, 15), 15, 0, 0, broken)
        self.assertEqual(hits, [])
        self.assertIn("429", failed["DEMO"])

    def test_unusable_charts(self):
        day, cut = D(2026, 10, 19), DT(2026, 10, 19, 10, 15)
        for sym in ("GAPPY", "HALTED", "NULLY", "EMPTY"):       # no 9:50 bar, halted, a null close, nothing
            self.assertFalse(scan.usable(scan.session_bars(self.data[sym], day, cut), day, cut, 3), sym)
        s = bars_series("2026-10-21", [10, 10.2, 10.1, None, 10.5, 10.6, 10.7, 10.8, 10.9])
        bars = scan.session_bars(s, D(2026, 10, 21), DT(2026, 10, 21, 10, 0))
        self.assertFalse(scan.usable(bars, D(2026, 10, 21), DT(2026, 10, 21, 10, 0), 3))

    def test_muhurat_session_and_a_30_minute_range(self):
        # Sunday 8 Nov 2026: Muhurat trading opens at 2:20 PM, so the range starts there
        closes = [100, 101, 100.5, 100.8, 101.2, 100.9, 101.5] + [101.0] * 6
        s = bars_series("2026-11-08", closes, highs=[c + 0.2 for c in closes], start="14:20")
        hits, counts, _ = scan.scan_orb({"X": s}, ["X"], D(2026, 11, 8), DT(2026, 11, 8, 15, 25), 30, 0, 0)
        self.assertEqual(counts["scanned"], 1)
        self.assertEqual(hits[0]["at"], "2:55 PM")          # range 2:20 to 2:50 (high 101.4), the 2:50 bar 101.5
        self.assertEqual(hits[0]["or_high"], 101.4)
        bars = scan.session_bars(s, D(2026, 11, 8), DT(2026, 11, 8, 15, 29))
        self.assertEqual(len(bars), 13)
        self.assertEqual(scan.orb_window(scan.parse_now("2026-11-08 14:45"), None, 15, None)[1],
                         DT(2026, 11, 8, 14, 45))
        with self.assertRaises(scan.ScanError) as cm:
            scan.orb_window(scan.parse_now("2026-11-08 14:30"), None, 15, None)
        self.assertIn("runs 2:20 PM to 2:35 PM", str(cm.exception))
        with self.assertRaises(scan.ScanError) as cm:
            scan.orb_window(scan.parse_now("2026-11-08 14:50"), None, 15, UNTIL)
        self.assertIn("expired", str(cm.exception))         # the default 14:45 cutoff leaves Muhurat little room

    def test_window_messages(self):
        def msg(now, at=None, until=UNTIL):
            with self.assertRaises(scan.ScanError) as cm:
                scan.orb_window(scan.parse_now(now), at, 15, until)
            return str(cm.exception)
        self.assertIn("weekend", msg("2026-10-17 10:15"))
        m = msg("2026-10-20 10:15")
        self.assertIn("NSE holiday: Dussehra", m)
        self.assertIn("next open 2026-10-21 09:15 IST", m)
        self.assertIn("Run orb again after 9:35 AM", msg("2026-10-19 09:25"))
        self.assertIn("runs 9:15 AM to 9:30 AM", msg("2026-10-19 09:34"))
        self.assertIn("expired", msg("2026-10-19 14:50"))
        self.assertIn("expired", msg("2026-10-19 11:30", until=datetime.time(11, 0)))
        self.assertIn("closed at 3:30 PM", msg("2026-10-19 15:45", until=None))
        self.assertIn("later than now", msg("2026-10-19 10:00", at=datetime.time(10, 15)))
        self.assertIn("after the 3:30 PM close", msg("2026-10-19 17:00", at=datetime.time(15, 45)))
        self.assertEqual(scan.orb_window(scan.parse_now("2026-10-19 16:30"), datetime.time(10, 15), 15, UNTIL),
                         (D(2026, 10, 19), DT(2026, 10, 19, 10, 15)))
        self.assertEqual(scan.orb_window(scan.parse_now("2026-10-19 09:35"), None, 15, UNTIL)[1],
                         DT(2026, 10, 19, 9, 35))
        with self.assertRaises(scan.ScanError):
            scan.orb_window(scan.parse_now("2026-10-19 10:15"), None, 7, UNTIL)


class LiveFiveMinute(unittest.TestCase):
    """A spark 5-minute answer and DEMO's v8 chart as if fetched at 10:01:47 AM IST on Monday."""

    def setUp(self):
        with open(SPARK5, encoding="utf-8") as fh:
            self.spark = scan.parse_spark(fh.read())
        with open(DEMO5, encoding="utf-8") as fh:
            self.v8 = scan.parse_chart(fh.read(), "DEMO")
        self.now = scan.parse_now("2026-10-19T04:31:48Z")

    def test_in_progress_bar_and_live_tick_are_dropped(self):
        self.assertEqual(sorted(self.spark), ["DEMO", "PENNY", "SYNA"])
        t = self.spark["DEMO"]
        self.assertEqual(len(t["t"]), 11)                  # 9:15 ... 10:00 (in progress) + the 10:01:47 tick
        self.assertEqual(scan.ts_to_ist(t["t"][-1]), DT(2026, 10, 19, 10, 1, 47))
        day, cut = scan.orb_window(self.now, None, 15, UNTIL)
        self.assertEqual(cut, DT(2026, 10, 19, 10, 1))
        bars = scan.session_bars(t, day, cut)
        self.assertEqual([b["start"].strftime("%H:%M") for b in bars][-2:], ["09:50", "09:55"])
        self.assertEqual(len(bars), 9)
        self.assertEqual(len(scan.session_bars(self.v8, day, cut)), 9)

    def test_demo_broke_its_range_at_ten(self):
        day, cut = scan.orb_window(self.now, None, 15, UNTIL)
        hits, counts, failed = scan.scan_orb(self.spark, ["DEMO", "PENNY"], day, cut, 15, 20, 0, lambda s: self.v8)
        self.assertEqual(hits, [DEMO_HIT])
        self.assertEqual(counts["illiquid"], 1)            # PENNY under the Rs 20 floor

    def test_stage_two_cap_takes_the_most_traded(self):
        data, _ = scan.load_offline(WALL)
        spark_like = {k: dict(v, h=None, l=None) for k, v in data.items()}
        asked = []

        def confirm(sym):
            asked.append(sym)
            return data[sym]
        day, cut = D(2026, 10, 19), DT(2026, 10, 19, 10, 15)
        hits, counts, _ = scan.scan_orb(spark_like, sorted(data), day, cut, 15, 0, 0, confirm, max_confirm=5)
        self.assertEqual((counts["confirmed"], counts["unconfirmed"]), (5, counts["candidates"] - 5))
        self.assertEqual(asked[0], "DEMO")                 # the most rupees traded by 10:15
        self.assertLessEqual(len(hits), 5)
        hits_all, _, _ = scan.scan_orb(spark_like, sorted(data), day, cut, 15, 0, 0, confirm, max_confirm=0)
        self.assertEqual(len(hits_all), 13)
        for h in hits:
            self.assertIn(h, hits_all)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.t = os.path.join(self.d, "trader")
        self.wl = os.path.join(self.t, "watchlist.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def read(self, path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_universe_lists(self):
        uni = os.path.join(self.t, "universe.txt")
        code, text, _ = run(["universe", "--offline", LISTS, "--out", uni])
        self.assertEqual(code, 0)
        self.assertIn("Nifty 500 list (ind_nifty500list.csv, NSE): 14 rows.", text)
        self.assertIn("Kept 12 NSE stocks.", text)
        self.assertIn("1 series BE (trade for trade: no intraday)", text)
        with open(uni, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(len(lines), 12)
        self.assertIn("M&M|Mahindra & Mahindra Ltd.", lines)
        code, text, _ = run(["universe", "--offline", LISTS, "--index", "nifty50", "--json", "--no-write"])
        self.assertEqual((code, json.loads(text)["count"], json.loads(text)["list"]), (0, 3, "Nifty 50"))
        code, text, _ = run(["universe", "--offline", LISTS, "--fno", "--no-write"])
        self.assertIn("Kept 4 NSE stocks with futures and options.", text)
        code, text, _ = run(["universe", "--offline", LISTS, "--all", "--no-write"])
        self.assertIn("Kept 5 NSE EQ-series stocks.", text)
        code, _, err = run(["universe", "--offline", LISTS, "--index", "midcap150", "--no-write"])
        self.assertEqual(code, 1)
        self.assertIn("no ind_niftymidcap150list.csv in", err)

    def test_universe_daily_orb_end_to_end(self):
        uni = os.path.join(self.t, "universe.txt")
        self.assertEqual(run(["universe", "--offline", LISTS, "--out", uni])[0], 0)

        # daily over that universe with the fixture charts: 5 of the 12 are in both, so partial
        code, text, _ = run(["daily", "--universe", uni, "--offline", DAILY, "--now", "2026-10-19 09:00",
                             "--out", self.wl])
        self.assertEqual(code, 0)
        self.assertIn("Fetched 5 of 12 charts (7 failed or had no data)", text)
        self.assertIn("(Rs 20+ and 2,00,000+ shares)", text)
        doc = self.read(self.wl)
        self.assertEqual((doc["mode"], doc["universe"], doc["scanned"], doc["failed"]), ("daily", 12, 5, 7))
        self.assertEqual([h["symbol"] for h in doc["hits"]], ["RECLAIM", "FLAGGY", "BRKOUT"])
        self.assertEqual(doc["hits"][2], {"symbol": "BRKOUT", "setups": ["breakout_20d"], "price": 108.69,
                                          "close_date": "2026-10-16", "rsi": 69.6, "volume": 900000})
        self.assertEqual((doc["as_of"], doc["exchange"]), ("2026-10-16 close", "NSE"))

        # the 10:15 scan
        code, text, _ = run(["orb", "--offline", WALL, "--now", "2026-10-19 10:15", "--min-price", "0",
                             "--out", self.wl])
        self.assertEqual(code, 0)
        self.assertIn("Opening-range breakout scan, Mon 2026-10-19 10:15 AM IST: 15-minute range (9:15 AM to "
                      "9:30 AM)", text)
        self.assertIn("Fetched 32 of 33 charts (1 failed or had no data); 28 usable; 15 candidates", text)
        self.assertIn("13 of 28 charts closed above their opening-range high by 10:15 AM", text)
        self.assertIn("  10:00 AM  DEMO         Rs 1,366.40  range 1351.30 to 1363.80  +0.19% over", text)
        self.assertIn("not a list of stocks to buy", text)
        doc = self.read(self.wl)
        self.assertEqual({k: doc[k] for k in ("created_by", "created_at", "mode", "as_of", "universe", "scanned",
                                              "source", "exchange")},
                         {"created_by": "trader-scanner", "created_at": "2026-10-19T04:45:00Z", "mode": "orb",
                          "as_of": "2026-10-19 10:15 IST", "universe": 33, "scanned": 28, "source": "yahoo",
                          "exchange": "NSE"})
        self.assertEqual(len(doc["hits"]), 13)
        self.assertIn(DEMO_HIT, doc["hits"])

        # the hits read back as a watchlist, with --at instead of --now
        code, text, _ = run(["orb", "--offline", WALL, "--at", "10:15", "--watchlist", self.wl, "--min-price", "0",
                             "--json", "--no-write"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"], len(out["hits"])), (13, 13, 13))

    def test_partial_stage_two_is_said(self):
        """The networked path with a fake Yahoo that answers from the synthetic bundle."""
        bundle = json.loads(scan._read_maybe_gz(WALL))
        calls = []

        def get(path):
            calls.append(path)
            if path.startswith("/v7/finance/spark"):
                res = []
                for y in path.split("symbols=")[1].split("&")[0].split(","):
                    y = urllib.parse.unquote(y)
                    r = ((bundle.get(scan.ours(y)) or {}).get("chart") or {}).get("result")
                    if r:
                        closes = r[0]["indicators"]["quote"][0].get("close", [])
                        res.append({"symbol": y, "response": [dict(r[0], indicators={"quote": [{"close": closes}]})]})
                return json.dumps({"spark": {"result": res, "error": None}})
            return json.dumps(bundle[scan.ours(urllib.parse.unquote(path.split("/chart/")[1].split("?")[0]))])
        old_get, old_sleep, real_now = scan.yahoo_get, scan.SLEEP, scan.parse_now
        scan.yahoo_get, scan.SLEEP = get, (lambda s: None)
        scan.parse_now = lambda text: real_now(text or "2026-10-19 10:20")  # the machine clock, pinned
        uni = os.path.join(self.d, "u.txt")
        with open(uni, "w") as fh:
            fh.write("\n".join(sorted(bundle)) + "\n")
        try:
            code, text, _ = run(["orb", "--universe", uni, "--now", "2026-10-19 10:15", "--min-price", "0",
                                 "--max-confirm", "5", "--no-write", "--source", "yahoo"])
        finally:
            scan.yahoo_get, scan.SLEEP, scan.parse_now = old_get, old_sleep, real_now
        self.assertEqual(code, 0)
        self.assertIn("28 usable; 15 candidates, 5 confirmed", text)
        self.assertIn("PARTIAL: 10 more candidates were not checked", text)
        self.assertIn("of the 5 checked charts closed above their opening-range high by 10:15 AM", text)
        self.assertEqual(sum(1 for c in calls if c.startswith("/v8/")), 5)
        self.assertEqual(sum(1 for c in calls if c.startswith("/v7/")), 2)    # 33 symbols, 20 a request
        self.assertTrue(all("range=1d&interval=5m" in c for c in calls))
        self.assertTrue(any("SYN%26SONS.NS" in c for c in calls))             # & goes in the URL quoted

    def test_plain_messages_and_exit_codes(self):
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-10-17 10:15", "--out", self.wl])
        self.assertEqual(code, 1)
        self.assertIn("No session on Sat 2026-10-17 (weekend)", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(os.path.exists(self.wl))
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-10-20 10:15", "--out", self.wl])
        self.assertIn("NSE holiday: Dussehra", err)
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-10-19 09:30", "--out", self.wl])
        self.assertIn("after 9:35 AM", err)
        cfg = os.path.join(self.t, "config.json")
        os.makedirs(self.t)
        with open(cfg, "w") as fh:
            json.dump({"no_entries_after": "10:00"}, fh)
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-10-19 10:15", "--config", cfg])
        self.assertEqual(code, 1)
        self.assertIn("expired", err)
        code, _, err = run(["orb", "--offline", WALL])
        self.assertIn("--at HH:MM", err)
        code, text, _ = run(["orb", "--offline", WALL, "--now", "2026-10-19 09:55", "--symbols", "DEMO",
                             "--out", self.wl])
        self.assertEqual(code, 1)                          # no breakout yet, but the empty list is written
        self.assertIn("No chart closed above its opening-range high by 9:55 AM", text)
        self.assertEqual(self.read(self.wl)["hits"], [])
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-10-19 10:15", "--symbols", "500325.BO"])
        self.assertEqual(code, 1)
        self.assertIn("BSE listing", err)
        code, _, err = run(["daily", "--universe", os.path.join(self.d, "nope.txt")])
        self.assertEqual(code, 1)
        self.assertIn("scan.py universe", err)
        code, _, err = run(["daily", "--offline", os.path.join(self.d, "nothing-here")])
        self.assertIn("no offline file", err)
        empty = os.path.join(self.d, "empty.json")
        with open(empty, "w") as fh:
            fh.write("{}")
        code, _, err = run(["daily", "--offline", empty])
        self.assertEqual(code, 1)
        self.assertIn("no symbols to scan", err)

    def test_offline_folder_of_v8_files(self):
        folder = os.path.join(self.d, "charts")
        os.makedirs(folder)
        bundle = json.loads(scan._read_maybe_gz(WALL))
        for sym, fname in (("DEMO", "DEMO.NS.json"), ("SYNA", "SYNA.json"), ("SYNK", "SYNK.json"),
                           ("DELISTED", "DELISTED.json")):
            with open(os.path.join(folder, fname), "w") as fh:
                json.dump(bundle[sym], fh)
        code, text, _ = run(["orb", "--offline", folder, "--now", "2026-10-19 10:15", "--no-write", "--json"])
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"], out["failed"]), (4, 3, 1))
        self.assertEqual([h["symbol"] for h in out["hits"]], ["SYNA", "DEMO"])
        self.assertEqual(code, 0)

    def test_symbol_lists(self):
        txt = os.path.join(self.d, "list.txt")
        with open(txt, "w") as fh:
            fh.write("# my list\nDEMO|Demo Industries Ltd. (synthetic)\nbrkout.ns\n\nNSE:m&m\nDEMO\n")
        self.assertEqual(scan.symbols_from_file(txt), ["DEMO", "BRKOUT", "M&M", "DEMO"])
        js = os.path.join(self.d, "wl.json")
        with open(js, "w") as fh:
            json.dump({"hits": [{"symbol": "demo"}, {"symbol": "FLAGGY"}]}, fh)
        self.assertEqual(scan.symbols_from_file(js), ["DEMO", "FLAGGY"])
        code, text, _ = run(["daily", "--offline", DAILY, "--watchlist", txt, "--now", "2026-10-19 09:00",
                             "--json", "--no-write", "--min-volume", "0"])
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"], out["failed"]), (3, 2, 1))   # M&M is not in the file
        code, text, _ = run(["daily", "--offline", DAILY, "--limit", "3", "--now", "2026-10-19 09:00", "--json",
                             "--no-write", "--min-volume", "0"])
        self.assertEqual(json.loads(text)["universe"], 3)
        code, text, _ = run(["daily", "--offline", DAILY, "--symbols", "brkout, flaggy", "--now",
                             "2026-10-19 09:00", "--json", "--no-write"])
        self.assertEqual([h["symbol"] for h in json.loads(text)["hits"]], ["FLAGGY", "BRKOUT"])   # most traded first

    def test_daily_text_and_json(self):
        code, text, _ = run(["daily", "--offline", DAILY, "--now", "2026-10-19 09:00", "--no-write"])
        self.assertEqual(code, 0)
        self.assertIn("Daily scan, Mon 2026-10-19 09:00 IST, closes through 2026-10-16", text)
        self.assertIn("5 charts matched a setup: 1 20-day closing-high breakout, 1 bull flag, 1 reclaimed the "
                      "20-day average, 1 oversold (RSI under 30), 1 oversold bounce.", text)
        self.assertIn("  BRKOUT         Rs 108.69  RSI  69.6  20-day closing-high breakout", text)
        code, text, _ = run(["daily", "--offline", DAILY, "--now", "2026-10-19 09:00", "--json", "--no-write",
                             "--min-volume", "0", "--min-price", "0"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual(len(out["hits"]), 7)
        self.assertEqual(out["note"], "A scan is a list of charts that match a rule, not a list of stocks to buy.")

    def test_a_year_past_the_holiday_list_says_so(self):
        code, text, _ = run(["orb", "--offline", WALL, "--now", "2027-01-05 10:15", "--no-write", "--symbols",
                             "DEMO"])
        self.assertEqual(code, 1)
        self.assertIn("Holiday list covers 2026 only: check nseindia.com for 2027-01-05.", text)


class KiteSource(unittest.TestCase):
    """--source kite: the user's own Kite Connect session, read only. A fake Kite
    answers from the same synthetic charts (kite-2026-10-19-synthetic.json.gz), so
    the Kite path must find exactly what the Yahoo path finds."""

    @classmethod
    def setUpClass(cls):
        cls.files = tempfile.mkdtemp()
        doc = json.loads(scan._read_maybe_gz(KITE))
        inst = os.path.join(cls.files, "instruments.csv")
        with open(inst, "w", encoding="utf-8") as fh:
            fh.write(doc["instruments"])
        cls.candles, cls.quotes = {}, {}
        for key, answer in doc["candles"].items():
            sym, interval = key.split("|")
            path = os.path.join(cls.files, "c%d.json" % len(cls.candles))
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(answer, fh)
            cls.candles[(doc["tokens"][sym], interval)] = path
        for sym, answer in doc["quotes"].items():
            path = os.path.join(cls.files, "q%d.json" % len(cls.quotes))
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(answer, fh)
            cls.quotes[sym] = path
        cls.inst = inst
        cls.wall_syms = sorted(json.loads(scan._read_maybe_gz(WALL)))
        cls.daily_syms = sorted(scan.load_offline(DAILY)[0])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.files)

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)                       # trader/state/... is relative, like a real project
        FakeKite.session_file(self.d)
        self.fake = FakeKite(candles=self.candles, quotes=self.quotes, instruments=self.inst)
        self.old = (scan.kite_urlopen, scan.SLEEP, scan.yahoo_get)
        self.fake.install(scan)
        self.sleeps = []
        scan.SLEEP = self.sleeps.append

        def no_yahoo(path):
            raise AssertionError("the Kite path asked Yahoo for %s" % path)
        scan.yahoo_get = no_yahoo

    def tearDown(self):
        scan.kite_urlopen, scan.SLEEP, scan.yahoo_get = self.old
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def universe(self, syms):
        path = os.path.join(self.d, "u.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(syms) + "\n")
        return path

    def run_json(self, argv):
        code, text, err = run(argv + ["--json", "--no-write"])
        self.assertNotIn(TOKEN, text + err)
        return code, json.loads(text), err

    def assert_read_only(self):
        self.assertEqual(self.fake.methods(), {"GET"})
        auth = {h.get("Authorization") for _, _, h in self.fake.requests}
        self.assertEqual(auth, {"token demo_api_key:%s" % TOKEN})

    def test_orb_finds_what_yahoo_finds(self):
        uni = self.universe(self.wall_syms)
        _, want, _ = self.run_json(["orb", "--offline", WALL, "--now", "2026-10-19 10:15", "--min-price", "0"])
        code, got, _ = self.run_json(["orb", "--universe", uni, "--now", "2026-10-19 10:15", "--min-price", "0",
                                      "--source", "kite"])
        self.assertEqual(code, 0)
        self.assertEqual(got["hits"], want["hits"])
        self.assertEqual([h["symbol"] for h in got["hits"]], HITS_1015)
        self.assertIn(DEMO_HIT, got["hits"])
        self.assertEqual((got["source"], got["universe"], got["scanned"], got["failed"], got["unconfirmed"]),
                         ("kite", 33, 28, 1, 0))
        c = got["counts"]
        self.assertEqual((c["quoted"], c["candles"], c["illiquid"], c["candidates"]), (32, 32, 0, 15))
        paths = self.fake.paths()
        self.assertEqual(paths.count("/quote"), 1)                       # one batch, up to 500 symbols
        self.assertEqual(paths.count("/instruments/NSE"), 1)             # cached for the day after that
        self.assertEqual(sum(1 for p in paths if p.startswith("/instruments/historical/")), 32)
        self.assertIn("/instruments/historical/9000001/5minute", paths)  # DEMO, the shared candles
        self.assertEqual(self.sleeps, [scan.KITE_PAUSE] * 31)            # 3 requests a second at most
        self.assert_read_only()
        cache = os.path.join(self.d, "trader", "state", "kite-instruments-NSE.json")
        self.assertTrue(os.path.exists(cache))

    def test_orb_text_floor_and_the_cap(self):
        uni = self.universe(self.wall_syms)
        code, text, err = run(["orb", "--universe", uni, "--now", "2026-10-19 10:15", "--source", "kite",
                               "--no-write"])
        self.assertEqual(code, 0)
        self.assertNotIn(TOKEN, text + err)
        self.assertIn("Opening-range breakout scan, Mon 2026-10-19 10:15 AM IST: 15-minute range (9:15 AM to "
                      "9:30 AM), 5-minute bars (Kite, live), NSE.", text)
        # PENNY's quote is under the Rs 20 floor, so it is never fetched
        self.assertIn("Kite quotes for 32 of 33 symbols (1 had none); 1 under the price / volume floor; 5-minute "
                      "candles for 31, the most traded first; 27 usable; 14 candidates, all checked with real "
                      "highs and lows.", text)
        self.assertIn("12 of 27 charts closed above their opening-range high by 10:15 AM", text)
        self.assertIn("  10:00 AM  DEMO         Rs 1,366.40  range 1351.30 to 1363.80  +0.19% over", text)
        self.assertNotIn("PENNY", text.split("closed above")[1])
        n = len(self.fake.requests)
        code, out, _ = self.run_json(["orb", "--universe", uni, "--now", "2026-10-19 10:15", "--min-price", "0",
                                      "--source", "kite", "--max-confirm", "5"])
        self.assertEqual(out["unconfirmed"], 27)
        self.assertEqual(sum(1 for p in self.fake.paths()[n:] if p.startswith("/instruments/historical/")), 5)
        code, text, _ = run(["orb", "--universe", uni, "--now", "2026-10-19 10:15", "--min-price", "0",
                             "--source", "kite", "--max-confirm", "5", "--no-write"])
        self.assertIn("PARTIAL: 27 more charts were not fetched (only the 5 most traded are; --max-confirm 0 "
                      "fetches all).", text)
        self.assert_read_only()

    def test_daily_finds_what_yahoo_finds(self):
        uni = self.universe(self.daily_syms)
        for floors in (["--min-price", "0", "--min-volume", "0"], []):
            _, want, _ = self.run_json(["daily", "--offline", DAILY, "--now", "2026-10-19 09:00"] + floors)
            n = len(self.fake.requests)
            code, got, _ = self.run_json(["daily", "--universe", uni, "--now", "2026-10-19 09:00",
                                          "--source", "kite"] + floors)
            asked = self.fake.paths()[n:]
            self.assertEqual(code, 0)
            self.assertEqual([(h["symbol"], h["setups"], h["price"], h["close_date"], h["rsi"]) for h in got["hits"]],
                             [(h["symbol"], h["setups"], h["price"], h["close_date"], h["rsi"]) for h in want["hits"]])
            self.assertEqual({k: got["counts"][k] for k in ("fetched", "illiquid", "short", "scanned", "hits")},
                             {k: want["counts"][k] for k in ("fetched", "illiquid", "short", "scanned", "hits")})
            self.assertEqual((got["source"], got["as_of"]), ("kite", "2026-10-16 close"))
        self.assertEqual({h["symbol"]: h["setups"] for h in got["hits"]},
                         {k: v for k, v in Daily.EXPECT.items() if k not in ("THIN", "CHEAP")})
        # with the Rs 20 floor, CHEAP (Rs 15) is under it on its quote: no candles asked for it
        tokens = json.loads(scan._read_maybe_gz(KITE))["tokens"]
        self.assertNotIn("/instruments/historical/%d/day" % tokens["CHEAP"], asked)
        self.assertEqual(sum(1 for p in asked if "/historical/" in p), 12)
        self.assertTrue(all(p.endswith("/day") for p in self.fake.paths() if "/historical/" in p))
        self.assert_read_only()
        code, text, _ = run(["daily", "--universe", uni, "--now", "2026-10-19 09:00", "--source", "kite",
                             "--no-write"])
        self.assertIn("closes through 2026-10-16 (Kite day candles, live, NSE, daily closes only).", text)

    def test_kite_volume_floor_reads_the_last_complete_day(self):
        s = {"t": [scan.ts_of(scan.ist_to_utc(DT(2026, 10, d, 0, 0))) for d in (15, 16, 19)],
             "c": [10.0, 11.0, 12.0], "v": [500, 700, 90], "meta": {}}
        self.assertEqual(scan.last_complete_volume(s, scan.parse_now("2026-10-19 10:00")), 700)
        self.assertEqual(scan.last_complete_volume(s, scan.parse_now("2026-10-19 15:31")), 90)

    def test_auto_and_the_config(self):
        uni = self.universe(["DEMO"])
        # a session from today: auto is Kite
        code, out, _ = self.run_json(["orb", "--universe", uni, "--now", "2026-10-19 10:15"])
        self.assertEqual((out["source"], [h["symbol"] for h in out["hits"]]), ("kite", ["DEMO"]))
        self.assertNotIn("source_note", out)
        # the session ends at 6:00 AM IST: the next morning auto falls back to Yahoo, with one note
        calls = []

        def yahoo(path):
            calls.append(path)
            with open(DAILY, encoding="utf-8") as fh:
                return fh.read()
        scan.yahoo_get = yahoo
        n = len(self.fake.requests)
        code, text, _ = run(["daily", "--universe", uni, "--now", "2026-10-21 08:30", "--no-write"])
        self.assertEqual(len(self.fake.requests), n)
        self.assertEqual(len(calls), 1)
        self.assertEqual(text.count("Data: no Kite session today, so free Yahoo data"), 1)
        self.assertIn("(Yahoo spark, free, can lag, NSE, daily closes only)", text)
        code, out, _ = self.run_json(["daily", "--universe", uni, "--now", "2026-10-21 08:30"])
        self.assertEqual(out["source"], "yahoo")
        self.assertIn("no Kite session today", out["source_note"])
        # "data_source": "yahoo" in the config never asks Kite, even with a session
        cfg = os.path.join(self.d, "cfg.json")
        with open(cfg, "w") as fh:
            json.dump({"data_source": "yahoo"}, fh)
        code, out, _ = self.run_json(["daily", "--universe", uni, "--now", "2026-10-19 09:00", "--config", cfg])
        self.assertEqual((out["source"], len(self.fake.requests)), ("yahoo", n))
        # --offline wins over everything
        code, out, _ = self.run_json(["orb", "--offline", WALL, "--now", "2026-10-19 10:15", "--source", "kite",
                                      "--symbols", "DEMO"])
        self.assertEqual((out["source"], len(self.fake.requests)), ("yahoo", n))

    def test_no_session_and_refusals_are_one_plain_line(self):
        uni = self.universe(self.wall_syms)
        os.remove(os.path.join(self.d, "trader", "state", "kite-session.json"))
        code, _, err = run(["orb", "--universe", uni, "--now", "2026-10-19 10:15", "--source", "kite"])
        self.assertEqual(code, 1)
        self.assertEqual(err.strip().splitlines(), ["scan.py: no Kite session for today: log in first (python3 "
                                                    "trader-broker/kite.py login), or use --source yahoo"])
        FakeKite.session_file(self.d)
        refused = os.path.join(FIX, "shared", "kite", "error-token-synthetic.json")
        self.fake.fail["/quote"] = (403, refused)
        code, text, err = run(["orb", "--universe", uni, "--now", "2026-10-19 10:15", "--source", "kite",
                               "--out", os.path.join(self.d, "wl.json"), "--quiet"])
        self.assertEqual(code, 1)
        self.assertEqual(len(err.strip().splitlines()), 1)                 # --quiet: no progress lines
        self.assertIn("scan.py: Kite refused the session (TokenException: Incorrect `api_key` or `access_token`.)."
                      " Log in again: python3 trader-broker/kite.py login", err)
        self.assertNotIn("Traceback", err)
        self.assertNotIn(TOKEN, text + err)
        self.assertFalse(os.path.exists(os.path.join(self.d, "wl.json")))
        # a refusal halfway through the candles stops the scan at once
        del self.fake.fail["/quote"]
        self.fake.fail["/instruments/historical/"] = (403, refused)
        n = len(self.fake.requests)
        code, _, err = run(["orb", "--universe", uni, "--now", "2026-10-19 10:15", "--source", "kite", "--quiet"])
        self.assertEqual(code, 1)
        self.assertIn("Kite refused the session", err)
        self.assertEqual(sum(1 for p in self.fake.paths()[n:] if "/historical/" in p), 1)
        self.assert_read_only()

    def test_a_symbol_kite_does_not_know(self):
        uni = self.universe(["DEMO", "DELISTED"])
        code, out, _ = self.run_json(["orb", "--universe", uni, "--now", "2026-10-19 10:15", "--source", "kite"])
        self.assertEqual((out["universe"], out["failed"], [h["symbol"] for h in out["hits"]]), (2, 1, ["DEMO"]))
        self.assertEqual(self.fake.paths().count("/instruments/historical/9000001/5minute"), 1)


if __name__ == "__main__":
    unittest.main()
