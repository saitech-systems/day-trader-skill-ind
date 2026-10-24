"""trader-timemachine tests. Fixture: tests/fixtures/shared/DEMO-60d5m-synthetic.json.gz,
SYNTHETIC five-minute bars (not market data) for a made-up NSE stock, DEMO (Yahoo DEMO.NS),
in Yahoo's chart JSON v8 shape: 60 sessions, 2026-07-24 to Monday 2026-10-19, "fetched" at
10:49:45 IST with the in-progress 10:45 bar and a live tick. tests/fixtures/make_synthetic.py
builds it from a seed. The numbers pinned below are this tool's own output on that file:
they prove the replays are repeatable, not that DEMO's odds mean anything.
"""

import copy
import datetime
import gzip
import hashlib
import io
import json
import os
import random
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX
from _kite import FakeKite, KDIR

tm = load("trader-timemachine", "timemachine")

DEMO = os.path.join(FIX, "shared", "DEMO-60d5m-synthetic.json.gz")
with gzip.open(DEMO, "rt", encoding="utf-8") as _fh:
    TEXT = _fh.read()
NOW = "2026-10-19 10:50"
NOW_UTC = tm.parse_now(NOW)

# the synthetic story's plan, as trader-exit writes it
PLAN = {"created_by": "trader-exit", "symbol": "DEMO", "side": "buy", "date": "2026-10-19", "setup": "orb",
        "exchange": "NSE", "tick": 0.1,
        "entry": 1366.40, "entry_time": "10:00 AM", "entry_bar": 8, "interval": "5m",
        "opening_range": {"high": 1363.80, "low": 1351.30, "minutes": 15},
        "stop": 1357.50, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
        "target": 1375.30, "target_r": 1.0, "target_label": "1R (one times the risk)",
        "risk_per_share": 8.90, "reward_risk": 1.0, "flat_by": "15:05"}

# fingerprints of this tool's seed-19 run on the synthetic fixture: any change to the draws shows here
SIM_BANDS = "56206aa2c47a3869"
SIM_PATHS = "066475bd10822a9a"
SIM_TRADES = "ebbd0eb9b9ef19e6"
SIM_HIST = {"lo": 1332.15, "hi": 1408.01,
            "bins": [107, 109, 136, 180, 278, 364, 456, 525, 630, 708, 785, 763, 710, 711, 653, 626, 520, 422,
                     336, 277, 206, 152, 117, 73]}


def sha(x):
    return hashlib.sha256(json.dumps(x).encode("utf-8")).hexdigest()[:16]


def plan(**kw):
    raw = dict(PLAN)
    raw.update(kw)
    return tm.normalize_plan(raw)


def machine(p=None, text=TEXT, now=NOW_UTC, **kw):
    return tm.time_machine(text, p or plan(), now, **kw)


def edited(fn):
    """The fixture's chart JSON, changed by fn(result), back as text."""
    doc = json.loads(TEXT)
    fn(doc["chart"]["result"][0])
    return json.dumps(doc)


def keep_bars(result, keep):
    """Keep only the bars where keep(ts) is true."""
    q = result["indicators"]["quote"][0]
    idx = [i for i, ts in enumerate(result["timestamp"]) if keep(ts)]
    result["timestamp"] = [result["timestamp"][i] for i in idx]
    for k in q:
        q[k] = [q[k][i] for i in idx]


def ist(ts):
    return tm.to_ist(tm.utc_of(ts))


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = tm.main(argv)
    return code, out.getvalue(), err.getvalue()


class Story(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = machine()

    def test_the_odds(self):
        m = self.doc["mc"]
        self.assertEqual((m["replays"], m["seed"], m["window"], m["until"], m["until_from"]),
                         (10000, 19, 2, "15:05", "flat_by"))
        self.assertEqual(m["pool_sessions"], 59)
        self.assertEqual(m["bars_left"], 61)
        self.assertEqual((m["pool_from"], m["pool_to"]), ("2026-07-24", "2026-10-16"))
        self.assertEqual(m["p_target"], 0.5359)
        self.assertEqual(m["p_stop"], 0.4505)
        self.assertEqual(m["p_neither"], 0.0136)
        self.assertEqual(m["p_green"], 0.5432)
        self.assertEqual(m["median_close"], 1369.73)
        self.assertEqual(m["p5_close"], 1343.24)
        self.assertEqual(m["p95_close"], 1397.6)
        self.assertEqual(m["avg_R"], 0.086)
        self.assertEqual(m["r_at_target"], 1.0)

    def test_bit_for_bit(self):
        m = self.doc["mc"]
        self.assertEqual(sha(m["bands"]), SIM_BANDS)
        self.assertEqual(len(m["sample_paths"]), 160)
        self.assertEqual(sha(m["sample_paths"]), SIM_PATHS)
        self.assertEqual(m["hist"], SIM_HIST)
        self.assertEqual(m["bands"][-1], [1343.93, 1359.12, 1369.58, 1381.64, 1396.14])

    def test_the_history_replay(self):
        h = self.doc["history"]
        self.assertEqual((h["sessions"], h["triggered"]), (59, 47))
        self.assertEqual((h["target"], h["stop"], h["close"], h["green"]), (23, 21, 3, 23))
        self.assertEqual(h["avg_R"], 0.025)
        self.assertEqual(h["rule"], {"setup": "orb", "or_minutes": 15, "stop_rule": "or_mid", "target_r": 1.0})
        slim = [{"date": t["date"], "entry": t["entry"], "outcome": t["outcome"], "R": t["R"]} for t in h["trades"]]
        self.assertEqual(sha(slim), SIM_TRADES)
        first = h["trades"][0]
        self.assertEqual((first["date"], first["time"], first["entry"], first["stop"], first["target"],
                          first["outcome"]), ("2026-07-24", "9:55 AM", 1210.2, 1204.9, 1215.5, "target"))
        # every stop and target sits on the NSE tick (0.10 between Rs 1,000 and 5,000)
        for t in h["trades"]:
            for k in ("stop", "target"):
                self.assertAlmostEqual(round(t[k] * 10) / 10.0, t[k], places=9, msg=t)

    def test_the_verdict_says_coin_flip(self):
        self.assertEqual(self.doc["verdict"], "Close to a coin flip: target first 53.6 % vs stop first 45.1 %.")
        # 1R breaks even at 50 % of the replays that hit one side; the target got 54.3 %
        gap, share, even = tm.lean(self.doc["mc"])
        self.assertEqual((round(share, 3), even), (0.543, 0.5))
        self.assertEqual(self.doc["verdict_notes"], [])

    def test_bands_start_at_the_entry(self):
        m = self.doc["mc"]
        self.assertEqual(m["bands"][0], [1366.4] * 5)
        self.assertEqual(len(m["bands"]), 62)
        self.assertEqual((m["band_times"][0], m["band_times"][-1], len(m["band_times"])), ("10:00", "15:05", 62))
        self.assertTrue(all(p[0] == 1366.4 and len(p) == 62 for p in m["sample_paths"]))

    def test_the_plan_is_carried_through(self):
        p = self.doc["plan"]
        self.assertEqual((p["symbol"], p["entry"], p["stop"], p["target"], p["entry_bar"], p["entry_time"]),
                         ("DEMO", 1366.4, 1357.5, 1375.3, 8, "10:00 AM"))
        self.assertEqual((p["risk_per_share"], p["target_r"], p["flat_by"]), (8.9, 1.0, "15:05"))
        self.assertEqual(self.doc["warnings"], [])
        self.assertEqual((self.doc["data"]["symbol"], self.doc["data"]["yahoo_symbol"]), ("DEMO", "DEMO.NS"))
        self.assertEqual(self.doc["data"]["last_bar"], "2026-10-19 10:40 IST")

    def test_the_text_report(self):
        text = "\n".join(tm.report(self.doc))
        for line in ("DEMO buy, Monday 2026-10-19: entry 1366.40 at 10:00 AM (bar 8), stop 1357.50, target 1375.30",
                     "10,000 replays", "10:00 AM to 3:05 PM, your flat-by time (61 five-minute bars)", "seed 19",
                     "59 sessions with every bar from 9:15 AM to 3:05 PM",
                     "target first   53.6 %", "stop first     45.1 %", "neither        1.4 %    still open at 3:05 PM",
                     "green          54.3 %", "median 1369.73, 90 % of replays between 1343.24 and 1397.60",
                     "Average R: +0.086", "fired 47 times: 23 target, 21 stop, 3 neither. Average R +0.025",
                     "Close to a coin flip: target first 53.6 % vs stop first 45.1 %.", "not a prediction",
                     "--seed 19"):
            self.assertIn(line, text)
        for word in ("profit", "P&L", "guarantee", "real time", "tick by tick", "$", "New York", "ET"):
            self.assertNotIn(word, text)


class Seeds(unittest.TestCase):
    def test_same_seed_same_numbers(self):
        a = machine(replays=2000, seed=5)["mc"]
        b = machine(replays=2000, seed=5)["mc"]
        self.assertEqual(a, b)

    def test_another_seed_gives_other_numbers(self):
        m = machine(seed=7)["mc"]
        self.assertNotEqual((m["p_target"], m["p_stop"], m["median_close"]), (0.5359, 0.4505, 1369.73))
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)
        self.assertNotEqual(sha(m["bands"]), SIM_BANDS)

    def test_the_draws_are_stable_across_pythons(self):
        # random.Random(seed), randint and choice are stable since Python 3.2; the pinned numbers
        # above are the real proof, this pins the first draws so a failure points at the RNG
        r = random.Random(19)
        self.assertEqual([r.randint(-2, 2) for _ in range(8)], [-2, 2, -2, 2, -1, 1, 0, 2])
        self.assertEqual(r.choice(range(59)), 18)

    def test_window_and_replays(self):
        a = machine(replays=500, window=0)["mc"]
        self.assertEqual((a["replays"], a["window"]), (500, 0))
        self.assertNotEqual(a["bands"], machine(replays=500)["mc"]["bands"])
        with self.assertRaises(tm.TMError):
            machine(replays=0)
        with self.assertRaises(tm.TMError):
            machine(window=11)


class Bars(unittest.TestCase):
    def setUp(self):
        self.result = tm.chart_result(TEXT, "DEMO")

    def test_the_in_progress_tick_is_dropped(self):
        meta, bars, dropped = tm.parse_bars(self.result, NOW_UTC)
        self.assertEqual(dropped, {"no_price": 0, "off_grid": 1, "not_complete": 1, "outside_hours": 0})
        last = bars[-1]
        self.assertEqual((last["date"], last["start"], last["slot"]), ("2026-10-19", "10:40", 17))
        # with the real clock (any time later) the data's own time still marks 10:45 as in progress
        self.assertEqual(tm.parse_bars(self.result, tm.parse_now(None))[1][-1]["start"], "10:40")
        entry = [b for b in bars if b["date"] == "2026-10-19" and b["slot"] == 8][0]
        self.assertEqual((entry["start"], entry["c"]), ("09:55", 1366.4))

    def test_now_pins_the_clock(self):
        bars = tm.parse_bars(self.result, tm.parse_now("2026-10-19 09:57"))[1]
        today = [b for b in bars if b["date"] == "2026-10-19"]
        self.assertEqual(len(today), 8)
        with self.assertRaises(tm.TMError) as cm:
            machine(now=tm.parse_now("2026-10-19 09:57"))
        self.assertIn("not in the data", str(cm.exception))
        self.assertIn("09:50 bar", str(cm.exception))
        self.assertEqual(machine(now=tm.parse_now("2026-10-19 10:00"), replays=200)["mc"]["pool_sessions"], 59)

    def test_india_time_has_no_daylight_saving(self):
        stamps = [1774842300,   # 2026-03-30 03:45Z = 9:15 IST
                  1793591100,   # 2026-11-02 03:45Z = 9:15 IST, the same UTC time after the US clock change
                  1793590800,   # 2026-11-02 03:40Z = 9:10 IST, the pre-open: outside the session
                  1793612700]   # 2026-11-02 09:45Z = 15:15 IST
        res = {"meta": {"symbol": "X.NS"}, "timestamp": stamps,
               "indicators": {"quote": [{"open": [1] * 4, "high": [1] * 4, "low": [1] * 4, "close": [1] * 4,
                                         "volume": [0] * 4}]}}
        meta, bars, dropped = tm.parse_bars(res, tm.parse_now("2027-01-01T00:00Z"))
        self.assertEqual(sorted((b["date"], b["start"], b["slot"]) for b in bars),
                         [("2026-03-30", "09:15", 0), ("2026-11-02", "09:15", 0), ("2026-11-02", "15:15", 72)])
        self.assertEqual(dropped["outside_hours"], 1)
        self.assertEqual(tm.to_ist(tm.utc_of(1774842300)).strftime("%H:%M"), "09:15")
        self.assertEqual(tm.parse_now("2026-10-19 10:15"), tm.datetime.datetime(2026, 10, 19, 4, 45))
        self.assertEqual(tm.parse_now("2026-06-19 10:15"), tm.datetime.datetime(2026, 6, 19, 4, 45))

    def test_a_session_with_a_gap_is_left_out(self):
        def cut(res):  # 2026-09-04 stops at 13:00, like a halted stock: 45 bars
            keep_bars(res, lambda ts: not (ist(ts).date().isoformat() == "2026-09-04" and ist(ts).hour >= 13))
        doc = machine(text=edited(cut), replays=300)
        self.assertEqual(doc["mc"]["pool_sessions"], 58)
        self.assertEqual(doc["data"]["short_sessions_left_out"], [{"date": "2026-09-04", "bars": 45}])
        self.assertEqual(doc["history"]["sessions"], 58)
        self.assertNotIn("2026-09-04", [t["date"] for t in doc["history"]["trades"]])
        self.assertIn("Left out 1 session(s) with a gap before 3:05 PM: 2026-09-04", "\n".join(tm.report(doc)))

    def test_a_gap_after_the_replay_end_does_not_matter(self):
        # a stock that stops trading at 15:15 (the closing auction for F&O stocks from 2026-08-03 to
        # 2026-12-06) still has every bar to 15:05; replays to the 15:30 close leave that day out
        def auction(res):
            keep_bars(res, lambda ts: not (ist(ts).date().isoformat() == "2026-09-08"
                                           and (ist(ts).hour, ist(ts).minute) >= (15, 15)))
        text = edited(auction)
        self.assertEqual(machine(text=text, replays=200)["mc"]["pool_sessions"], 59)
        doc = machine(text=text, replays=200, until="15:30")
        self.assertEqual(doc["mc"]["pool_sessions"], 58)
        self.assertEqual(doc["data"]["short_sessions_left_out"], [{"date": "2026-09-08", "bars": 72}])

    def test_today_is_not_in_the_pool(self):
        doc = machine(replays=200)
        self.assertEqual(doc["mc"]["pool_to"], "2026-10-16")
        self.assertNotIn("2026-10-19", [t["date"] for t in doc["history"]["trades"]])

    def test_a_missing_price_is_dropped(self):
        def hole(res):
            res["indicators"]["quote"][0]["close"][5] = None
        doc = machine(text=edited(hole), replays=200)
        self.assertEqual(doc["data"]["dropped"]["no_price"], 1)
        self.assertEqual(doc["mc"]["pool_sessions"], 58)   # 2026-07-24 now has a gap

    def test_empty_and_broken_data(self):
        empty = json.dumps({"chart": {"result": [{"meta": {"symbol": "DEMO.NS"}, "timestamp": [],
                                                  "indicators": {"quote": [{}]}}], "error": None}})
        with self.assertRaises(tm.TMError) as cm:
            machine(text=empty)
        self.assertIn("no complete 5-minute bars", str(cm.exception))
        gone = json.dumps({"chart": {"result": None, "error": {"code": "Not Found",
                                                               "description": "No data found, symbol may be delisted"}}})
        with self.assertRaises(tm.TMError) as cm:
            machine(text=gone)
        self.assertIn("No data found", str(cm.exception))
        with self.assertRaises(tm.TMError):
            machine(text="<html>rate limited</html>")

    def test_fewer_than_20_sessions_is_refused(self):
        def trim(res):
            keep_bars(res, lambda ts: ist(ts).date().isoformat() >= "2026-09-21")
        with self.assertRaises(tm.TMError) as cm:
            machine(text=edited(trim))
        self.assertIn("only 19 session(s)", str(cm.exception))
        self.assertIn("at least 20", str(cm.exception))

        def twenty(res):
            keep_bars(res, lambda ts: ist(ts).date().isoformat() >= "2026-09-18")
        self.assertEqual(machine(text=edited(twenty), replays=200)["mc"]["pool_sessions"], 20)

    def test_holidays_and_special_sessions_are_refused(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(date="2026-10-20"))
        self.assertIn("not an NSE session (NSE holiday: Dussehra)", str(cm.exception))
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(date="2026-10-18"))
        self.assertIn("weekend", str(cm.exception))
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(date="2026-11-08", entry_time="2:40 PM", entry_bar=None))
        self.assertIn("special session (Muhurat trading", str(cm.exception))
        self.assertIn("14:20 to 15:30", str(cm.exception))

    def test_the_data_must_be_the_plans_symbol(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(symbol="RELIANCE"))
        self.assertIn("data is for DEMO.NS", str(cm.exception))
        self.assertIn("RELIANCE.NS", str(cm.exception))


class Plans(unittest.TestCase):
    def test_an_entry_not_in_the_data_is_refused(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(date="2026-10-21"))
        self.assertIn("not in the data", str(cm.exception))
        self.assertIn("no bars for that day", str(cm.exception))
        with self.assertRaises(tm.TMError) as cm:      # the 10:50 bar has not happened in this data yet
            machine(plan(entry_time="10:55 AM", entry_bar=None))
        self.assertIn("not in the data", str(cm.exception))

    def test_plan_checks(self):
        cases = [
            (dict(entry_time="10:02 AM", entry_bar=None), "5-minute grid"),
            (dict(entry_time="3:30 PM", entry_bar=None), "between 9:20 AM and 3:25 PM"),
            (dict(entry_bar=9), "disagrees with itself"),
            (dict(entry_time=None, entry_bar=None), "entry_time"),
            (dict(interval="1m"), "7 days of 1-min bars"),
            (dict(stop=1367.0), "stop < entry < target"),
            (dict(side="short"), "buy or sell"),
            (dict(symbol=""), "NSE symbol"),
            (dict(symbol="DEMO.BO"), "BSE listing"),
            (dict(entry="abc"), "number for entry"),
            (dict(date="Oct 19"), "date like"),
            (dict(opening_range={"minutes": 7}), "multiple of 5"),
        ]
        for change, words in cases:
            with self.assertRaises(tm.TMError, msg=change) as cm:
                plan(**change)
            self.assertIn(words, str(cm.exception), change)

    def test_nse_symbols(self):
        self.assertEqual(plan(symbol="demo.ns")["symbol"], "DEMO")
        self.assertEqual(plan(symbol="NSE:DEMO")["symbol"], "DEMO")
        self.assertEqual(plan(symbol="m&m")["symbol"], "M&M")

    def test_entry_bar_or_entry_time_alone(self):
        self.assertEqual(plan(entry_time=None)["entry_time"], "10:00 AM")
        self.assertEqual(plan(entry_bar=None)["entry_bar"], 8)
        self.assertEqual(plan(entry_time="10:00", entry_bar=None)["entry_bar"], 8)
        self.assertEqual(plan(entry_time="10:00am", entry_bar=None)["entry_bar"], 8)

    def test_until_defaults_to_flat_by_else_the_close(self):
        m = machine(replays=500)["mc"]
        self.assertEqual((m["until"], m["until_from"], m["bars_left"]), ("15:05", "flat_by", 61))
        m = machine(plan(flat_by=None), replays=500)["mc"]
        self.assertEqual((m["until"], m["until_from"], m["bars_left"]), ("15:30", "close", 66))
        doc = machine(plan(flat_by="15:07"), replays=500)
        self.assertEqual(doc["mc"]["until"], "15:05")
        self.assertIn("between bars: the replays end at 3:05 PM", doc["warnings"][0])
        self.assertEqual(machine(plan(flat_by="15:45"), replays=500)["mc"]["until"], "15:30")
        doc = machine(plan(flat_by="soon"), replays=500)
        self.assertEqual(doc["mc"]["until"], "15:30")
        self.assertIn("not a time", doc["warnings"][0])

    def test_until_cuts_the_replay(self):
        doc = machine(until="15:30", replays=500)
        m = doc["mc"]
        self.assertEqual((m["until"], m["until_from"], m["bars_left"], m["band_times"][-1]),
                         ("15:30", "until", 66, "15:30"))
        self.assertIn("still open at 3:30 PM", "\n".join(tm.report(doc)))
        self.assertEqual(machine(until="3:30 PM", replays=500)["mc"], m)
        m = machine(until="14:00", replays=500)["mc"]
        self.assertEqual((m["until"], m["bars_left"]), ("14:00", 48))
        for bad, words in (("10:00 AM", "nothing to replay"), ("15:35", "no later than 3:30 PM"),
                           ("15:07", "5-minute grid"), ("noon", "India time")):
            with self.assertRaises(tm.TMError) as cm:
                machine(until=bad, replays=10)
            self.assertIn(words, str(cm.exception), bad)

    def test_the_full_session_numbers(self):
        m = machine(until="15:30")["mc"]
        self.assertEqual((m["p_target"], m["p_stop"], m["p_neither"], m["avg_R"], m["median_close"]),
                         (0.5459, 0.4491, 0.005, 0.097, 1369.0))

    def test_an_entry_outside_its_bar_is_flagged(self):
        doc = machine(plan(entry=1370.0, stop=1362.0, target=1378.0), replays=200)
        self.assertIn("outside that bar's range", doc["warnings"][0])

    def test_stops_round_onto_the_tick_away_from_the_entry(self):
        self.assertEqual(tm.tick_away(1357.55, True), 1357.5)
        self.assertEqual(tm.tick_away(1357.55, False), 1357.6)
        self.assertEqual(tm.tick_away(249.995, True), 249.99)
        self.assertEqual(tm.tick_away(612.33, True), 612.3)
        self.assertEqual(tm.tick_away(612.33, False), 612.35)
        self.assertEqual(tm.tick_near(1375.3000000000002), 1375.3)
        self.assertEqual(tm.tick_near(612.325), 612.35)

    def test_the_stop_rule_is_worked_out_when_not_given(self):
        rule = machine(plan(stop_rule=None), replays=100)["history"]["rule"]
        self.assertEqual(rule["stop_rule"], "or_mid")
        rule = machine(plan(stop_rule=None, stop=1351.30, target=1381.50), replays=100)["history"]["rule"]
        self.assertEqual((rule["stop_rule"], rule["target_r"]), ("or_low", 1.0))
        rule = machine(plan(stop_rule="atr", stop=1360.0, target=1379.2), replays=100)["history"]["rule"]
        self.assertEqual((rule["stop_rule"], rule["stop_pct"], rule["target_r"]), ("pct", 0.4684, 2.0))

    def test_a_setup_other_than_orb_skips_the_history(self):
        doc = machine(plan(setup="flag"), replays=200)
        self.assertIsNone(doc["history"]["rule"])
        self.assertIn("orb", doc["history"]["note"])
        self.assertEqual(doc["mc"]["pool_sessions"], 59)
        self.assertIn("History: skipped", "\n".join(tm.report(doc)))

    def test_a_sell_plan_mirrors_the_rules(self):
        doc = machine(plan(side="sell", stop=1375.30, target=1357.50, stop_rule="or_high"), replays=2000)
        m = doc["mc"]
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)
        self.assertEqual(m["r_at_target"], 1.0)
        self.assertEqual(doc["history"]["rule"]["stop_rule"], "or_high")
        for t in doc["history"]["trades"]:
            self.assertTrue(t["target"] < t["entry"] < t["stop"], t)
        self.assertIn("below the entry", "\n".join(tm.report(doc)))

    def test_the_verdict_thresholds_at_1r(self):
        # 1R: the target's share of the replays that hit one side against 50 %
        self.assertTrue(tm.verdict({"p_target": 0.30, "p_stop": 0.26}).startswith("Close to a coin flip"))
        self.assertTrue(tm.verdict({"p_target": 0.40, "p_stop": 0.30}).startswith("Leans toward the target"))
        self.assertTrue(tm.verdict({"p_target": 0.20, "p_stop": 0.30}).startswith("Leans toward the stop"))
        self.assertTrue(tm.verdict({"p_target": 0.60, "p_stop": 0.30}).startswith("Strongly toward the target"))
        self.assertEqual(tm.verdict({"p_target": 0.10, "p_stop": 0.60, "r_at_target": 1.0}),
                         "Strongly toward the stop: target first 10.0 % vs stop first 60.0 %.")
        self.assertEqual(tm.verdict({"p_target": 0.0, "p_stop": 0.0, "until": "15:05"}),
                         "No replay reached the target or the stop by 3:05 PM: the price at 3:05 PM decides every one.")

    def test_a_2r_target_is_judged_against_its_break_even(self):
        # a 2R plan needs the target first in 33 % of the replays that hit one side; this one got 32 %
        mc = {"p_target": 0.241, "p_stop": 0.505, "p_neither": 0.254, "r_at_target": 2.0, "avg_R": 0.095}
        self.assertEqual(tm.verdict(mc), "Close to break-even: at 2R the target has to come first in 33 % of the "
                                         "replays that hit one side; it did in 32 % (target first 24.1 % vs stop "
                                         "first 50.5 %).")
        self.assertEqual(tm.verdict_notes(mc), [])
        lean = dict(mc, p_target=0.15, p_stop=0.55)
        self.assertTrue(tm.verdict(lean).startswith("Leans toward the stop: at 2R the target has to come first in "
                                                    "33 % of the replays that hit one side; it did in 21 %"))
        self.assertEqual(tm.verdict_notes(lean), ["Average R is +0.095 all the same: the replays that hit neither "
                                                  "pull it the other way."])
        self.assertTrue(tm.verdict(dict(mc, p_target=0.30, p_stop=0.40)).startswith("Leans toward the target"))
        self.assertTrue(tm.verdict(dict(mc, p_target=0.40, p_stop=0.40)).startswith("Strongly toward the target"))
        self.assertIn("at 1.5R the target has to come first in 40 %", tm.verdict(dict(mc, r_at_target=1.5)))

    def test_2r_on_the_fixture_near_break_even(self):
        # the story's entry and stop with a 2R target 1384.20: 30.6 % vs 54.0 % looks lopsided, but 2R needs a third
        doc = machine(plan(target=1384.20))
        m = doc["mc"]
        self.assertEqual((m["r_at_target"], m["p_target"], m["p_stop"], m["p_neither"], m["avg_R"]),
                         (2.0, 0.3062, 0.5404, 0.1534, 0.158))
        self.assertEqual(doc["verdict"], "Close to break-even: at 2R the target has to come first in 33 % of the "
                                         "replays that hit one side; it did in 36 % (target first 30.6 % vs stop "
                                         "first 54.0 %).")
        self.assertEqual(doc["verdict_notes"], [])
        h = doc["history"]
        self.assertEqual((h["triggered"], h["target"], h["stop"], h["close"], h["avg_R"]), (47, 13, 26, 8, 0.096))

    def test_a_wide_2r_plan_a_clear_lean(self):
        # stop 1345.00, 2R target 1409.20: the target first only 1.8 % vs stop first 15.1 %
        doc = machine(plan(stop=1345.0, target=1409.2, stop_rule="pct"))
        m = doc["mc"]
        self.assertEqual((m["r_at_target"], m["p_target"], m["p_stop"], m["p_neither"], m["avg_R"]),
                         (2.0, 0.0176, 0.1506, 0.8318, 0.155))
        self.assertEqual(doc["verdict"], "Strongly toward the stop: at 2R the target has to come first in 33 % of "
                                         "the replays that hit one side; it did in 10 % (target first 1.8 % vs stop "
                                         "first 15.1 %).")
        self.assertEqual(doc["verdict_notes"], [
            "83.2 % of the replays hit neither by 3:05 PM; the price at 3:05 PM decides those.",
            "Average R is +0.155 all the same: the replays that hit neither pull it the other way."])
        text = "\n".join(tm.report(doc))
        self.assertIn(doc["verdict"] + "\n" + doc["verdict_notes"][0] + "\n" + doc["verdict_notes"][1], text)

    def test_the_opening_range_low_stop_leans_toward_the_target(self):
        doc = machine(plan(stop=1351.30, target=1381.50, stop_rule=None))
        self.assertEqual(doc["verdict"], "Leans toward the target: target first 42.7 % vs stop first 29.7 %.")
        self.assertEqual(doc["history"]["rule"]["stop_rule"], "or_low")


class Network(unittest.TestCase):
    def setUp(self):
        self.calls, self.sleeps = [], []
        self._get, self._sleep = tm._get, tm._sleep
        tm._sleep = self.sleeps.append

    def tearDown(self):
        tm._get, tm._sleep = self._get, self._sleep

    def answer(self, codes):
        codes = list(codes)

        def fake(url, timeout=20):
            self.calls.append(url.split(".finance")[0].split("//")[1])
            code = codes.pop(0)
            if code != 200:
                raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
            return TEXT
        tm._get = fake

    def test_url_and_user_agent(self):
        self.assertEqual(tm.chart_url("demo"),
                         "https://query2.finance.yahoo.com/v8/finance/chart/DEMO.NS?range=60d&interval=5m")
        self.assertIn("/chart/M%26M.NS?", tm.chart_url("m&m", "query1"))
        self.assertIn("/chart/BAJAJ-AUTO.NS?", tm.chart_url("BAJAJ-AUTO"))
        self.assertEqual(tm.UA, "Mozilla/5.0 (compatible; day-trader-skill-ind/1.0; "
                                "+https://github.com/saitech-systems/day-trader-skill-ind)")

    def test_429_waits_retries_then_falls_back_to_query1(self):
        self.answer([429, 429, 200])
        self.assertEqual(tm.fetch_chart("DEMO"), TEXT)
        self.assertEqual(self.calls, ["query2", "query2", "query1"])
        self.assertEqual(self.sleeps, [tm.BACKOFF, tm.PAUSE])

    def test_one_429_then_ok(self):
        self.answer([429, 200])
        self.assertEqual(tm.fetch_chart("DEMO"), TEXT)
        self.assertEqual(self.calls, ["query2", "query2"])

    def test_5xx_falls_back(self):
        self.answer([503, 200])
        self.assertEqual(tm.fetch_chart("DEMO"), TEXT)
        self.assertEqual(self.calls, ["query2", "query1"])

    def test_rate_limited_everywhere_is_one_plain_line(self):
        self.answer([429, 429, 429, 429])
        with self.assertRaises(tm.TMError) as cm:
            tm.fetch_chart("DEMO")
        self.assertIn("rate limiting", str(cm.exception))

    def test_404(self):
        self.answer([404])
        with self.assertRaises(tm.TMError) as cm:
            tm.fetch_chart("ZZZZ")
        self.assertIn("ZZZZ.NS", str(cm.exception))
        self.assertIn("check the NSE symbol", str(cm.exception))

    def test_run_over_the_network_path(self):
        self.answer([200])
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "plan.json")
            with open(p, "w") as fh:
                json.dump(PLAN, fh)
            code, text, _ = run(["run", "--plan", p, "--now", NOW, "--replays", "300"])
            self.assertEqual(code, 0)
            self.assertEqual(self.calls, ["query2"])
        finally:
            shutil.rmtree(d)


class KiteSource(unittest.TestCase):
    """The user's own Kite Connect, read only, through FakeKite: the same synthetic DEMO bars as
    Kite candles. The numbers must be the pinned Yahoo numbers, bit for bit."""

    TOKEN = "demo_access_token"

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.mkdir("trader")
        with open(os.path.join("trader", "plan.json"), "w") as fh:
            json.dump(PLAN, fh)
        self.env = {k: os.environ.pop(k) for k in ("KITE_API_KEY", "KITE_ACCESS_TOKEN") if k in os.environ}
        self.saved = (tm.kite_urlopen, tm._get, tm._sleep)
        self.fake = FakeKite().install(tm)
        self.yahoo = []

        def yahoo(url, timeout=20):
            self.yahoo.append(url)
            return TEXT
        tm._get = yahoo
        tm._sleep = lambda s: None

    def tearDown(self):
        tm.kite_urlopen, tm._get, tm._sleep = self.saved
        os.environ.update(self.env)
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def login(self, days_ago=0):
        when = tm.to_ist(tm.utc_now()) - datetime.timedelta(days=days_ago, minutes=1)
        FakeKite.session_file(self.d, login_ist=when)

    def test_kite_candles_give_the_pinned_numbers(self):
        self.login()
        code, text, err = run(["run", "--now", NOW, "--write"])
        self.assertEqual(code, 0, err)
        with open(os.path.join("trader", "timemachine.json")) as fh:
            doc = json.load(fh)
        m, h = doc["mc"], doc["history"]
        self.assertEqual(doc["source"], "kite")
        self.assertEqual((m["pool_sessions"], m["pool_from"], m["pool_to"], m["bars_left"]),
                         (59, "2026-07-24", "2026-10-16", 61))
        self.assertEqual((m["p_target"], m["p_stop"], m["p_neither"], m["p_green"]), (0.5359, 0.4505, 0.0136, 0.5432))
        self.assertEqual((m["median_close"], m["p5_close"], m["p95_close"], m["avg_R"]), (1369.73, 1343.24, 1397.6, 0.086))
        self.assertEqual((sha(m["bands"]), sha(m["sample_paths"]), m["hist"]), (SIM_BANDS, SIM_PATHS, SIM_HIST))
        slim = [{"date": t["date"], "entry": t["entry"], "outcome": t["outcome"], "R": t["R"]} for t in h["trades"]]
        self.assertEqual((h["triggered"], h["target"], h["stop"], sha(slim)), (47, 23, 21, SIM_TRADES))
        self.assertEqual(doc["verdict"], "Close to a coin flip: target first 53.6 % vs stop first 45.1 %.")
        self.assertIn("Bars: Kite, live.", text)
        self.assertNotIn("Note:", text)
        self.assertEqual(self.yahoo, [])
        self.assertEqual(self.fake.methods(), {"GET"})
        self.assertEqual(self.fake.paths(), ["/instruments/NSE", "/instruments/historical/9000001/5minute"])
        url = self.fake.requests[1][1]
        self.assertIn("from=2026-07-24+09%3A00%3A00&to=2026-10-19+16%3A00%3A00", url)   # the same 60 sessions

    def test_sixty_sessions_back_on_the_nse_calendar(self):
        D = datetime.date
        self.assertEqual(tm.first_session(D(2026, 10, 19)), D(2026, 7, 24))
        self.assertEqual(tm.first_session(D(2026, 10, 19), 1), D(2026, 10, 19))
        self.assertEqual(tm.first_session(D(2026, 10, 21), 2), D(2026, 10, 19))     # Tue 20th is Dussehra
        self.assertEqual(tm.first_session(D(2026, 10, 19), 3), D(2026, 10, 15))     # over the weekend

    def test_an_old_session_falls_back_to_yahoo_and_says_so(self):
        self.login(days_ago=2)
        code, text, err = run(["run", "--now", NOW, "--replays", "300"])
        self.assertEqual(code, 0, err)
        self.assertTrue(text.startswith("Note: no Kite session today, so free Yahoo data"), text[:200])
        self.assertIn("Bars: Yahoo, free, can lag.", text)
        self.assertEqual((len(self.yahoo), self.fake.requests), (1, []))
        code, text, err = run(["run", "--now", NOW, "--replays", "300", "--json"])
        doc = json.loads(text)
        self.assertEqual(doc["source"], "yahoo")
        self.assertIn("no Kite session today", doc["source_note"])

    def test_the_config_can_say_yahoo(self):
        self.login()
        with open(os.path.join("trader", "config.json"), "w") as fh:
            json.dump({"data_source": "yahoo"}, fh)
        code, text, _ = run(["run", "--now", NOW, "--replays", "300"])
        self.assertIn("Bars: Yahoo, free, can lag.", text)
        self.assertEqual((len(self.yahoo), self.fake.requests), (1, []))

    def test_offline_wins_and_says_so(self):
        self.login()
        code, text, _ = run(["run", "--now", NOW, "--replays", "300", "--offline", DEMO])
        self.assertIn("Bars: a saved file.", text)
        self.assertEqual((self.yahoo, self.fake.requests), ([], []))

    def test_source_kite_without_a_session_is_refused(self):
        code, text, err = run(["run", "--now", NOW, "--source", "kite"])
        self.assertEqual(code, 1)
        self.assertEqual(err.strip(), "timemachine.py: no Kite session for today: log in first "
                                      "with /trader-broker (kite.py login), or use --source yahoo")

    def test_a_refused_session_is_one_plain_line(self):
        self.login()
        self.fake.fail["/instruments/historical"] = (403, os.path.join(KDIR, "error-token-synthetic.json"))
        code, text, err = run(["run", "--now", NOW])
        self.assertEqual(code, 1)
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertIn("timemachine.py: Kite refused the session (TokenException", err)
        self.assertNotIn(self.TOKEN, text + err)

    def test_the_token_never_shows(self):
        self.login()
        code, text, err = run(["run", "--now", NOW, "--replays", "300", "--write"])
        self.assertNotIn(self.TOKEN, text + err)
        with open(os.path.join("trader", "timemachine.json")) as fh:
            self.assertNotIn(self.TOKEN, fh.read())
        code, text, err = run(["chart", "--now", NOW, "--replays", "300"])
        self.assertEqual(code, 0, err)
        self.assertNotIn(self.TOKEN, text + err)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.plan = os.path.join(self.d, "trader", "plan.json")
        os.makedirs(os.path.dirname(self.plan))
        with open(self.plan, "w") as fh:
            json.dump(PLAN, fh, indent=2)
        self.out = os.path.join(self.d, "trader", "timemachine.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_run_with_the_plan_end_to_end(self):
        code, text, err = run(["run", "--plan", self.plan, "--offline", DEMO, "--now", NOW, "--write", self.out])
        self.assertEqual(code, 0, err)
        self.assertIn("target first   53.6 %", text)
        self.assertIn("Close to a coin flip: target first 53.6 % vs stop first 45.1 %.", text)
        self.assertIn("Wrote %s." % self.out, text)
        self.assertIn("/trader-budget", text)
        with open(self.out) as fh:
            doc = json.load(fh)
        self.assertEqual(sorted(k for k in ("plan", "mc", "history") if k in doc), ["history", "mc", "plan"])
        self.assertEqual(doc["created_by"], "trader-timemachine")
        self.assertEqual(doc["source"], "offline")
        self.assertEqual((doc["mc"]["p_target"], doc["mc"]["avg_R"]), (0.5359, 0.086))
        self.assertEqual(sha(doc["mc"]["sample_paths"]), SIM_PATHS)
        self.assertEqual((doc["history"]["triggered"], doc["history"]["avg_R"]), (47, 0.025))
        self.assertEqual(doc["plan"]["from"], self.plan)

    def test_json_output(self):
        code, text, _ = run(["run", "--plan", self.plan, "--offline", DEMO, "--now", NOW, "--json"])
        self.assertEqual(code, 0)
        doc = json.loads(text)
        self.assertEqual(doc["mc"]["p_stop"], 0.4505)
        self.assertNotIn("sample_paths", doc["mc"])
        self.assertEqual(doc["verdict"][:20], "Close to a coin flip")
        self.assertFalse(os.path.exists(self.out))

    def test_the_plan_as_flags_matches_the_file_with_until(self):
        # flags carry no flat_by, so --until 15:05 gives the same run as the plan file
        code, text, err = run(["run", "--symbol", "demo", "--date", "2026-10-19", "--entry", "1366.40",
                               "--stop", "1357.50", "--target", "1375.30", "--entry-time", "10:00 AM",
                               "--until", "15:05", "--offline", DEMO, "--now", NOW, "--json"])
        self.assertEqual(code, 0, err)
        doc = json.loads(text)
        self.assertEqual((doc["mc"]["p_target"], doc["mc"]["p_green"]), (0.5359, 0.5432))
        self.assertEqual(doc["plan"]["stop_rule"], "or_mid")
        self.assertEqual((doc["history"]["target"], doc["history"]["stop"], doc["history"]["avg_R"]), (23, 21, 0.025))
        code, text, err = run(["run", "--symbol", "DEMO", "--date", "2026-10-19", "--entry", "1366.40",
                               "--stop", "1357.50", "--target", "1375.30", "--entry-time", "10:00 AM",
                               "--offline", DEMO, "--now", NOW, "--json"])
        self.assertEqual(json.loads(text)["mc"]["until"], "15:30")
        code, _, err = run(["run", "--symbol", "DEMO", "--entry", "1366.40", "--offline", DEMO])
        self.assertEqual(code, 1)
        self.assertIn("--stop", err)

    def test_offline_folder_and_default_paths(self):
        shutil.copy(DEMO, os.path.join(self.d, "DEMO-60d5m.json.gz"))
        cwd = os.getcwd()
        os.chdir(self.d)
        try:
            code, text, err = run(["run", "--offline", ".", "--now", NOW, "--replays", "500", "--write"])
            self.assertEqual(code, 0, err)
            self.assertTrue(os.path.exists(os.path.join("trader", "timemachine.json")))
            code, text, err = run(["chart", "--from"])
            self.assertEqual(code, 0, err)
            self.assertIn("* median", text)
            with open(os.path.join("trader", "timemachine.json")) as fh:
                self.assertEqual(json.load(fh)["mc"]["replays"], 500)
        finally:
            os.chdir(cwd)

    def test_errors_are_one_plain_line(self):
        for argv in (["run", "--plan", os.path.join(self.d, "nope.json")],
                     ["run", "--plan", self.plan, "--offline", os.path.join(self.d, "missing.json.gz")],
                     ["run", "--plan", self.plan, "--offline", DEMO, "--until", "9:00"],
                     ["chart", "--from", os.path.join(self.d, "nope.json")]):
            code, text, err = run(argv)
            self.assertEqual(code, 1, argv)
            self.assertEqual(len(err.strip().splitlines()), 1, err)
            self.assertTrue(err.startswith("timemachine.py: "), err)
            self.assertNotIn("Traceback", err)
        self.assertEqual(run([])[0], 1)

    def test_chart(self):
        run(["run", "--plan", self.plan, "--offline", DEMO, "--now", NOW, "--write", self.out])
        code, text, _ = run(["chart", "--from", self.out])
        self.assertEqual(code, 0)
        lines = text.splitlines()
        self.assertIn("DEMO 2026-10-19, 2,000 replay paths from the 10:00 AM entry (seed 19)", lines[0])
        self.assertTrue(any(l.startswith("  1375.30 |") and l.endswith("  target") for l in lines))
        self.assertTrue(any(l.startswith("  1357.50 |") and l.endswith("  stop") for l in lines))
        self.assertTrue(any(l.startswith("  1366.40 |") and l.endswith("  entry") for l in lines))
        self.assertIn("10:00", text)
        self.assertIn("3:05", text)
        self.assertEqual(lines[-1], "Close to a coin flip: target first 53.6 % vs stop first 45.1 %.")
        code, again, _ = run(["chart", "--plan", self.plan, "--offline", DEMO, "--now", NOW])
        self.assertEqual(again, text)


if __name__ == "__main__":
    unittest.main()
