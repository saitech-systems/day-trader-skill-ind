"""trader-entry tests. SYNTHETIC fixtures, not market data (the India edition's
tests were written without network access to Yahoo or NSE):

tests/fixtures/shared/DEMO-2026-10-19-5m-synthetic.json
    A made-up NSE stock, DEMO (Yahoo DEMO.NS), on Monday 2026-10-19: five-minute
    bars from 9:15 IST "fetched" at 10:49:45, so it ends with the in-progress
    10:45 bar and a live tick at 10:49:45. Opening range 1351.30 to 1363.80; the
    9:55 bar (bar 8) closes above it at 1366.40, at 10:00 AM. Made by
    tests/fixtures/make_synthetic.py.
tests/fixtures/trader-entry/TESTCO-2026-10-19-1m-synthetic.json
    One-minute bars for another made-up stock: a wick above the range high, a
    close AT the high, a null bar, a volume spike, a live tick. Made by
    tests/fixtures/trader-entry/make_synthetic.py.
"""

import copy
import datetime
import gzip
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX, ROOT
from _kite import FakeKite, KDIR

entry = load("trader-entry", "entry")

DEMO = os.path.join(FIX, "shared", "DEMO-2026-10-19-5m-synthetic.json")
TESTCO = os.path.join(FIX, "trader-entry", "TESTCO-2026-10-19-1m-synthetic.json")
OPEN_TS = 1792381500       # 2026-10-19 09:15 IST (03:45 UTC)
DT = datetime.datetime


def ist(text):
    return entry.parse_now(text)


def doc(path=DEMO):
    with open(path) as fh:
        return json.load(fh)


def chart(d=None, sym="DEMO"):
    return entry.parse_chart(json.dumps(d if d is not None else doc()), sym)


def at(text, d=None, **kw):
    return entry.evaluate(chart(d), ist(text), **kw)


def shift_to(d, day, open_hhmm="09:15"):
    """The same session moved to another date (and, for a special session, another open)."""
    d = copy.deepcopy(d)
    r = d["chart"]["result"][0]
    hh, mm = map(int, open_hhmm.split(":"))
    new_open = entry.ts_of(entry.ist_to_utc(DT.combine(day, datetime.time(hh, mm))))
    delta = new_open - OPEN_TS
    r["timestamp"] = [t + delta for t in r["timestamp"]]
    r["meta"]["regularMarketTime"] += delta
    return d


def no_breakout(d=None):
    """Every bar after the range stays under the 1363.80 high."""
    d = copy.deepcopy(d if d is not None else doc())
    q = d["chart"]["result"][0]["indicators"]["quote"][0]
    for i in range(3, len(q["close"])):
        q["close"][i] = min(q["close"][i], 1363.60)
        q["high"][i] = min(q["high"][i], 1363.70)
        q["open"][i] = min(q["open"][i], 1363.60)
    return d


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = entry.main(argv)
    return code, out.getvalue(), err.getvalue()


class TheStory(unittest.TestCase):
    def test_triggers_at_1000_on_the_955_bar(self):
        r = at("2026-10-19 10:00")
        self.assertEqual(r["status"], "triggered")
        self.assertEqual(r["opening_range"]["high"], 1363.8)
        self.assertEqual(r["opening_range"]["low"], 1351.3)
        self.assertEqual(r["opening_range"]["bars"], 3)
        t = r["trigger"]
        self.assertEqual(t["time"], "10:00 AM")
        self.assertEqual(t["bar_start"], "09:55")
        self.assertEqual(t["bar_index"], 8)
        self.assertEqual(t["price"], 1366.4)
        self.assertEqual(t["pct_over"], 0.19)
        self.assertEqual(r["level"], 1363.8)
        self.assertEqual(r["prev_close"], 1352.4)
        self.assertEqual((r["opens"], r["closes"], r["no_entries_before"], r["no_entries_after"]),
                         ("09:15", "15:30", "09:30", "14:45"))

    def test_at_957_the_955_bar_is_still_forming(self):
        r = at("2026-10-19 09:57")
        self.assertEqual(r["status"], "waiting")
        self.assertIsNone(r["trigger"])
        self.assertEqual(r["last"]["bar_start"], "09:50")
        self.assertEqual(r["last"]["close"], 1362.9)
        self.assertEqual(r["forming"]["bar_start"], "09:55")
        self.assertIn("0.90 under the range high 1363.80", r["reason"])
        self.assertEqual(at("2026-10-19 09:59:59")["status"], "waiting")

    def test_the_wick_and_the_close_at_the_high_do_not_trigger(self):
        r = at("2026-10-19 09:40")                     # the 9:35 bar wicked to 1364.50, closed 1362.70
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(r["last"]["bar_start"], "09:35")
        r = at("2026-10-19 09:45")                     # the 9:40 bar closed AT 1363.80
        self.assertEqual(r["status"], "waiting")
        self.assertEqual((r["last"]["close"], r["last"]["vs_level_pct"]), (1363.8, 0.0))

    def test_before_the_open_and_while_the_range_forms(self):
        r = at("2026-10-19 09:05")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "pre-open"))
        self.assertIn("opens at 9:15", r["reason"])
        r = at("2026-10-19 09:29")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "forming"))
        self.assertEqual(r["opening_range"]["bars"], 2)
        self.assertFalse(r["opening_range"]["complete"])
        r = at("2026-10-19 09:31")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "open"))
        self.assertEqual(r["opening_range"]["bars"], 3)

    def test_the_live_tick_and_the_in_progress_bar_are_dropped(self):
        c = chart()
        self.assertEqual(c["bars"][-1]["t"], 1792387185)          # 10:49:45 IST is in the file
        self.assertEqual((c["symbol"], c["yahoo"]), ("DEMO", "DEMO.NS"))
        r = entry.evaluate(c, entry.utc_of(c["data_time"]))     # read as of the file's own time
        self.assertEqual(r["now"], "2026-10-19 10:49:45")
        self.assertEqual(r["_bars"][-1]["slot"], 17)               # the 10:40 bar
        self.assertEqual(r["forming"]["bar_start"], "10:45")
        self.assertEqual(r["bars_closed"], 18)
        # 10:50: closed by the clock, but Yahoo's last trade (10:49:45) is before its close
        r = at("2026-10-19 10:50")
        self.assertEqual(r["_bars"][-1]["slot"], 17)
        self.assertIn("Yahoo's last trade is 10:49:45", r["lag_note"])
        # a minute later with no newer trade it counts; the 10:49:45 tick never becomes a bar
        r = at("2026-10-19 10:51")
        self.assertEqual(r["_bars"][-1]["slot"], 18)
        self.assertEqual(r["bars_closed"], 19)
        self.assertTrue(all(b["t"] % 300 == 0 for b in r["_bars"]))

    def test_after_the_close_it_stays_triggered_and_says_it_is_late(self):
        r = at("2026-10-19 15:35")
        self.assertEqual((r["status"], r["phase"]), ("triggered", "after-close"))
        self.assertEqual(r["trigger"]["time"], "10:00 AM")
        text = "\n".join(entry.describe(r))
        self.assertIn("minutes ago", text)
        self.assertNotIn("This is the moment", text)


class Rules(unittest.TestCase):
    def test_volume_confirmation_moves_the_trigger(self):
        r = at("2026-10-19 10:49", min_volume_x=1)
        self.assertEqual(r["opening_range"]["avg_volume"], 303667)
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_index"], t["price"]), ("10:30 AM", 14, 1371.6))
        self.assertEqual(t["volume_x"], 1.16)
        r = at("2026-10-19 10:01", min_volume_x=1)
        self.assertEqual(r["status"], "waiting")
        self.assertIn("0.84x the range's volume, under your 1.00x", r["reason"])

    def test_level_setup(self):
        r = at("2026-10-19 10:30", setup="level", level=1368.0)
        self.assertEqual(r["trigger"]["time"], "10:05 AM")
        self.assertEqual(r["trigger"]["bar_index"], 9)
        self.assertEqual(r["trigger"]["price"], 1368.1)
        # a level under the open: the first eligible close is the 9:25 bar's, at 9:30 (no_entries_before)
        r = at("2026-10-19 10:30", setup="level", level=1350)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"]), ("9:30 AM", 2))
        with self.assertRaises(entry.EntryError):
            at("2026-10-19 10:30", setup="level")

    def test_no_breakout_waits_then_expires_at_the_cutoff(self):
        d = no_breakout()
        r = at("2026-10-19 10:49", d)
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(at("2026-10-19 14:44", d)["status"], "waiting")
        r = at("2026-10-19 14:45", d)
        self.assertEqual(r["status"], "expired")
        self.assertIn("by 14:45", r["reason"])
        self.assertEqual(at("2026-10-19 15:45", d)["phase"], "after-close")

    def test_the_bar_closing_at_the_cutoff_still_counts_when_yahoo_lags(self):
        d = no_breakout()
        r0 = d["chart"]["result"][0]
        keep = [i for i, t in enumerate(r0["timestamp"]) if t <= OPEN_TS + 17 * 300]   # live: up to the 10:40 bar
        r0["timestamp"] = [r0["timestamp"][i] for i in keep]
        q = r0["indicators"]["quote"][0]
        for k in q:
            q[k] = [q[k][i] for i in keep]
        r0["meta"]["regularMarketTime"] = OPEN_TS + 89 * 60 + 40                       # last trade 10:44:40
        cfg = {"no_entries_before": "09:30", "no_entries_after": "10:45"}
        r = at("2026-10-19 10:45:10", d, cfg=cfg)                           # the 10:40 bar closed by the clock only
        self.assertEqual(r["status"], "waiting")
        self.assertIn("Yahoo's last trade is 10:44:40", r["lag_note"])
        self.assertEqual(at("2026-10-19 10:46", d, cfg=cfg)["status"], "expired")

    def test_config_cutoff(self):
        cfg = {"no_entries_before": "09:30", "no_entries_after": "09:55"}
        r = at("2026-10-19 10:00", cfg=cfg)
        self.assertEqual(r["status"], "expired")
        cfg = {"no_entries_before": "10:05", "no_entries_after": "14:45"}
        r = at("2026-10-19 10:30", cfg=cfg)
        self.assertEqual(r["trigger"]["time"], "10:05 AM")       # the 10:00 bar, first to close at or after 10:05
        # a cutoff after the close is the close
        r = at("2026-10-19 10:00", cfg={"no_entries_before": "09:30", "no_entries_after": "16:00"})
        self.assertEqual(r["no_entries_after"], "15:30")

    def test_bad_inputs(self):
        with self.assertRaises(entry.EntryError):
            at("2026-10-19 10:15", or_minutes=7)
        with self.assertRaises(entry.EntryError):
            at("2026-10-19 10:15", interval="15m")
        with self.assertRaises(entry.EntryError):
            entry.parse_now("tomorrow")
        with self.assertRaises(entry.EntryError):
            entry.parse_hhmm("25:00", "x")
        self.assertEqual(entry.parse_now("2026-10-19 10:00"), DT(2026, 10, 19, 4, 30))
        self.assertEqual(entry.parse_now("2026-10-19T04:30:00Z"), DT(2026, 10, 19, 4, 30))
        self.assertEqual(entry.parse_now("2026-10-19 10:00 IST"), DT(2026, 10, 19, 4, 30))
        self.assertEqual(entry.parse_now("2026-10-19T10:00:00+05:30"), DT(2026, 10, 19, 4, 30))

    def test_or_minutes_30(self):
        r = at("2026-10-19 10:49", or_minutes=30)
        self.assertEqual(r["opening_range"]["bars"], 6)
        self.assertEqual(r["opening_range"]["high"], 1364.5)      # the 9:35 wick is inside a 30-minute range
        self.assertEqual(r["trigger"]["time"], "10:00 AM")


class Symbols(unittest.TestCase):
    def test_nse_symbols_and_yahoo(self):
        self.assertEqual(entry.nse_symbol("reliance"), "RELIANCE")
        self.assertEqual(entry.nse_symbol("RELIANCE.NS"), "RELIANCE")
        self.assertEqual(entry.nse_symbol("NSE:bajaj-auto"), "BAJAJ-AUTO")
        self.assertEqual(entry.yahoo_symbol("m&m"), "M&M.NS")
        self.assertEqual(entry.yahoo_quote("m&m"), "M%26M.NS")
        self.assertIn("/chart/M%26M.NS?", entry.chart_path("m&m", "1d", "5m"))
        for bad in ("RELIANCE.BO", "BSE:500325", "TSLA/USD", "", "A B"):
            with self.assertRaises(entry.EntryError):
                entry.nse_symbol(bad)


class OneMinute(unittest.TestCase):
    def c(self):
        return entry.parse_chart(json.dumps(doc(TESTCO)), "TESTCO")

    def test_wick_and_close_at_the_high_do_not_trigger(self):
        c = self.c()
        r = entry.evaluate(c, entry.utc_of(c["data_time"]), interval="1m")
        self.assertEqual(r["now"], "2026-10-19 09:51:30")
        self.assertEqual(r["opening_range"], {"high": 100.5, "low": 99.0, "minutes": 15, "bars": 15, "of": 15,
                                              "complete": True, "avg_volume": 10667})
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_start"], t["bar_index"], t["price"]), ("9:44 AM", "09:43", 28, 100.62))
        r = entry.evaluate(c, ist("2026-10-19 09:43:59"), interval="1m")
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(r["forming"]["bar_start"], "09:43")
        self.assertEqual(r["last"]["close"], 100.5)             # the 9:42 bar closed AT the high

    def test_null_bar_tick_and_volume(self):
        c = self.c()
        r = entry.evaluate(c, entry.utc_of(c["data_time"]), interval="1m", min_volume_x=2)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"]), ("9:47 AM", 31))
        slots = [b["slot"] for b in r["_bars"]]
        self.assertNotIn(19, slots)                               # 9:34 came as nulls
        self.assertEqual(slots[-1], 35)                           # 9:51 still forming, 9:51:30 tick dropped
        self.assertEqual(r["forming"]["bar_start"], "09:51")

    def test_the_file_and_the_interval_must_agree(self):
        code, _, err = run(["check", "TESTCO", "--offline", TESTCO])
        self.assertEqual(code, 1)
        self.assertIn("1m bars, not 5m", err)


class Calendar(unittest.TestCase):
    def test_no_daylight_saving_in_india(self):
        # the same bars on dates either side of the US and European clock changes: India never moves
        for day in (datetime.date(2026, 3, 9), datetime.date(2026, 3, 30), datetime.date(2026, 10, 26),
                    datetime.date(2026, 11, 2), datetime.date(2026, 12, 7)):
            d = shift_to(doc(), day)
            r = entry.evaluate(chart(d), ist("%s 10:00" % day))
            self.assertEqual(r["status"], "triggered", day)
            self.assertEqual(r["trigger"]["time"], "10:00 AM", day)
            self.assertEqual(r["trigger"]["bar_index"], 8, day)
            self.assertEqual(entry.evaluate(chart(d), ist("%s 09:57" % day))["status"], "waiting", day)
            self.assertEqual(ist("%s 10:00" % day), DT.combine(day, datetime.time(4, 30)), day)

    def test_muhurat_special_session_builds_its_own_range(self):
        day = datetime.date(2026, 11, 8)                          # a Sunday: Muhurat trading, 14:20 to 15:30
        self.assertEqual(entry.trading_day(day)[0], True)
        d = shift_to(doc(), day, "14:20")
        r = entry.evaluate(chart(d), ist("2026-11-08 14:00"))
        self.assertEqual((r["status"], r["phase"]), ("waiting", "pre-open"))
        self.assertIn("opens at 14:20 and the 15-minute range is set at 14:35", r["reason"])
        r = entry.evaluate(chart(d), ist("2026-11-08 14:36"))
        self.assertEqual((r["opening_range"]["high"], r["opening_range"]["low"]), (1363.8, 1351.3))
        self.assertEqual((r["opens"], r["closes"], r["no_entries_after"]), ("14:20", "15:30", "14:45"))
        self.assertIn("Muhurat", "\n".join(entry.describe(r)))
        # bar 8 closes at 15:05, after the default 14:45 cutoff: expired
        r = entry.evaluate(chart(d), ist("2026-11-08 14:46"))
        self.assertEqual(r["status"], "expired")
        # with a later cutoff it triggers at 3:05 PM
        cfg = {"no_entries_before": "09:30", "no_entries_after": "15:20"}
        r = entry.evaluate(chart(d), ist("2026-11-08 15:06"), cfg=cfg)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"]), ("3:05 PM", 8))

    def test_budget_sunday_is_a_trading_day(self):
        self.assertEqual(entry.session(datetime.date(2026, 2, 1)), (datetime.time(9, 15), datetime.time(15, 30)))
        self.assertEqual(entry.trading_day(datetime.date(2026, 2, 1))[0], True)
        self.assertEqual(entry.trading_day(datetime.date(2026, 2, 8)), (False, "weekend"))

    def test_weekend_holiday_and_empty_data(self):
        r = at("2026-10-17 10:30")
        self.assertEqual((r["status"], r["phase"]), ("no-data", "closed"))
        self.assertEqual(r["next_open"], "2026-10-19 09:15")
        r = at("2026-10-20 10:30")
        self.assertIn("NSE holiday: Dussehra", r["reason"])
        self.assertEqual(r["next_open"], "2026-10-21 09:15")
        self.assertIn("Next open: 2026-10-21 09:15 IST", "\n".join(entry.describe(r)))
        r = at("2026-11-10 10:30")
        self.assertIn("Diwali Balipratipada", r["reason"])
        empty = copy.deepcopy(doc())
        res = empty["chart"]["result"][0]
        del res["timestamp"]
        res["indicators"] = {"quote": [{}]}
        r = at("2026-10-19 10:00", empty)
        self.assertEqual((r["status"], r["phase"]), ("no-data", "open"))
        r = at("2026-10-21 10:00")                                # Yahoo sent Monday's bars only
        self.assertEqual(r["status"], "no-data")
        self.assertFalse(at("2027-03-01 10:30")["calendar_known"])
        self.assertIn("check nseindia.com", "\n".join(entry.describe(at("2027-03-01 10:30"))))

    def test_yahoo_errors(self):
        bad = json.dumps({"chart": {"result": None, "error": {"code": "Not Found",
                                                              "description": "No data found, symbol may be delisted"}}})
        with self.assertRaises(entry.EntryError) as cm:
            entry.parse_chart(bad, "ZZZZ")
        self.assertIn("No data found", str(cm.exception))
        with self.assertRaises(entry.EntryError):
            entry.parse_chart("<html>", "DEMO")


class Network(unittest.TestCase):
    """Yahoo is never called: _open_url and _sleep are swapped for fakes."""

    def setUp(self):
        self.calls, self.sleeps = [], []
        self._open, self._sleep = entry._open_url, entry._sleep
        entry._sleep = lambda s: self.sleeps.append(s)
        with open(DEMO) as fh:
            self.body = fh.read()

    def tearDown(self):
        entry._open_url, entry._sleep = self._open, self._sleep

    def answers(self, *codes):
        seq = list(codes)

        def fake(url, timeout=20):
            self.calls.append(url)
            code = seq.pop(0)
            if code == 200:
                return self.body
            raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b'{"chart":{"result":null,'
                                                                           b'"error":{"description":"No data found"}}}'))
        entry._open_url = fake

    def test_url_and_429_retry_once_after_a_pause(self):
        self.answers(429, 200)
        c = entry.get_chart("demo", "5m", datetime.date(2026, 10, 19), today=datetime.date(2026, 10, 19))
        self.assertEqual(len(c["bars"]), 20)
        self.assertEqual(self.calls, ["https://query2.finance.yahoo.com/v8/finance/chart/DEMO.NS?range=1d&interval=5m"] * 2)
        self.assertEqual(self.sleeps, [entry.BACKOFF])

    def test_429_twice_falls_back_to_query1(self):
        self.answers(429, 429, 200)
        entry.get_chart("DEMO", "5m", datetime.date(2026, 10, 19), today=datetime.date(2026, 10, 19))
        self.assertEqual([u.split("/")[2] for u in self.calls],
                         ["query2.finance.yahoo.com", "query2.finance.yahoo.com", "query1.finance.yahoo.com"])

    def test_5xx_falls_back_without_waiting(self):
        self.answers(503, 200)
        entry.get_chart("DEMO", "5m", datetime.date(2026, 10, 19), today=datetime.date(2026, 10, 19))
        self.assertEqual(self.sleeps, [])
        self.assertIn("query1", self.calls[1])

    def test_still_busy_is_one_plain_error(self):
        self.answers(429, 429, 429, 429)
        code, out, err = run(["check", "DEMO", "--now", "2026-10-19 10:00"])
        self.assertEqual(code, 1)
        self.assertIn("HTTP 429", err)
        self.assertNotIn("Traceback", err)

    def test_unknown_symbol_404(self):
        self.answers(404)
        with self.assertRaises(entry.EntryError) as cm:
            entry.get_chart("ZZZZ", "5m", datetime.date(2026, 10, 19), today=datetime.date(2026, 10, 19))
        self.assertIn("No data found", str(cm.exception))

    def test_a_bse_symbol_never_reaches_yahoo(self):
        self.answers(200)
        code, _, err = run(["check", "RELIANCE.BO", "--now", "2026-10-19 10:00"])
        self.assertEqual(code, 1)
        self.assertIn("BSE listing", err)
        self.assertEqual(self.calls, [])

    def test_ranges_for_past_sessions(self):
        today = datetime.date(2026, 10, 21)
        self.assertEqual(entry.chart_range(today, today, "5m"), "1d")
        self.assertEqual(entry.chart_range(datetime.date(2026, 10, 19), today, "5m"), "5d")
        self.assertEqual(entry.chart_range(datetime.date(2026, 10, 1), today, "5m"), "1mo")
        self.assertEqual(entry.chart_range(datetime.date(2026, 8, 31), today, "5m"), "60d")
        with self.assertRaises(entry.EntryError):
            entry.chart_range(datetime.date(2026, 10, 1), today, "1m")
        with self.assertRaises(entry.EntryError):
            entry.chart_range(datetime.date(2026, 6, 1), today, "5m")


def live_provider(d, lag_until=None, calls=None):
    """Bars the way Yahoo serves them at `now`: closed bars final, the in-progress bar
    showing a spike to 1365.00 (above the high, then it closes under), plus a live tick.
    lag_until: before this UTC time Yahoo's last trade lags 30 seconds behind."""
    base = chart(d)

    def provider(now):
        if calls is not None:
            calls.append(now)
        nts = entry.ts_of(now)
        lagging = lag_until is not None and now < lag_until
        data_ts = nts - 30 if lagging else nts
        bars = []
        for b in base["bars"]:
            if b["t"] % 300 or b["t"] > data_ts:
                continue
            b = dict(b)
            if b["t"] + 300 > data_ts:
                b["c"], b["h"] = 1365.00, max(b["h"], 1365.00)
            bars.append(b)
        if data_ts % 300:
            bars.append({"t": data_ts, "o": 1365.0, "h": 1365.0, "l": 1365.0, "c": 1365.0, "v": 0})
        return {"symbol": "DEMO", "meta": {}, "bars": bars, "prev_close": 1352.4, "data_time": data_ts}
    return provider


class Watch(unittest.TestCase):
    OPTS = {"setup": "orb", "or_minutes": 15, "interval": "5m", "level": None, "min_volume_x": None, "cfg": None}

    def loop(self, provider, start, **kw):
        clock = entry.SimClock(ist(start))
        lines, fired = [], []
        code, res = entry.watch_loop(provider, clock.now, clock.sleep, lines.append, dict(self.OPTS),
                                     on_trigger=lambda r, now: fired.append((r, now)), **kw)
        return code, res, lines, fired, clock

    def test_fires_on_the_first_closed_bar_not_the_spike_in_progress(self):
        calls = []
        code, res, lines, fired, clock = self.loop(live_provider(doc(), calls=calls), "2026-10-19 09:52", every=60)
        self.assertEqual(code, 0)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0][0]["trigger"]["time"], "10:00 AM")
        self.assertEqual(fired[0][0]["trigger"]["price"], 1366.4)
        # it looked at 9:52 (9:50 bar spiking to 1365 in progress) and at 9:55:02 (that bar closed 1362.90)
        self.assertEqual([entry.to_ist(c).strftime("%H:%M:%S") for c in calls], ["09:52:00", "09:55:02", "10:00:02"])
        text = "\n".join(lines)
        self.assertIn(" 9:50 bar  closed 1362.90  0.90 under 1363.80", text)
        self.assertIn(" 9:55 bar  closed 1366.40  TRIGGERED", text)
        self.assertEqual(text.count("TRIGGERED"), 1)

    def test_waits_for_yahoo_to_catch_up(self):
        lag = ist("2026-10-19 10:00:30")
        calls = []
        code, res, lines, fired, clock = self.loop(live_provider(doc(), lag_until=lag, calls=calls),
                                                   "2026-10-19 09:57", every=30)
        self.assertEqual(code, 0)
        times = [entry.to_ist(c).strftime("%H:%M:%S") for c in calls]
        self.assertEqual(times, ["09:57:00", "10:00:02", "10:00:32"])   # 10:00:02: Yahoo still at 9:59:32
        self.assertEqual(fired[0][0]["trigger"]["time"], "10:00 AM")

    def test_from_before_the_open_prints_the_range_bar_by_bar(self):
        code, res, lines, fired, clock = self.loop(live_provider(doc()), "2026-10-19 09:00", every=30)
        self.assertEqual(code, 0)
        text = "\n".join(lines)
        self.assertIn("not open yet", lines[0])
        self.assertIn(" 9:25 bar  closed 1358.90  opening range set: high 1363.80, low 1351.30", text)
        self.assertEqual(sum(1 for x in lines if " bar  closed " in x), 9)    # 9:15 ... 9:55, one line each

    def test_a_computer_that_dozes_off_catches_up_when_it_wakes(self):
        class Dozing(entry.SimClock):
            naps = 0

            def sleep(self, seconds):
                entry.SimClock.sleep(self, seconds)
                self.naps += 1
                if self.naps == 3:                      # the laptop sleeps for 17 minutes mid-wait
                    self.t += datetime.timedelta(minutes=17)
        clock = Dozing(ist("2026-10-19 09:31"))
        calls, lines = [], []
        code, res = entry.watch_loop(live_provider(doc(), calls=calls), clock.now, clock.sleep, lines.append,
                                     dict(self.OPTS), every=30)
        self.assertEqual(code, 0)
        times = [entry.to_ist(c).strftime("%H:%M:%S") for c in calls]
        self.assertEqual(times[:2], ["09:31:00", "09:49:30"])   # straight after waking, not a long sleep later
        text = "\n".join(lines)
        for start in (" 9:30", " 9:35", " 9:40", " 9:45"):
            self.assertIn(start + " bar  closed", text)          # the bars it slept through, one line each
        self.assertEqual(res["trigger"]["time"], "10:00 AM")

    def test_expires_and_until(self):
        code, res, lines, fired, clock = self.loop(live_provider(no_breakout()), "2026-10-19 14:00", every=300)
        self.assertEqual(code, 1)
        self.assertEqual(res["status"], "expired")
        self.assertEqual(fired, [])
        self.assertEqual(entry.to_ist(clock.now()).strftime("%H:%M"), "14:45")
        until = DT(2026, 10, 19, 11, 0)
        code, res, lines, fired, clock = self.loop(live_provider(no_breakout()), "2026-10-19 10:30", every=300,
                                                   until=until)
        self.assertEqual(code, 1)
        self.assertIn("--until 11:00", lines[-1])

    def test_closed_day_and_repeated_errors(self):
        code, _, lines, _, _ = self.loop(live_provider(doc()), "2026-10-17 10:00")
        self.assertEqual(code, 1)
        self.assertIn("weekend", lines[0])
        code, _, lines, _, _ = self.loop(live_provider(doc()), "2026-10-20 10:00")
        self.assertIn("Dussehra", lines[0])

        def broken(now):
            raise entry.EntryError("Yahoo did not answer")
        code, _, lines, _, _ = self.loop(broken, "2026-10-19 10:00", every=30)
        self.assertEqual(code, 1)
        self.assertIn("Stopping after 5 failed reads", lines[-1])


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def test_check_write_is_the_contract_shape(self):
        code, text, _ = run(["check", "DEMO", "--offline", DEMO, "--now", "2026-10-19 10:00", "--write"])
        self.assertEqual(code, 0)
        self.assertIn("TRIGGERED 10:00 AM at 1366.40", text)
        self.assertIn("IST", text)
        self.assertIn("/trader-exit", text)
        with open(os.path.join("trader", "entry.json")) as fh:
            e = json.load(fh)
        want = {"created_by": "trader-entry", "symbol": "DEMO", "exchange": "NSE", "setup": "orb", "side": "buy",
                "date": "2026-10-19", "status": "triggered", "time": "10:00 AM", "bar_start": "09:55",
                "bar_index": 8, "price": 1366.4, "level": 1363.8, "interval": "5m",
                "opening_range": {"high": 1363.8, "low": 1351.3, "minutes": 15},
                "source": "yahoo", "checked_at": "2026-10-19T04:30:00Z"}
        for k, v in want.items():
            self.assertEqual(e[k], v, k)

    def test_check_waiting_json_and_exit_codes(self):
        code, text, _ = run(["check", "DEMO", "--offline", DEMO, "--now", "2026-10-19 09:57", "--json"])
        self.assertEqual(code, 1)
        r = json.loads(text)
        self.assertEqual(r["status"], "waiting")
        self.assertNotIn("_bars", r)
        code, text, _ = run(["check", "DEMO", "--offline", DEMO, "--now", "2026-10-19 09:57", "--write", "e.json"])
        with open("e.json") as fh:
            self.assertEqual(json.load(fh)["status"], "waiting")
        self.assertIn("still forming", text)

    def test_offline_gz_folder_suffixes_and_mismatch(self):
        os.mkdir("charts")
        with open(DEMO, "rb") as src, gzip.open(os.path.join("charts", "DEMO.json.gz"), "wb") as dst:
            dst.write(src.read())
        code, text, _ = run(["check", "demo", "--offline", "charts", "--now", "2026-10-19 10:00"])
        self.assertEqual(code, 0)
        code, text, _ = run(["check", "NSE:DEMO", "--offline", "charts", "--now", "2026-10-19 10:00"])
        self.assertEqual(code, 0)
        code, text, _ = run(["check", "DEMO.NS", "--offline", DEMO, "--now", "2026-10-19 10:00"])
        self.assertEqual(code, 0)
        code, _, err = run(["check", "RELIANCE", "--offline", DEMO])
        self.assertEqual(code, 1)
        self.assertIn("holds DEMO, not RELIANCE", err)

    def test_longs_only_and_flag_errors(self):
        code, _, err = run(["check", "DEMO", "--offline", DEMO, "--side", "short"])
        self.assertEqual(code, 1)
        self.assertIn("longs only", err)
        code, _, err = run(["check", "DEMO", "--offline", DEMO, "--level", "1300"])
        self.assertIn("--level is for --setup level", err)
        code, _, err = run(["check", "DEMO", "--offline", DEMO, "--setup", "level"])
        self.assertIn("needs --level", err)

    def test_config_is_read(self):
        os.mkdir("trader")
        with open(os.path.join("trader", "config.json"), "w") as fh:
            json.dump({"no_entries_after": "09:55"}, fh)
        code, text, _ = run(["check", "DEMO", "--offline", DEMO, "--now", "2026-10-19 10:00"])
        self.assertEqual(code, 1)
        self.assertIn("EXPIRED", text)
        with open(os.path.join("trader", "config.json"), "w") as fh:
            fh.write("{nope")
        code, _, err = run(["check", "DEMO", "--offline", DEMO])
        self.assertIn("not valid JSON", err)

    def test_watch_replays_the_file_and_writes_the_entry(self):
        code, text, _ = run(["watch", "DEMO", "--offline", DEMO, "--now", "2026-10-19 09:35", "--every", "30"])
        self.assertEqual(code, 0)
        self.assertIn(" 9:55 bar  closed 1366.40  TRIGGERED", text)
        with open(os.path.join("trader", "entry.json")) as fh:
            self.assertEqual(json.load(fh)["bar_index"], 8)
        code, text, _ = run(["watch", "DEMO", "--offline", DEMO, "--now", "2026-10-19 09:35", "--until", "09:45",
                             "--out", "x.json"])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists("x.json"))

    def test_watchlist(self):
        os.mkdir("charts")
        shutil.copy(DEMO, os.path.join("charts", "DEMO.json"))
        shutil.copy(TESTCO, os.path.join("charts", "TESTCO.json"))
        os.mkdir("trader")
        with open(os.path.join("trader", "watchlist.json"), "w") as fh:
            json.dump({"created_by": "trader-scanner", "hits": [
                {"symbol": "DEMO", "setups": ["orb"], "or_high": 1363.8, "or_low": 1351.3},
                {"symbol": "ZZZZ", "setups": ["orb"]}, {"symbol": "TESTCO", "setups": ["orb"]}]}, fh)
        code, text, _ = run(["watchlist", "--offline", "charts", "--now", "2026-10-19 10:00"])
        self.assertEqual(code, 0)
        rows = text.splitlines()
        self.assertTrue(rows[1].startswith("DEMO        triggered  10:00 AM  1366.40   1363.80"), rows[1])
        self.assertIn("ZZZZ        error: no offline file", rows[2])
        self.assertIn("TESTCO      error: TESTCO has 1m bars", rows[3])
        self.assertIn("1 of 3 triggered", text)
        code, text, _ = run(["watchlist", "--offline", "charts", "--now", "2026-10-19 09:57", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(text)[0]["result"]["status"], "waiting")
        code, _, err = run(["watchlist", "--watchlist", "missing.json"])
        self.assertIn("run /trader-scanner first", err)


class KiteSource(unittest.TestCase):
    """The user's own Kite Connect, read only: the FakeKite answers from the same
    synthetic DEMO bars (tests/fixtures/shared/kite/), and Yahoo must not be called."""

    TOKEN = "demo_access_token"

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        self.env = {k: os.environ.pop(k) for k in ("KITE_API_KEY", "KITE_ACCESS_TOKEN") if k in os.environ}
        self.saved = (entry.kite_urlopen, entry._open_url, entry._sleep, entry.KITE_PAUSE)
        self.fake = FakeKite().install(entry)
        self.yahoo = []
        with open(DEMO) as fh:
            body = fh.read()

        def yahoo(url, timeout=20):
            self.yahoo.append(url)
            return body
        entry._open_url = yahoo
        entry._sleep = lambda s: None
        entry.KITE_PAUSE = 0

    def tearDown(self):
        entry.kite_urlopen, entry._open_url, entry._sleep, entry.KITE_PAUSE = self.saved
        os.environ.update(self.env)
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def login(self, days_ago=0):
        """A session saved the way trader-broker/kite.py saves it, logged in a minute ago (or days ago)."""
        when = entry.to_ist(entry.utc_now()) - datetime.timedelta(days=days_ago, minutes=1)
        FakeKite.session_file(self.d, login_ist=when)

    def check(self, *args):
        return run(["check", "DEMO", "--now", "2026-10-19 10:00"] + list(args))

    def test_kite_gives_the_same_trigger_as_yahoo(self):
        self.login()
        code, text, err = self.check("--json")
        self.assertEqual(code, 0, err)
        res = json.loads(text)
        yahoo = at("2026-10-19 10:00")
        self.assertEqual(res["source"], "kite")
        for k in ("time", "price", "bar_index", "bar_start", "pct_over"):
            self.assertEqual(res["trigger"][k], yahoo["trigger"][k], k)
        self.assertEqual((res["trigger"]["time"], res["trigger"]["price"], res["trigger"]["bar_index"]),
                         ("10:00 AM", 1366.4, 8))
        self.assertEqual(res["opening_range"], yahoo["opening_range"])
        self.assertEqual(res["prev_close"], 1352.4)           # from Kite's quote
        self.assertEqual(self.fake.methods(), {"GET"})        # read only
        self.assertEqual(self.fake.paths(), ["/instruments/NSE", "/instruments/historical/9000001/5minute",
                                             "/quote"])
        self.assertEqual(self.yahoo, [])
        _, url, headers = self.fake.requests[1]
        self.assertIn("from=2026-10-19+09%3A00%3A00&to=2026-10-19+16%3A00%3A00", url)
        self.assertEqual(headers.get("X-kite-version"), "3")
        self.assertEqual(headers.get("Authorization"), "token demo_api_key:" + self.TOKEN)

    def test_the_text_names_the_source(self):
        self.login()
        code, text, _ = self.check("--write")
        self.assertEqual(code, 0)
        self.assertIn("on 5-minute bars (Kite, live)", text.splitlines()[0])
        self.assertIn("TRIGGERED 10:00 AM at 1366.40", text)
        with open(os.path.join("trader", "entry.json")) as fh:
            self.assertEqual(json.load(fh)["source"], "kite")
        code, text, _ = run(["check", "DEMO", "--now", "2026-10-19 10:00", "--offline", DEMO])
        self.assertIn("(Yahoo, free, can lag)", text.splitlines()[0])

    def test_min_volume_on_kite_bars(self):
        self.login()
        code, text, _ = run(["check", "DEMO", "--now", "2026-10-19 10:35", "--min-volume-x", "1", "--json"])
        t = json.loads(text)["trigger"]
        self.assertEqual((t["time"], t["price"], t["bar_index"]), ("10:30 AM", 1371.6, 14))

    def test_the_forming_kite_candle_waits_for_its_close(self):
        self.login()
        code, text, _ = run(["check", "DEMO", "--now", "2026-10-19 09:58", "--json"])
        res = json.loads(text)
        self.assertEqual((code, res["status"]), (1, "waiting"))
        self.assertEqual(res["forming"]["bar_start"], "09:55")

    def test_the_token_never_shows(self):
        self.login()
        with open(os.path.join("trader", "watchlist.json"), "w") as fh:
            json.dump({"hits": [{"symbol": "DEMO"}, {"symbol": "ZZZZ"}]}, fh)
        outs = [self.check("--write"), self.check("--json"),
                run(["watchlist", "--now", "2026-10-19 10:00"]),
                run(["watch", "DEMO", "--now", "2026-10-19 09:50", "--out", os.path.join("trader", "w.json")])]
        for code, text, err in outs:
            self.assertNotIn(self.TOKEN, text + err)
        for name in ("entry.json", "w.json"):
            with open(os.path.join("trader", name)) as fh:
                self.assertNotIn(self.TOKEN, fh.read())

    def test_watch_replays_on_kite(self):
        self.login()
        code, text, err = run(["watch", "DEMO", "--now", "2026-10-19 09:50", "--out", "e.json"])
        self.assertEqual(code, 0, err)
        self.assertIn("TRIGGER", text)
        with open("e.json") as fh:
            e = json.load(fh)
        self.assertEqual((e["time"], e["price"], e["source"]), ("10:00 AM", 1366.4, "kite"))
        self.assertEqual(self.fake.methods(), {"GET"})
        self.assertEqual(self.yahoo, [])

    def test_watchlist_on_kite(self):
        self.login()
        with open(os.path.join("trader", "watchlist.json"), "w") as fh:
            json.dump({"hits": [{"symbol": "DEMO"}, {"symbol": "ZZZZ"}]}, fh)
        code, text, _ = run(["watchlist", "--now", "2026-10-19 10:00"])
        self.assertEqual(code, 0)
        self.assertIn("DEMO        triggered  10:00 AM  1366.40", text)
        self.assertIn("ZZZZ        error: Kite has no NSE equity called ZZZZ", text)
        self.assertIn("Kite bars, live. Nothing was placed.", text)

    def test_an_old_session_falls_back_to_yahoo_and_says_so(self):
        self.login(days_ago=2)                     # Zerodha ended it at 6:00 AM the next day
        code, text, err = self.check()
        self.assertEqual(code, 0, err)
        self.assertTrue(text.startswith("Note: no Kite session today, so free Yahoo data"), text)
        self.assertEqual(text.count("no Kite session today"), 1)
        self.assertIn("(Yahoo, free, can lag)", text)
        self.assertEqual(len(self.yahoo), 1)
        self.assertEqual(self.fake.requests, [])
        code, text, _ = self.check("--json")
        self.assertIn("no Kite session today", json.loads(text)["source_note"])

    def test_source_kite_without_a_session_is_refused(self):
        code, text, err = self.check("--source", "kite")
        self.assertEqual(code, 1)
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertIn("entry.py: no Kite session for today: log in first with /trader-broker (kite.py login)", err)
        self.assertEqual((self.fake.requests, self.yahoo), ([], []))

    def test_yahoo_by_choice_or_by_config(self):
        self.login()
        code, text, _ = self.check("--source", "yahoo")
        self.assertIn("(Yahoo, free, can lag)", text)
        with open(os.path.join("trader", "config.json"), "w") as fh:
            json.dump({"data_source": "yahoo"}, fh)
        code, text, _ = self.check()
        self.assertIn("(Yahoo, free, can lag)", text)
        self.assertNotIn("Note:", text)
        self.assertEqual((len(self.yahoo), self.fake.requests), (2, []))
        code, text, _ = self.check("--source", "kite")        # the flag wins over the config
        self.assertIn("(Kite, live)", text)

    def test_offline_wins_over_kite(self):
        self.login()
        code, text, _ = self.check("--offline", DEMO)
        self.assertEqual(code, 0)
        self.assertEqual((self.fake.requests, self.yahoo), ([], []))

    def test_a_refused_session_is_one_plain_line(self):
        self.login()
        self.fake.fail["/instruments"] = (403, os.path.join(KDIR, "error-token-synthetic.json"))
        code, text, err = self.check()
        self.assertEqual(code, 1)
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertIn("entry.py: Kite refused the session (TokenException: Incorrect `api_key` or "
                      "`access_token`.). Log in again with /trader-broker (kite.py login)", err)
        self.assertNotIn("Traceback", err)
        self.assertNotIn(self.TOKEN, err)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-entry", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-exit", "SKILL.md"), encoding="utf-8") as fh:
            sibling = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-entry\n"))
        self.assertGreater(len(text.splitlines()), 60)
        safety = text[text.index("## Safety"):]
        self.assertEqual(safety, sibling[sibling.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("bar by bar", "can lag", "day trading loses most people money", "/trader-exit", "longs only",
                     "in the background", "/loop", "nothing was placed", "10:00 am", "1366.40", "bar 8", "nse",
                     "india time", "synthetic", "sebi-registered", "needs approval"):
            self.assertIn(must, low)
        for never in ("\u2014", "tick " + "by tick", "real " + "time", "pro" + "fit", "nyse", "new york",
                      "alpaca", "tsla"):
            self.assertNotIn(never, low)
        self.assertNotRegex(text, r"\$\d")                     # rupees, not dollars


if __name__ == "__main__":
    unittest.main()
