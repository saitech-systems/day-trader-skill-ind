"""trader-alerts tests. Fixtures in tests/fixtures/trader-alerts/, made by its make_synthetic.py:
SYNTHETIC Yahoo chart JSON quotes (not market data) for made-up NSE stocks DEMO, SAMPLE and
TESTCO, Yahoo's 404 answer for an unknown symbol (ZZZZ), and plan-demo.json, the shared DEMO
story's plan (entry 1366.40, stop 1357.50, target 1375.30, flat by 15:05 on 2026-10-19).
"""

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

from _load import load, ROOT, FIX

alerts = load("trader-alerts", "alerts")

QDIR = os.path.join(FIX, "trader-alerts")
PLAN = os.path.join(QDIR, "plan-demo.json")
DT = datetime.datetime
UA = "Mozilla/5.0 (compatible; day-trader-skill-ind/1.0; +https://github.com/saitech-systems/day-trader-skill-ind)"

# NSE equity-segment holidays on weekdays, 2026 (NSE holiday list, read 2026-10-23)
NSE_2026 = ["2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31", "2026-04-03", "2026-04-14",
            "2026-05-01", "2026-05-28", "2026-06-26", "2026-09-14", "2026-10-02", "2026-10-20", "2026-11-10",
            "2026-11-24", "2026-12-25"]
SPECIAL = {"2026-02-01": ("09:15", "15:30"), "2026-11-08": ("14:20", "15:30")}


def ist(text):
    return alerts.parse_now(text)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = alerts.main(argv)
    return code, out.getvalue(), err.getvalue()


def plan_rules():
    with open(PLAN) as fh:
        return alerts.rules_from_plan(json.load(fh))


class Calendar(unittest.TestCase):
    def test_holidays_match_the_published_list(self):
        self.assertEqual(sorted(alerts.NSE_HOLIDAYS), NSE_2026)
        self.assertEqual({k: v[:2] for k, v in alerts.NSE_SPECIAL_SESSIONS.items()}, SPECIAL)
        for d in alerts.NSE_HOLIDAYS:
            self.assertLess(datetime.date.fromisoformat(d).weekday(), 5, d)
        for d in alerts.NSE_SPECIAL_SESSIONS:
            self.assertEqual(datetime.date.fromisoformat(d).weekday(), 6, d)   # both are Sundays
        self.assertEqual(alerts.CALENDAR_YEARS, (2026,))

    def test_india_time_has_no_daylight_saving(self):
        for utc in (DT(2026, 1, 5, 3, 45), DT(2026, 3, 9, 3, 45), DT(2026, 7, 1, 3, 45), DT(2026, 11, 2, 3, 45)):
            self.assertEqual(alerts.to_ist(utc).strftime("%H:%M"), "09:15")
        self.assertEqual(alerts.ist_to_utc(DT(2026, 10, 19, 15, 5)), DT(2026, 10, 19, 9, 35))

    def test_parse_now(self):
        self.assertEqual(ist("2026-10-13T09:15"), DT(2026, 10, 13, 3, 45))
        self.assertEqual(ist("2026-10-19 10:15"), DT(2026, 10, 19, 4, 45))
        self.assertEqual(ist("2026-10-19 10:15 IST"), DT(2026, 10, 19, 4, 45))
        self.assertEqual(ist("2026-12-01T09:15"), DT(2026, 12, 1, 3, 45))
        self.assertEqual(ist("2026-10-13T20:00:01Z"), DT(2026, 10, 13, 20, 0, 1))
        self.assertEqual(ist("2026-10-13T09:15+05:30"), DT(2026, 10, 13, 3, 45))
        with self.assertRaises(alerts.AlertError):
            ist("tomorrow")

    def test_market_status(self):
        st = alerts.market_status(ist("2026-10-13T10:00"))
        self.assertTrue(st["open"])
        self.assertEqual((st["reason"], st["opens"], st["closes"]), ("regular session", "09:15", "15:30"))
        before = alerts.market_status(ist("2026-10-13T09:14"))
        self.assertFalse(before["open"])
        self.assertEqual((before["reason"], before["next_open"]), ("before the open", "2026-10-13 09:15"))
        after = alerts.market_status(ist("2026-10-13T15:30"))
        self.assertFalse(after["open"])
        self.assertEqual((after["reason"], after["next_open"]), ("after the close", "2026-10-14 09:15"))
        self.assertTrue(alerts.market_status(ist("2026-10-13T15:29"))["open"])
        dussehra = alerts.market_status(ist("2026-10-20T11:00"))
        self.assertFalse(dussehra["open"])
        self.assertEqual(dussehra["reason"], "NSE holiday: Dussehra")
        self.assertEqual(dussehra["next_open"], "2026-10-21 09:15")
        self.assertEqual(alerts.market_status(ist("2026-10-16T16:00"))["next_open"], "2026-10-19 09:15")
        self.assertEqual(alerts.market_status(ist("2026-10-17T10:00"))["reason"], "weekend")
        xmas = alerts.market_status(ist("2026-12-25T10:00"))
        self.assertFalse(xmas["open"])
        self.assertEqual(xmas["next_open"], "2026-12-28 09:15")
        self.assertFalse(alerts.market_status(ist("2027-01-04T10:00"))["calendar_known"])
        self.assertTrue(alerts.market_status(ist("2026-12-31T10:00"))["calendar_known"])

    def test_diwali_week_and_muhurat_trading(self):
        fri = alerts.market_status(ist("2026-11-06T16:00"))
        self.assertEqual((fri["next_open"], fri["next_open_session"]),
                         ("2026-11-08 14:20", "Muhurat trading, Diwali Laxmi Pujan (Sunday)"))
        self.assertFalse(alerts.market_status(ist("2026-11-08T14:19"))["open"])
        muhurat = alerts.market_status(ist("2026-11-08T14:20"))
        self.assertTrue(muhurat["open"])
        self.assertEqual((muhurat["opens"], muhurat["closes"]), ("14:20", "15:30"))
        self.assertEqual(muhurat["reason"], "special session: Muhurat trading, Diwali Laxmi Pujan (Sunday)")
        self.assertNotIn("auction", muhurat)
        self.assertEqual(alerts.market_status(ist("2026-11-08T15:30"))["next_open"], "2026-11-09 09:15")
        # Tuesday 2026-11-10 is Diwali Balipratipada
        self.assertEqual(alerts.market_status(ist("2026-11-09T15:45"))["next_open"], "2026-11-11 09:15")
        self.assertTrue(alerts.market_status(ist("2026-02-01T10:00"))["open"])     # Budget-day Sunday

    def test_the_closing_auction_note(self):
        self.assertIn("15:15", alerts.market_status(ist("2026-10-19T10:00"))["auction"])
        self.assertIn("until 2026-12-06", alerts.market_status(ist("2026-12-04T10:00"))["auction"])
        self.assertIn("15:30 to 15:40", alerts.market_status(ist("2026-12-08T10:00"))["auction"])
        self.assertNotIn("auction", alerts.market_status(ist("2026-07-01T10:00")))


class Quotes(unittest.TestCase):
    def test_yahoo_fixtures(self):
        with open(os.path.join(QDIR, "yahoo-DEMO.json")) as fh:
            q = alerts.parse_yahoo(fh.read(), "DEMO")
        self.assertEqual((q["symbol"], q["price"], q["prev_close"], q["open"]), ("DEMO", 1369.85, 1352.4, 1355.0))
        self.assertEqual((q["time"], q["currency"]), ("2026-10-19T05:19:45Z", "INR"))
        with open(os.path.join(QDIR, "yahoo-SAMPLE.json")) as fh:
            q = alerts.parse_yahoo(fh.read(), "SAMPLE")
        self.assertEqual((q["price"], q["prev_close"], q["time"]), (245.17, 248.0, "2026-10-16T09:59:58Z"))

    def test_urls_and_user_agent(self):
        self.assertEqual(alerts.UA, UA)
        self.assertEqual(alerts.yahoo_url("demo"),
                         "https://query2.finance.yahoo.com/v8/finance/chart/DEMO.NS?range=1d&interval=1d")
        self.assertIn("/chart/M%26M.NS?", alerts.yahoo_url("m&m"))
        self.assertIn("/chart/BAJAJ-AUTO.NS?", alerts.yahoo_url("BAJAJ-AUTO.NS"))
        self.assertIn("//query1.finance.yahoo.com/", alerts.yahoo_url("DEMO", alerts.YAHOO_HOSTS[1]))

    def test_offline_quotes_and_errors(self):
        q, errors = alerts.get_quotes(["DEMO", "sample.ns", "ZZZZ", "NOPE", "DEMO.BO"], QDIR)
        self.assertEqual(sorted(q), ["DEMO", "SAMPLE"])
        self.assertEqual(q["SAMPLE"]["price"], 245.17)
        self.assertEqual(len(errors), 3)
        self.assertIn("No data found", errors[0])
        self.assertIn("no offline quote file", errors[1])
        self.assertIn("BSE listing", errors[2])

    def test_offline_gz(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(QDIR, "yahoo-DEMO.json"), "rb") as src:
                with gzip.open(os.path.join(d, "yahoo-DEMO.json.gz"), "wb") as dst:
                    dst.write(src.read())
            q, errors = alerts.get_quotes(["DEMO"], d)
            self.assertEqual((q["DEMO"]["price"], errors), (1369.85, []))
        finally:
            shutil.rmtree(d)

    def test_rupees_in_sentences(self):
        self.assertEqual(alerts.rs(1369.85), "Rs 1,369.85")
        self.assertEqual(alerts.rs(245.0), "Rs 245.00")
        self.assertEqual(alerts.inr(123456.5), "Rs 1,23,456.50")
        self.assertEqual(alerts.inr(3215), "Rs 3,215")


class FakeNet(object):
    """Stands in for urllib: answers each request with the next status code."""

    def __init__(self, codes, body=None):
        self.codes, self.calls, self.sleeps = list(codes), [], []
        with open(os.path.join(QDIR, "yahoo-DEMO.json")) as fh:
            self.body = body or fh.read()

    def urlopen(self, req, timeout=None):
        self.calls.append((req.full_url.split("/")[2], req.get_header("User-agent")))
        code = self.codes.pop(0)
        if code != 200:
            fp = io.BytesIO(b'{"chart":{"result":null,"error":{"code":"Not Found",'
                            b'"description":"No data found, symbol may be delisted"}}}' if code == 404 else b"")
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, fp)
        return io.BytesIO(self.body.encode("utf-8"))


class Network(unittest.TestCase):
    def setUp(self):
        self.saved = (alerts._urlopen, alerts._sleep)

    def tearDown(self):
        alerts._urlopen, alerts._sleep = self.saved

    def net(self, codes):
        f = FakeNet(codes)
        alerts._urlopen = f.urlopen
        alerts._sleep = f.sleeps.append
        return f

    def hosts(self, f):
        return [h.split(".")[0] for h, _ in f.calls]

    def test_query2_first_with_the_contract_user_agent(self):
        f = self.net([200])
        q, errors = alerts.get_quotes(["DEMO"])
        self.assertEqual((q["DEMO"]["price"], errors), (1369.85, []))
        self.assertEqual(f.calls, [("query2.finance.yahoo.com", UA)])

    def test_one_retry_with_a_pause_on_429(self):
        f = self.net([429, 200])
        self.assertEqual(alerts.get_quotes(["DEMO"])[0]["DEMO"]["price"], 1369.85)
        self.assertEqual(self.hosts(f), ["query2", "query2"])
        self.assertEqual(f.sleeps, [alerts.BACKOFF])

    def test_falls_back_to_query1_on_429_and_5xx(self):
        f = self.net([429, 429, 200])
        self.assertIn("DEMO", alerts.get_quotes(["DEMO"])[0])
        self.assertEqual(self.hosts(f), ["query2", "query2", "query1"])
        f = self.net([503, 200])
        self.assertIn("DEMO", alerts.get_quotes(["DEMO"])[0])
        self.assertEqual(self.hosts(f), ["query2", "query1"])
        f = self.net([500, 502])
        q, errors = alerts.get_quotes(["DEMO"])
        self.assertEqual(q, {})
        self.assertIn("query1.finance.yahoo.com/v8/finance/chart/DEMO.NS answered HTTP 502", errors[0])

    def test_a_404_is_yahoos_answer_and_a_403_is_not_retried(self):
        f = self.net([404])
        q, errors = alerts.get_quotes(["NOPE"])
        self.assertIn("No data found", errors[0])
        self.assertEqual(self.hosts(f), ["query2"])
        f = self.net([403])
        q, errors = alerts.get_quotes(["DEMO"])
        self.assertIn("HTTP 403", errors[0])
        self.assertEqual(self.hosts(f), ["query2"])

    def test_a_pause_between_symbols_and_none_for_a_bad_one(self):
        f = self.net([200, 200])
        q, errors = alerts.get_quotes(["DEMO", "X.BO", "SAMPLE"])
        self.assertEqual(f.sleeps, [alerts.PAUSE])
        self.assertEqual(len(f.calls), 2)
        self.assertIn("BSE listing", errors[0])


class Rules(unittest.TestCase):
    def setUp(self):
        self.q, _ = alerts.get_quotes(["DEMO", "SAMPLE", "TESTCO"], QDIR)
        self.open = alerts.market_status(ist("2026-10-16T10:00"))
        self.now = ist("2026-10-16T10:00")

    def at(self, text, rules, state=None, quotes=None):
        now = ist(text)
        return alerts.evaluate(rules, self.q if quotes is None else quotes, alerts.market_status(now),
                               state or {}, now)

    def test_a_target_rings_once(self):
        rules = [{"id": "sample-245", "symbol": "SAMPLE", "type": "above", "price": 245}]
        rings, st = alerts.evaluate(rules, self.q, self.open, {}, self.now)
        self.assertEqual(len(rings), 1)
        self.assertIn("SAMPLE is Rs 245.17: at or above your alert level Rs 245.", rings[0]["message"])
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(rings, [])

    def test_stop_and_rearm(self):
        rules = [{"id": "testco-stop", "symbol": "TESTCO", "type": "stop", "price": 3215, "rearm": True}]
        rings, st = alerts.evaluate(rules, self.q, self.open, {}, self.now)
        self.assertIn("TESTCO is Rs 3,210.60: at or below your stop Rs 3,215.", rings[0]["message"])
        self.q["TESTCO"]["price"] = 3220.0
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(rings, [])
        self.assertNotIn("testco-stop", st["rung"])
        self.q["TESTCO"]["price"] = 3200.0
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(len(rings), 1)

    def test_pct_move_from_previous_close(self):
        big = [{"id": "sample-3", "symbol": "SAMPLE", "type": "pct_move", "pct": 3}]
        self.assertEqual(alerts.evaluate(big, self.q, self.open, {}, self.now)[0], [])
        small = [{"id": "sample-1", "symbol": "SAMPLE", "type": "pct_move", "pct": 1, "direction": "down"}]
        rings, st = alerts.evaluate(small, self.q, self.open, {}, self.now)
        self.assertIn("SAMPLE moved -1.14% to Rs 245.17 (from Rs 248.00)", rings[0]["message"])
        self.assertEqual(alerts.evaluate(small, self.q, self.open, st, self.now)[0], [])
        up = [{"id": "sample-up", "symbol": "SAMPLE", "type": "pct_move", "pct": 1, "direction": "up"}]
        self.assertEqual(alerts.evaluate(up, self.q, self.open, {}, self.now)[0], [])
        demo = [{"id": "demo-1", "symbol": "DEMO", "type": "pct_move", "pct": 1}]
        rings, _ = self.at("2026-10-19 10:52", demo)
        self.assertIn("DEMO moved +1.29% to Rs 1,369.85 (from Rs 1,352.40)", rings[0]["message"])

    def test_opening_bell_once_per_trading_day(self):
        rules = [{"id": "bell", "type": "opening_bell"}]
        closed = alerts.market_status(ist("2026-10-16T08:00"))
        self.assertEqual(alerts.evaluate(rules, {}, closed, {}, ist("2026-10-16T08:00"))[0], [])
        rings, st = alerts.evaluate(rules, {}, self.open, {}, self.now)
        self.assertEqual(rings[0]["message"], "Opening bell: the NSE is open (2026-10-16 10:00 IST).")
        self.assertEqual(alerts.evaluate(rules, {}, self.open, st, ist("2026-10-16T11:00"))[0], [])
        nxt = alerts.market_status(ist("2026-10-19T09:16"))
        self.assertEqual(len(alerts.evaluate(rules, {}, nxt, st, ist("2026-10-19T09:16"))[0]), 1)

    def test_the_bell_rings_for_muhurat_trading(self):
        rules = [{"id": "bell", "type": "opening_bell"}]
        self.assertEqual(self.at("2026-11-08 14:10", rules)[0], [])
        rings, _ = self.at("2026-11-08 14:21", rules)
        self.assertEqual(rings[0]["message"], "Opening bell: the NSE is open (2026-11-08 14:21 IST), Muhurat "
                                              "trading, Diwali Laxmi Pujan (Sunday), 14:20 to 15:30.")

    def test_the_plan_gives_a_stop_a_target_and_flat_by(self):
        rules = plan_rules()
        self.assertEqual([(r["id"], r["type"]) for r in rules],
                         [("demo-plan-stop", "stop"), ("demo-plan-target", "target"), ("demo-flat-by", "time")])
        self.assertEqual((rules[0]["price"], rules[1]["price"]), (1357.5, 1375.3))
        self.assertEqual((rules[2]["at"], rules[2]["label"]), ("15:05", "flat by close"))
        self.assertNotIn("before_close", rules[2])
        self.assertTrue(all(r["date"] == "2026-10-19" for r in rules))
        rings, st = self.at("2026-10-19 10:52", rules)          # the 10:49:45 quote, 1369.85
        self.assertEqual(rings, [])
        q = json.loads(json.dumps(self.q))
        q["DEMO"]["price"] = 1357.10
        rings, st = self.at("2026-10-19 11:30", rules, st, q)
        self.assertEqual([r["id"] for r in rings], ["demo-plan-stop"])
        self.assertIn("DEMO is Rs 1,357.10: at or below your stop Rs 1,357.50 (plan stop: opening-range midpoint)",
                      rings[0]["message"])
        q["DEMO"]["price"] = 1375.30
        self.assertEqual([r["id"] for r in self.at("2026-10-19 12:00", rules, st, q)[0]], ["demo-plan-target"])

    def test_a_plan_without_flat_by_uses_the_config_then_15_05(self):
        with open(PLAN) as fh:
            plan = json.load(fh)
        del plan["flat_by"]
        self.assertEqual(alerts.rules_from_plan(plan)[2]["at"], "15:05")
        self.assertEqual(alerts.rules_from_plan(plan, "14:55")[2]["at"], "14:55")

    def test_flat_by_rings_once_per_trading_day(self):
        rules = plan_rules()[2:]
        self.assertEqual(self.at("2026-10-19 15:04", rules)[0], [])
        rings, st = self.at("2026-10-19 15:05", rules)
        self.assertEqual(len(rings), 1)
        msg = rings[0]["message"]
        self.assertIn("Flat by close: it is 15:05 IST (set for 15:05), the market closes at 15:30.", msg)
        self.assertIn("trader-broker flatten --symbol DEMO", msg)
        self.assertIn("Zerodha squares off open MIS positions itself", msg)
        self.assertEqual(self.at("2026-10-19 15:20", rules, st)[0], [])
        rings, _ = self.at("2026-10-19 15:40", rules)
        self.assertIn("the market is closed", rings[0]["message"])
        self.assertEqual(self.at("2026-10-21 15:06", rules, st)[0], [])     # plan rules: their day only

    def test_a_time_alert_follows_the_nse_calendar(self):
        rule = [{"id": "flat", "type": "time", "at": "15:05", "label": "flat by close"}]
        self.assertEqual(self.at("2026-10-17 15:06", rule)[0], [])         # Saturday: no session
        self.assertEqual(self.at("2026-10-20 15:06", rule)[0], [])         # Dussehra
        rings, st = self.at("2026-11-08 15:06", rule)                      # Muhurat trading, a Sunday
        self.assertIn("the market closes at 15:30", rings[0]["message"])
        self.assertEqual(st["rung"]["flat"], "2026-11-08T09:36:00Z")       # 15:06 IST, no daylight saving
        rings, st = self.at("2026-12-01 15:05", rule)
        self.assertEqual(st["rung"]["flat"], "2026-12-01T09:35:00Z")

    def test_the_example_rules_are_valid(self):
        with open(os.path.join(ROOT, "trader-alerts", "alerts.example.json")) as fh:
            rules = json.load(fh)["rules"]
        for r in rules:
            alerts.validate(r)
        self.assertEqual([r for r in rules if r["id"].startswith("demo-plan") or r["id"] == "demo-flat-by"],
                         plan_rules())

    def test_bad_rules(self):
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "moon"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "above", "symbol": "DEMO"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "time", "at": "3pm"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError) as cm:
            alerts.evaluate([{"id": "x", "type": "above", "symbol": "DEMO.BO", "price": 1}], {}, self.open, {},
                            self.now)
        self.assertIn("BSE listing", str(cm.exception))
        with self.assertRaises(alerts.AlertError):
            alerts.rules_from_plan({"symbol": "DEMO", "stop": 1357.5})


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.rules = os.path.join(self.d, "alerts.json")
        self.state = os.path.join(self.d, "state", "alerts.json")
        self.prices = os.path.join(self.d, "prices.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def check(self, now):
        return run(["check", "--rules", self.rules, "--state", self.state, "--prices-out", self.prices,
                    "--offline", QDIR, "--now", now])

    def test_the_story_trade_from_the_plan(self):
        code, text, _ = run(["add", "--from-plan", "--plan", PLAN, "--rules", self.rules, "--state", self.state,
                             "--config", os.path.join(self.d, "none")])
        self.assertEqual(code, 0)
        self.assertIn("Added demo-flat-by", text)
        self.assertIn("the target is watched here only", text)
        self.assertEqual(run(["add", "--rules", self.rules, "--opening-bell"])[0], 0)

        code, text, _ = self.check("2026-10-19 10:52")
        self.assertEqual(code, 0)                                      # the opening bell rings first
        code, text, _ = self.check("2026-10-19 10:53")
        self.assertEqual(code, 1)
        self.assertIn("NSE OPEN (2026-10-19 10:53 IST, regular session).", text)
        self.assertIn("Nothing hit. 4 rule(s) watched today, DEMO Rs 1,369.85.", text)
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["prices"], {"DEMO": 1369.85})
        self.assertEqual(doc["detail"]["DEMO"]["time"], "2026-10-19T05:19:45Z")
        self.assertEqual(doc["detail"]["DEMO"]["fetched"], "2026-10-19T05:23:00Z")

        code, text, _ = self.check("2026-10-19 15:05")
        self.assertEqual(code, 0)
        self.assertIn("ALERT Flat by close", text)
        self.assertIn("not an order", text)
        self.assertEqual(self.check("2026-10-19 15:07")[0], 1)
        code, text, _ = self.check("2026-10-20 10:00")                  # Dussehra: no bell
        self.assertEqual(code, 1)
        self.assertIn("NSE closed (2026-10-20 10:00 IST, NSE holiday: Dussehra).", text)
        code, text, _ = self.check("2026-10-21 09:16")
        self.assertIn("ALERT Opening bell", text)
        code, text, _ = self.check("2026-10-21 09:20")
        self.assertIn("Nothing hit. 1 rule(s) watched today.", text)

        # a new plan replaces the plan rules and re-arms them
        code, text, _ = run(["add", "--from-plan", "--plan", PLAN, "--rules", self.rules, "--state", self.state])
        with open(self.rules) as fh:
            ids = [r["id"] for r in json.load(fh)["rules"]]
        self.assertEqual(sorted(ids), ["demo-flat-by", "demo-plan-stop", "demo-plan-target", "opening-bell"])
        with open(self.state) as fh:
            self.assertNotIn("demo-flat-by", json.load(fh)["rung"])

    def test_a_watchlist_evening_and_morning(self):
        for args in (["--opening-bell"], ["--symbol", "TESTCO", "--above", "3250"],
                     ["--symbol", "sample", "--stop", "240"]):
            code, _, _ = run(["add", "--rules", self.rules] + args)
            self.assertEqual(code, 0)
        code, _, err = run(["add", "--rules", self.rules, "--symbol", "TESTCO", "--above", "3250"])
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)
        code, _, err = run(["add", "--rules", self.rules, "--symbol", "TESTCO.BO", "--above", "3250"])
        self.assertEqual(code, 1)
        self.assertIn("BSE listing", err)

        code, text, _ = self.check("2026-10-16T20:00")
        self.assertEqual(code, 1)
        self.assertIn("Nothing hit", text)
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["prices"], {"SAMPLE": 245.17, "TESTCO": 3210.6})

        code, text, _ = self.check("2026-10-19T09:16")
        self.assertEqual(code, 0)
        self.assertIn("ALERT Opening bell", text)
        code, text, _ = self.check("2026-10-19T09:45")
        self.assertEqual(code, 1)

        code, text, _ = run(["list", "--rules", self.rules, "--state", self.state])
        self.assertIn("rang", text)
        self.assertEqual(run(["reset", "opening-bell", "--state", self.state])[0], 0)
        self.assertEqual(run(["reset", "opening-bell", "--state", self.state])[0], 1)

    def test_add_time_and_target(self):
        code, text, _ = run(["add", "--rules", self.rules, "--at", "15:05", "--label", "flat by close",
                             "--symbol", "demo"])
        self.assertEqual(code, 0)
        code, text, _ = run(["add", "--rules", self.rules, "--symbol", "DEMO", "--target", "1375.30",
                             "--date", "2026-10-19"])
        self.assertEqual(code, 0)
        with open(self.rules) as fh:
            rules = json.load(fh)["rules"]
        self.assertEqual(rules[0], {"id": "time-1505", "type": "time", "at": "15:05", "label": "flat by close",
                                    "symbol": "DEMO"})
        self.assertEqual(rules[1], {"id": "demo-target-1375.30", "symbol": "DEMO", "type": "target",
                                    "price": 1375.3, "date": "2026-10-19"})
        self.assertEqual(run(["add", "--rules", self.rules, "--at", "3pm"])[0], 1)
        code, _, err = run(["add", "--rules", self.rules, "--from-plan", "--plan", os.path.join(self.d, "none")])
        self.assertEqual(code, 1)
        self.assertIn("no plan", err)

    def test_quote_write_merges(self):
        code, text, _ = run(["quote", "DEMO", "SAMPLE", "--offline", QDIR, "--write", self.prices,
                             "--now", "2026-10-19T05:20:00Z"])
        self.assertEqual(code, 0)
        self.assertIn("DEMO       Rs 1,369.85 (+1.29% vs prev close Rs 1,352.40)", text)
        self.assertIn("delayed", text)
        run(["quote", "TESTCO", "--offline", QDIR, "--write", self.prices])
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(sorted(doc["prices"]), ["DEMO", "SAMPLE", "TESTCO"])
        self.assertEqual(doc["detail"]["SAMPLE"]["prev_close"], 248.0)
        self.assertEqual(doc["detail"]["DEMO"]["fetched"], "2026-10-19T05:20:00Z")

    def test_quote_set_stores_a_kite_price(self):
        code, text, err = run(["quote", "--set", "DEMO=1370.25", "--source", "kite", "--write", self.prices,
                               "--now", "2026-10-19 10:55"])
        self.assertEqual(code, 0, err)
        self.assertIn("DEMO       Rs 1,370.25", text)
        run(["quote", "SAMPLE", "--offline", QDIR, "--write", self.prices, "--now", "2026-10-19 10:56"])
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["prices"], {"DEMO": 1370.25, "SAMPLE": 245.17})
        self.assertEqual(doc["detail"]["DEMO"], {"prev_close": None, "time": "2026-10-19T05:25:00Z",
                                                 "source": "kite", "fetched": "2026-10-19T05:25:00Z"})
        self.assertEqual(doc["detail"]["SAMPLE"]["source"], "yahoo")
        for argv, words in ((["--set", "DEMO=1370", "--source", "yahoo"], "--source kite"),
                            (["--set", "DEMO"], "SYMBOL=PRICE"),
                            (["--set", "DEMO=-3"], "above 0"),
                            (["--set", "DEMO.BO=10"], "BSE listing"),
                            (["DEMO", "--source", "kite"], "get_ltp")):
            code, _, err = run(["quote", "--write", self.prices] + argv)
            self.assertEqual(code, 1, argv)
            self.assertIn(words, err, argv)

    def test_quote_set_defaults_to_trader_prices(self):
        cwd = os.getcwd()
        os.chdir(self.d)
        try:
            code, text, err = run(["quote", "--set", "M&M=3120.5", "--now", "2026-10-19 11:00"])
            self.assertEqual(code, 0, err)
            with open(os.path.join("trader", "prices.json")) as fh:
                doc = json.load(fh)
            self.assertEqual((doc["prices"], doc["detail"]["M&M"]["source"]), ({"M&M": 3120.5}, "manual"))
        finally:
            os.chdir(cwd)

    def test_quote_holdings_from_portfolio_and_plan(self):
        pf = os.path.join(self.d, "portfolio.json")
        with open(pf, "w") as fh:
            json.dump({"cash": 0, "positions": [{"ticker": "SAMPLE", "shares": 1}]}, fh)
        code, text, _ = run(["quote", "--holdings", "--portfolio", pf, "--plan", PLAN, "--offline", QDIR, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(json.loads(text)["quotes"]), ["DEMO", "SAMPLE"])

    def test_clock_exit_codes_and_words(self):
        code, text, _ = run(["clock", "--now", "2026-10-19 10:15"])
        self.assertEqual(code, 0)
        self.assertIn("India 2026-10-19 10:15 IST (Monday): NSE OPEN, regular session.", text)
        self.assertIn("Today's session: 09:15 to 15:30.", text)
        self.assertIn("Closing auction: F&O stocks stop continuous trading at 15:15", text)
        code, text, _ = run(["clock", "--now", "2026-12-25T10:00"])
        self.assertEqual(code, 1)
        self.assertIn("Christmas", text)
        self.assertIn("Next open: 2026-12-28 09:15 IST.", text)
        code, text, _ = run(["clock", "--now", "2026-11-06 16:00"])
        self.assertIn("Next open: 2026-11-08 14:20 IST (Muhurat trading, Diwali Laxmi Pujan (Sunday)).", text)
        code, text, _ = run(["clock", "--now", "2027-01-04 10:00"])
        self.assertIn("check nseindia.com", text)
        for word in ("New York", "NYSE", "$"):
            self.assertNotIn(word, text)

    def test_check_without_rules(self):
        code, _, err = run(["check", "--rules", self.rules, "--offline", QDIR])
        self.assertEqual(code, 1)
        self.assertIn("no rules", err)


if __name__ == "__main__":
    unittest.main()
