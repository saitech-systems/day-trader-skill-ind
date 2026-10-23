"""trader-exit tests. Bars for the ATR stop are the SYNTHETIC DEMO session in
tests/fixtures/shared/ (a made-up NSE stock, not market data; see test_entry.py
and tests/fixtures/make_synthetic.py)."""

import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal

from _load import load, FIX, ROOT
from _kite import FakeKite, KDIR

exitp = load("trader-exit", "exit")
entry = load("trader-entry", "entry")

DEMO = os.path.join(FIX, "shared", "DEMO-2026-10-19-5m-synthetic.json")
DEMO60 = os.path.join(FIX, "shared", "DEMO-60d5m-synthetic.json.gz")

# trader/plan.json for the synthetic story, exactly as the contract writes it
STORY_PLAN = {"created_by": "trader-exit", "symbol": "DEMO", "exchange": "NSE", "side": "buy", "date": "2026-10-19",
              "setup": "orb", "entry": 1366.4, "entry_time": "10:00 AM", "entry_bar": 8, "interval": "5m",
              "opening_range": {"high": 1363.8, "low": 1351.3, "minutes": 15},
              "stop": 1357.5, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
              "target": 1375.3, "target_r": 1.0, "target_label": "1R (one times the risk)",
              "risk_per_share": 8.9, "reward_risk": 1.0, "tick": 0.1, "flat_by": "15:05"}
STORY_ENTRY = {"created_by": "trader-entry", "symbol": "DEMO", "exchange": "NSE", "setup": "orb", "side": "buy",
               "date": "2026-10-19", "status": "triggered", "time": "10:00 AM", "bar_start": "09:55", "bar_index": 8,
               "price": 1366.4, "level": 1363.8, "interval": "5m",
               "opening_range": {"high": 1363.8, "low": 1351.3, "minutes": 15},
               "source": "yahoo", "checked_at": "2026-10-19T04:30:00Z"}


def run(mod, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = mod.main(argv)
    return code, out.getvalue(), err.getvalue()


def c(entry_px, stop, target="2R", oh=1363.80, ol=1351.30, atr=None, n=None):
    sr, sp = exitp.parse_stop(stop)
    tr, tp = exitp.parse_target(target)
    return exitp.compute(entry_px, sr, sp, tr, tp, oh, ol, atr, n)


class Math(unittest.TestCase):
    def test_the_story_or_mid_1r(self):
        p = c(1366.40, "or-mid", "1R")
        self.assertEqual(p["tick"], Decimal("0.10"))
        self.assertEqual(p["stop"], Decimal("1357.50"))          # floor(1357.55) to the 0.10 tick
        self.assertEqual(p["risk"], Decimal("8.90"))
        self.assertEqual(p["target"], Decimal("1375.30"))
        self.assertEqual(p["reward_risk"], Decimal("1.00"))
        self.assertEqual(p["target_label"], "1R (one times the risk)")
        self.assertIn("rounded down to the 0.10 tick", p["stop_detail"])

    def test_the_default_2r(self):
        p = c(1366.40, "or-mid")
        self.assertEqual(p["target"], Decimal("1384.20"))
        self.assertEqual(p["target_label"], "2R (two times the risk)")

    def test_raw_yahoo_floats_land_on_the_tick_grid(self):
        p = c(1366.4000244140625, "or-mid", "1R", 1363.800048828125, 1351.300048828125)
        self.assertEqual((p["entry"], p["stop"], p["target"]),
                         (Decimal("1366.40"), Decimal("1357.50"), Decimal("1375.30")))

    def test_other_stops(self):
        p = c(1366.40, "or-low")
        self.assertEqual((p["stop"], p["risk"], p["target"]), (Decimal("1351.30"), Decimal("15.10"), Decimal("1396.60")))
        p = c(1366.40, "price:1357.58")
        self.assertEqual(p["stop"], Decimal("1357.50"))             # rounds away from the entry, to the tick
        p = c(1366.40, "pct:1")
        self.assertEqual((p["stop"], p["risk"], p["target"]), (Decimal("1352.70"), Decimal("13.70"), Decimal("1393.80")))
        p = c(1366.40, "atr:1.5", atr=5.1, n=9)
        self.assertEqual(p["stop"], Decimal("1358.70"))             # 1366.40 - 7.65 = 1358.75
        self.assertEqual(p["stop_label"], "1.5 x ATR(9) under the entry")

    def test_ticks_by_price_band(self):
        self.assertEqual([str(exitp.tick_size(x)) for x in (30, 249.99, 250, 999.95, 1000, 5000, 9999, 10000, 25000)],
                         ["0.01", "0.01", "0.05", "0.05", "0.10", "0.50", "0.50", "1.00", "5.00"])
        p = c(260.00, "price:257.13", oh=None, ol=None)
        self.assertEqual((p["stop"], p["tick"]), (Decimal("257.10"), Decimal("0.05")))
        p = c(25000, "pct:1", oh=None, ol=None)
        self.assertEqual((p["stop"], p["target"], p["tick"]), (Decimal("24750.00"), Decimal("25500.00"), Decimal("5.00")))

    def test_target_rounds_half_up_and_price_targets(self):
        p = c(100, "price:99.01", "1.5R", None, None)
        self.assertEqual(p["target"], Decimal("101.49"))            # 100 + 1.485, tick 0.01
        self.assertEqual(p["target_label"], "1.5R (1.5 times the risk)")
        p = c(1366.40, "price:1361.10", "1.5R")
        self.assertEqual(p["target"], Decimal("1374.40"))           # 1366.40 + 7.95 = 1374.35, half up to 0.10
        p = c(1366.40, "or-mid", "price:1378.23")
        self.assertEqual(p["target"], Decimal("1378.20"))
        self.assertEqual(p["reward_risk"], Decimal("1.33"))
        self.assertEqual(p["target_label"], "your price (1.33R)")
        self.assertEqual(p["target_r"], Decimal("1.33"))

    def test_refusals_say_why(self):
        cases = [("price:1366.40", "2R", "at or above the entry"),
                 ("price:1367", "2R", "at or above the entry"),
                 ("or-mid", "price:1366", "at or below the entry"),
                 ("price:1366.00", "2R", "too tight to survive the spread"),
                 ("pct:0.04", "2R", "too tight to survive the spread")]
        for stop, target, why in cases:
            with self.assertRaises(exitp.PlanError) as cm:
                c(1366.40, stop, target)
            self.assertIn(why, str(cm.exception), stop)
        # 0.05% of 1366.40 is 0.6832: a 0.70 risk is allowed, 0.60 is not
        self.assertEqual(c(1366.40, "price:1365.70")["risk"], Decimal("0.70"))
        with self.assertRaises(exitp.PlanError):
            c(1366.40, "price:1365.80")
        # under Rs 40 two ticks is the floor: 0.02 on a 30.00 stock, never one tick
        self.assertEqual(c(30.00, "price:29.98", oh=None, ol=None)["risk"], Decimal("0.02"))
        with self.assertRaises(exitp.PlanError) as cm:
            c(30.00, "price:29.99", oh=None, ol=None)
        self.assertIn("two ticks", str(cm.exception))
        with self.assertRaises(exitp.PlanError):
            c(1355.00, "or-mid")                                    # the midpoint is above this entry
        for bad in ("mid-ish", "pct:100", "price:0", "atr:-1"):
            with self.assertRaises(exitp.PlanError):
                exitp.parse_stop(bad)
        for bad in ("0R", "R", "lots", "price:x"):
            with self.assertRaises(exitp.PlanError):
                exitp.parse_target(bad)
        self.assertEqual(exitp.parse_stop("price:Rs 1,357.50"), ("price", Decimal("1357.50")))
        with self.assertRaises(exitp.PlanError):
            c(1366.40, "or-mid", oh=None, ol=None)
        with self.assertRaises(exitp.PlanError):
            c(1366.40, "or-mid", oh=1351.30, ol=1363.80)

    def test_atr_on_the_session_bars(self):
        ch = exitp.load_chart("DEMO", "5m", None, DEMO)
        day = exitp.datetime.date(2026, 10, 19)
        bars = exitp.session_bars(ch, day, "5m", upto_slot=8)
        self.assertEqual(len(bars), 9)                              # 9:15 ... the 9:55 entry bar
        # by hand: the true range of each bar, the first one without the overnight gap
        trs, prev = [], None
        for b in bars:
            r = b["h"] - b["l"]
            if prev is not None:
                r = max(r, abs(b["h"] - prev), abs(b["l"] - prev))
            trs.append(r)
            prev = b["c"]
        self.assertAlmostEqual(trs[0], 1362.0 - 1351.3, places=4)
        atr, n = exitp.atr_of(bars)
        self.assertEqual(n, 9)
        self.assertAlmostEqual(atr, sum(trs) / 9, places=9)
        self.assertAlmostEqual(atr, 5.1, places=6)
        # later in the day it uses the last 14 bars; the file's in-progress 10:45 bar and tick never count
        all_bars = exitp.session_bars(ch, day, "5m")
        self.assertEqual(len(all_bars), 18)
        self.assertEqual(exitp.atr_of(all_bars)[1], 14)
        # the 60-day file holds the same Monday
        ch60 = exitp.load_chart("DEMO", "5m", None, DEMO60)
        self.assertEqual([b["t"] for b in exitp.session_bars(ch60, day, "5m", upto_slot=8)], [b["t"] for b in bars])
        with self.assertRaises(exitp.PlanError):
            exitp.load_chart("RELIANCE", "5m", None, DEMO)          # the file holds DEMO.NS
        with self.assertRaises(exitp.PlanError):
            exitp.load_chart("RELIANCE.BO", "5m", None, DEMO)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.mkdir("trader")

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def write(self, path, doc):
        with open(path, "w") as fh:
            json.dump(doc, fh)

    def plan(self, *args):
        return run(exitp, ["plan", "--now", "2026-10-19 10:01"] + list(args))

    def read_plan(self):
        with open(os.path.join("trader", "plan.json")) as fh:
            return json.load(fh)

    def test_entry_to_plan_end_to_end(self):
        code, _, _ = run(entry, ["check", "DEMO", "--offline", DEMO, "--now", "2026-10-19 10:00", "--write"])
        self.assertEqual(code, 0)
        code, text, err = self.plan("--stop", "or-mid", "--target", "1R", "--write")
        self.assertEqual(code, 0, err)
        p = self.read_plan()
        for k, v in STORY_PLAN.items():
            self.assertEqual(p[k], v, k)
        self.assertIn("Stop          1357.50  opening-range midpoint", text)
        self.assertIn("Target        1375.30  1R (one times the risk)", text)
        self.assertIn("Risk a share  Rs 8.90 (0.65% of the entry)", text)
        self.assertIn("Reward:risk   1.0", text)
        self.assertIn("Flat by       15:05 IST", text)
        self.assertIn("not a guarantee", text)
        self.assertIn("/trader-timemachine", text)
        self.assertNotIn("Note:", text)
        self.assertNotIn("pro" + "fit", text.lower())   # never on screen
        code, text, _ = self.plan("--write")                       # the defaults: or-mid, 2R
        self.assertEqual(self.read_plan()["target"], 1384.2)
        self.assertEqual(self.read_plan()["target_r"], 2.0)
        code, text, _ = run(exitp, ["show", "--now", "2026-10-19 10:20"])
        self.assertEqual(code, 0)
        self.assertIn("1384.20", text)
        self.assertNotIn("not today", text)
        code, text, _ = run(exitp, ["show", "--now", "2026-10-21 10:20"])
        self.assertIn("not today", text)

    def test_manual_flags_and_json(self):
        code, text, _ = self.plan("--symbol", "demo", "--entry", "1366.40", "--or-high", "1363.80", "--or-low",
                                  "1351.30", "--time", "10:00", "--entry-bar", "8", "--date", "2026-10-19",
                                  "--target", "1R", "--json")
        self.assertEqual(code, 0)
        p = json.loads(text)
        for k, v in STORY_PLAN.items():
            self.assertEqual(p[k], v, k)
        self.assertEqual(p["warnings"], [])
        code, _, err = self.plan("--symbol", "RELIANCE.BO", "--entry", "1400", "--stop", "pct:1")
        self.assertEqual(code, 1)
        self.assertIn("BSE listing", err)

    def test_atr_stop_reads_bars(self):
        self.write(os.path.join("trader", "entry.json"), STORY_ENTRY)
        code, text, err = self.plan("--stop", "atr:1.5", "--offline", DEMO, "--json")
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        self.assertEqual((p["stop"], p["risk_per_share"], p["target"]), (1358.7, 7.7, 1381.8))
        self.assertEqual((p["stop_rule"], p["atr_bars"]), ("atr", 9))
        self.assertAlmostEqual(p["atr"], 5.1, places=4)
        code, _, err = self.plan("--stop", "atr:1.5", "--offline", DEMO, "--entry-bar", "1")
        self.assertEqual(code, 1)
        self.assertIn("at least 3 closed", err)

    def test_refusals_from_the_cli(self):
        self.write(os.path.join("trader", "entry.json"), dict(STORY_ENTRY, status="waiting", time=None, price=None,
                                                              bar_index=None, bar_start=None))
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("DEMO is waiting, not triggered", err)
        code, text, _ = self.plan("--entry", "1363.90", "--json")    # plan ahead at the level
        self.assertEqual(code, 0)
        p = json.loads(text)
        self.assertEqual((p["entry"], p["stop"], p["entry_time"]), (1363.9, 1357.5, None))

        self.write(os.path.join("trader", "entry.json"), dict(STORY_ENTRY, setup="level", level=1362.0))
        code, _, err = self.plan()
        self.assertIn("level setup has no opening-range stop", err)
        self.assertEqual(self.plan("--stop", "pct:1")[0], 0)

        code, _, err = self.plan("--side", "sell")
        self.assertIn("longs only", err)
        self.write(os.path.join("trader", "entry.json"), dict(STORY_ENTRY, side="sell"))
        code, _, err = self.plan()
        self.assertIn("longs only", err)
        code, _, err = self.plan("--stop", "price:1366.50")
        self.assertIn("longs only", err)
        os.remove(os.path.join("trader", "entry.json"))
        code, _, err = self.plan()
        self.assertIn("no entry", err)
        code, _, err = self.plan("--symbol", "DEMO")
        self.assertIn("needs --entry", err)
        code, _, err = self.plan("--symbol", "DEMO", "--from", "x.json", "--entry", "1")
        self.assertIn("not both", err)
        code, _, err = self.plan("--symbol", "DEMO", "--entry", "1366.40", "--or-high", "1363.80")
        self.assertIn("both --or-high and --or-low", err)
        self.assertNotIn("Traceback", err)

    def test_flat_by_config_closing_auction_and_late_entries(self):
        self.write(os.path.join("trader", "entry.json"), STORY_ENTRY)
        self.write(os.path.join("trader", "config.json"), {"flat_by": "14:30", "min_reward_risk": 1.5})
        code, text, _ = self.plan("--target", "1R", "--json")
        p = json.loads(text)
        self.assertEqual(p["flat_by"], "14:30")
        self.assertIn("under your min_reward_risk 1.5", p["warnings"][0])

        # while the closing auction starts at 15:15 for F&O stocks (2026-08-03 to 2026-12-06)
        self.write(os.path.join("trader", "config.json"), {"flat_by": "15:15"})
        code, text, _ = self.plan("--json")
        self.assertEqual(code, 0)
        self.assertIn("closing auction", json.loads(text)["warnings"][0])
        code, text, _ = self.plan()
        self.assertIn("Note: Flat-by 15:15", text)
        # after it, Zerodha's 15:25 MIS square-off
        code, text, _ = run(exitp, ["plan", "--now", "2026-12-08 10:01", "--symbol", "DEMO", "--entry", "1366.40",
                                    "--or-high", "1363.80", "--or-low", "1351.30", "--flat-by", "15:25", "--json"])
        self.assertIn("MIS auto square-off", json.loads(text)["warnings"][0])
        self.write(os.path.join("trader", "config.json"), {"flat_by": "15:30"})
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("flat_by 15:30 in the config is at or after the 3:30 PM close", err)
        os.remove(os.path.join("trader", "config.json"))

        code, _, err = self.plan("--flat-by", "15:45")
        self.assertIn("after the 3:30 PM close", err)
        self.write(os.path.join("trader", "entry.json"), dict(STORY_ENTRY, time="3:10 PM"))
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("no time left to hold it", err)

    def test_special_sessions_and_closed_days(self):
        args = ["--symbol", "DEMO", "--entry", "1366.40", "--or-high", "1363.80", "--or-low", "1351.30", "--json"]
        code, text, err = run(exitp, ["plan", "--now", "2026-11-08 14:41", "--date", "2026-11-08",
                                      "--time", "2:40 PM"] + args)
        self.assertEqual(code, 0, err)                              # Muhurat trading, a Sunday session
        p = json.loads(text)
        self.assertEqual((p["date"], p["flat_by"], p["warnings"]), ("2026-11-08", "15:05", []))
        code, text, _ = run(exitp, ["plan", "--now", "2026-10-17 10:00", "--date", "2026-10-17"] + args)
        self.assertIn("The NSE is closed on 2026-10-17 (weekend)", json.loads(text)["warnings"][0])
        code, text, _ = run(exitp, ["plan", "--now", "2026-10-20 10:00", "--date", "2026-10-20"] + args)
        self.assertIn("NSE holiday: Dussehra", json.loads(text)["warnings"][0])
        code, text, _ = run(exitp, ["plan", "--now", "2027-03-01 10:00", "--date", "2027-03-01"] + args)
        self.assertIn("check nseindia.com", json.loads(text)["warnings"][0])

    def test_show_without_a_plan(self):
        code, text, _ = run(exitp, ["show"])
        self.assertEqual(code, 1)
        self.assertIn("No plan", text)


class KiteSource(unittest.TestCase):
    """The user's own Kite Connect, read only, through FakeKite (the synthetic DEMO bars)."""

    TOKEN = "demo_access_token"

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.mkdir("trader")
        with open(os.path.join("trader", "entry.json"), "w") as fh:
            json.dump(STORY_ENTRY, fh)
        self.env = {k: os.environ.pop(k) for k in ("KITE_API_KEY", "KITE_ACCESS_TOKEN") if k in os.environ}
        self.saved = (exitp.kite_urlopen, exitp.yahoo_get, exitp.KITE_PAUSE)
        self.fake = FakeKite().install(exitp)
        self.yahoo = []

        def yahoo(path):
            self.yahoo.append(path)
            with open(DEMO) as fh:
                return fh.read()
        exitp.yahoo_get = yahoo
        exitp.KITE_PAUSE = 0

    def tearDown(self):
        exitp.kite_urlopen, exitp.yahoo_get, exitp.KITE_PAUSE = self.saved
        os.environ.update(self.env)
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def login(self, days_ago=0):
        when = exitp.to_ist(exitp.utc_now()) - datetime.timedelta(days=days_ago, minutes=1)
        FakeKite.session_file(self.d, login_ist=when)

    def plan(self, *args):
        return run(exitp, ["plan", "--now", "2026-10-19 10:00"] + list(args))

    def instruments(self, tick):
        """Kite's instrument list with DEMO on another tick (NSE assigns a tick per stock)."""
        path = os.path.join(self.d, "instruments.csv")
        with open(os.path.join(KDIR, "instruments-NSE-synthetic.csv")) as fh:
            rows = fh.read().splitlines()
        rows = [r.replace(",0.1,1,EQ,", ",%s,1,EQ," % tick) if ",DEMO," in r else r for r in rows]
        with open(path, "w") as fh:
            fh.write("\n".join(rows) + "\n")
        return path

    def test_an_atr_stop_on_kite_bars_matches_yahoo(self):
        code, text, err = self.plan("--stop", "atr:1.5", "--offline", DEMO, "--json")
        offline = json.loads(text)
        self.login()
        code, text, err = self.plan("--stop", "atr:1.5", "--json")
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        for k in ("stop", "target", "risk_per_share", "atr", "atr_bars", "tick"):
            self.assertEqual(p[k], offline[k], k)
        self.assertEqual((p["stop"], p["risk_per_share"], p["target"]), (1358.7, 7.7, 1381.8))
        self.assertEqual((p["tick_source"], p["bars_source"]), ("kite", "kite"))
        self.assertEqual((offline["tick_source"], offline["bars_source"]), ("price band", "offline"))
        self.assertEqual(self.fake.methods(), {"GET"})
        self.assertEqual(self.fake.paths(), ["/instruments/NSE", "/instruments/historical/9000001/5minute"])
        self.assertEqual(self.yahoo, [])

    def test_kites_own_tick_for_the_stock(self):
        self.login()
        self.fake.instruments = self.instruments("0.05")
        code, text, err = self.plan("--target", "1R", "--json")
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        # (1363.80 + 1351.30) / 2 = 1357.55 sits on a 0.05 grid: no rounding down to 1357.50
        self.assertEqual((p["tick"], p["tick_source"], p["stop"], p["target"]), (0.05, "kite", 1357.55, 1375.25))
        self.assertNotIn("bars_source", p)
        self.assertEqual(self.fake.paths(), ["/instruments/NSE"])
        code, text, _ = self.plan("--target", "1R")
        self.assertIn("(1363.80 + 1351.30) / 2 = 1357.55", text)
        self.assertEqual(self.fake.paths(), ["/instruments/NSE"])     # cached for the day

    def test_without_a_session_the_price_band_tick_and_no_kite_call(self):
        code, text, err = self.plan("--target", "1R", "--json")
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        self.assertEqual((p["tick"], p["tick_source"], p["stop"]), (0.1, "price band", 1357.5))
        self.assertEqual(p["warnings"], [])          # no bars were read, so no data note
        self.assertEqual((self.fake.requests, self.yahoo), ([], []))

    def test_an_atr_stop_without_a_session_says_yahoo(self):
        self.login(days_ago=2)
        code, text, err = self.plan("--stop", "atr:1.5")
        self.assertEqual(code, 0, err)
        self.assertIn("Note: No Kite session today, so free Yahoo data", text)
        self.assertEqual((len(self.yahoo), self.fake.requests), (1, []))

    def test_source_kite_without_a_session_is_refused(self):
        code, text, err = self.plan("--source", "kite")
        self.assertEqual(code, 1)
        self.assertEqual(err.strip(), "exit.py: no Kite session for today: log in first "
                                      "(python3 trader-broker/kite.py login), or use --source yahoo")

    def test_a_refused_session(self):
        self.login()
        self.fake.fail["/instruments"] = (403, os.path.join(KDIR, "error-token-synthetic.json"))
        code, text, err = self.plan("--stop", "atr:1.5")       # bars are needed: one plain line
        self.assertEqual(code, 1)
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertIn("exit.py: Kite refused the session (TokenException", err)
        code, text, err = self.plan("--json")                  # only the tick: the price band, with a note
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        self.assertEqual((p["tick_source"], p["stop"]), ("price band", 1357.5))
        self.assertTrue(any("Kite's instrument list did not load" in w for w in p["warnings"]))
        for t in (text, err):
            self.assertNotIn(self.TOKEN, t)

    def test_the_token_never_shows(self):
        self.login()
        for args in (("--stop", "atr:1.5", "--write"), ("--json",)):
            code, text, err = self.plan(*args)
            self.assertNotIn(self.TOKEN, text + err)
        with open(os.path.join("trader", "plan.json")) as fh:
            self.assertNotIn(self.TOKEN, fh.read())


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-exit", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-entry", "SKILL.md"), encoding="utf-8") as fh:
            sibling = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-exit\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertEqual(text[text.index("## Safety"):], sibling[sibling.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("before the entry", "a stop is a plan, not a guarantee", "day trading loses most people money",
                     "/trader-timemachine", "/trader-budget", "longs only", "1357.50", "1375.30", "rs 8.90", "1384.20",
                     "too tight to survive the spread", "tick", "15:05", "closing auction", "square-off", "synthetic",
                     "sebi-registered"):
            self.assertIn(must, low)
        for never in ("\u2014", "tick " + "by tick", "real " + "time", "pro" + "fit", "nyse", "new york", "alpaca",
                      "tsla"):
            self.assertNotIn(never, low)
        self.assertNotRegex(text, r"\$\d")


if __name__ == "__main__":
    unittest.main()
