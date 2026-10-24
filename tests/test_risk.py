"""trader-risk tests. Fixtures in tests/fixtures/trader-risk/:

orders-demo.json    what trader-budget writes for the SYNTHETIC DEMO story (a made-up NSE stock,
                    see tests/fixtures/make_synthetic.py): BUY 54 DEMO LIMIT 1366.40 MIS, its
                    SL stop-loss sell (trigger 1357.50, limit 1354.10) and the 1375.30 target.
orders-split.json   a long-term split of trader-budget/targets.example.json at MADE-UP prices:
                    five CNC LIMIT buys with no stop (test data, not quotes).
"""

import datetime
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import ROUND_FLOOR

from _load import load, ROOT, FIX

check = load("trader-risk", "check")
ticket = load("trader-broker", "ticket")

CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
DEMO_PATH = os.path.join(FIX, "trader-risk", "orders-demo.json")
SPLIT_PATH = os.path.join(FIX, "trader-risk", "orders-split.json")
NOW = "2026-10-19 10:00"
TODAY = "2026-10-19"
SPLIT_DAY = "2026-10-13"
PRICES = {"RELIANCE": 1412.35, "HDFCBANK": 987.6, "INFY": 1503.2, "TCS": 3120.0, "ITC": 412.55}  # made up
QUOTE_TIME = datetime.datetime(2026, 10, 19, 5, 19, 45)   # DEMO 1369.85 at 10:49:45 IST (synthetic)
# a long-term split's rules with every day-trade check switched off: the long-term checks still hold
SPLIT = {"budget": 100000, "cash_buffer_pct": 10, "max_position_pct": 25, "max_order_inr": 50000,
         "min_order_inr": 100, "max_orders_per_day": 10, "require_stop": False, "no_entries_before": None,
         "no_entries_after": None, "max_trades_per_day": 0, "max_daily_loss_inr": 0, "max_loss_per_trade_inr": 0,
         "min_reward_risk": 0}


def at(text):
    return check.parse_now(text)


def cfg(**over):
    with open(CFG_PATH) as fh:
        c = json.load(fh)
    c.update(over)
    return c


def split(**over):
    c = dict(SPLIT)
    c.update(over)
    return c


def demo(**over):
    with open(DEMO_PATH) as fh:
        o = json.load(fh)["orders"][0]
    o.update(over)
    return o


def stop_of(o, **over):
    s = dict(o["stop_order"])
    s.update(over)
    return s


def entry(sym, qty, price, stop, target):
    """A day-trade entry the way trader-budget writes one."""
    leg = {"variety": "regular", "exchange": "NSE", "tradingsymbol": sym, "transaction_type": "SELL",
           "quantity": qty, "product": "MIS", "order_type": "SL", "trigger_price": stop,
           "price": float(check.to_tick(stop * 0.9975, ROUND_FLOOR)), "validity": "DAY"}
    return {"variety": "regular", "exchange": "NSE", "tradingsymbol": sym, "transaction_type": "BUY",
            "quantity": qty, "product": "MIS", "order_type": "LIMIT", "price": price, "validity": "DAY",
            "stop_order": leg, "target_price": target, "plan": {"entry": price, "stop": stop, "target": target}}


def kite(sym, qty, price=None, side="BUY", product="CNC", order_type="LIMIT", **over):
    o = {"variety": "regular", "exchange": "NSE", "tradingsymbol": sym, "transaction_type": side,
         "quantity": qty, "product": product, "order_type": order_type, "validity": "DAY"}
    if price is not None:
        o["price"] = price
    o.update(over)
    return o


def split_orders():
    with open(SPLIT_PATH) as fh:
        return json.load(fh)["orders"]


def placed(sym, side="buy", day=TODAY, max_loss=553.67, kind=None, status="placed", leg=None):
    r = {"symbol": sym, "side": side, "status": status, "date": day, "at": day + "T10:05:00+05:30"}
    if max_loss is not None:
        r["max_loss"] = max_loss
    if kind:
        r["kind"] = kind
    if leg:
        r["leg"] = leg
    return r


def verdicts(res):
    return [r["verdict"] for r in res["results"]]


def reasons(res, i=0):
    return " | ".join(res["results"][i]["reasons"])


def warnings(res, i=0):
    return " | ".join(res["results"][i]["warnings"])


class TheStory(unittest.TestCase):
    def test_the_demo_order_passes_every_check(self):
        res = check.check([demo()], cfg(), now=at(NOW))
        r = res["results"][0]
        self.assertEqual(r["verdict"], "PASS", r["reasons"])
        self.assertEqual(r["reasons"], [])
        self.assertEqual(r["warnings"], [])
        self.assertEqual((r["kind"], r["entry_ref"], r["entry_ref_from"], r["stop"], r["target"]),
                         ("entry", 1366.4, "limit", 1357.5, 1375.3))
        self.assertEqual((r["risk_per_share"], r["charges"], r["max_loss"], r["reward_risk"]),
                         (8.9, 73.07, 553.67, 1.0))
        self.assertEqual(r["value_inr"], 73785.6)
        self.assertEqual(r["amount"], "54 shares")
        self.assertEqual(res["cash_after"], 26214.4)
        self.assertEqual(res["date"], TODAY)
        self.assertIs(res["paper"], True)

    def test_a_tighter_max_loss_blocks_it_on_the_loss_only(self):
        res = check.check([demo()], cfg(max_loss_per_trade_inr=500), now=at(NOW))
        self.assertEqual(verdicts(res), ["BLOCK"])
        self.assertEqual(res["results"][0]["reasons"],
                         ["loss: 54 shares x 8.90 risk a share = Rs 480.60 + about Rs 73.07 charges = Rs 553.67 at "
                          "the stop, over your Rs 500 max loss per trade"])

    def test_fingerprint_is_shared_with_the_broker(self):
        o = demo()
        self.assertEqual(check.fingerprint(o), ticket.fingerprint(o))
        self.assertEqual(check.fingerprint(dict(o, why="anything", plan={})), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, quantity=55)), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, stop_order=stop_of(o, trigger_price=1357.0))),
                            check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, target_price=1380.0)), check.fingerprint(o))
        self.assertEqual(check.KITE_FIELDS, ticket.KITE_FIELDS)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "state", "risk.json")
            none = os.path.join(d, "none")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = check.main(["--orders", DEMO_PATH, "--config", CFG_PATH, "--portfolio", none,
                                   "--prices", none, "--state", none, "--now", NOW, "--out", out])
            text = buf.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("PASS  #1 buy DEMO 54 shares, entry", text)
            self.assertIn("entry 1366.40 (the limit price), stop 1357.50, target 1375.30: risk 8.90 a share", text)
            self.assertIn("max loss Rs 553.67 incl. about Rs 73.07 charges (limit Rs 1,000), reward:risk 1.00 (min 1)",
                          text)
            self.assertIn("today before this: 0 of 3 trades, Rs 0 of Rs 3,000 daily loss", text)
            self.assertIn("1 passed, 0 blocked at 2026-10-19 10:00 IST", text)
            self.assertIn("Cash after the passed orders: Rs 26,214.40 (paper account)", text)
            self.assertIn("not financial advice", text)
            with open(out) as fh:
                rec = json.load(fh)
            self.assertEqual(rec["date"], TODAY)
            self.assertEqual(rec["results"][0]["kind"], "entry")
            self.assertEqual(rec["results"][0]["fingerprint"], check.fingerprint(demo()))


class DayTradeChecks(unittest.TestCase):
    def one(self, order, now=NOW, **over):
        return check.check([order], cfg(**over), now=at(now))

    def test_a_stop_is_required(self):
        o = demo()
        for k in ("stop_order", "target_price", "plan"):
            o.pop(k)
        o["plan"] = {"entry": 1366.4}
        res = self.one(o)
        self.assertIn("stop: no stop", reasons(res))
        res = self.one(o, require_stop=False, min_reward_risk=0)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("no stop", warnings(res))

    def test_a_plan_stop_counts_but_warns(self):
        o = demo()
        o.pop("stop_order")
        res = self.one(o)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("in the plan only, not an order at Zerodha", warnings(res))
        self.assertEqual(res["results"][0]["max_loss"], 553.67)

    def test_stop_and_target_on_the_right_side(self):
        o = demo()
        res = self.one(dict(o, stop_order=stop_of(o, trigger_price=1367.0, price=1364.0)))
        self.assertIn("must be below the entry 1366.40", reasons(res))
        res = self.one(demo(target_price=1366.0))
        self.assertIn("target: the target 1366.00 must be above the entry 1366.40", reasons(res))

    def test_reward_risk(self):
        res = self.one(demo(target_price=1372.0))
        self.assertIn("reward:risk: 0.63", reasons(res))
        self.assertEqual(verdicts(self.one(demo(target_price=1372.0), min_reward_risk=0.5)), ["PASS"])

    def test_a_limit_order_uses_its_limit_price(self):
        res = check.check([demo(price=1366.0)], cfg(), prices={"DEMO": 1369.85}, now=at(NOW))
        r = res["results"][0]
        self.assertEqual((r["entry_ref"], r["risk_per_share"], r["max_loss"], r["verdict"]),
                         (1366.0, 8.5, 532.07, "PASS"))

    def test_a_market_buy_uses_a_fresh_higher_price(self):
        o = demo(order_type="MARKET")
        o.pop("price")
        later = at("2026-10-19 10:55")
        res = check.check([o], cfg(), prices={"DEMO": 1369.85}, now=later, price_times={"DEMO": QUOTE_TIME})
        r = res["results"][0]
        self.assertEqual((r["entry_ref"], r["entry_ref_from"], r["risk_per_share"], r["max_loss"]),
                         (1369.85, "price", 12.35, 739.98))
        self.assertIn("reward:risk: 0.44", reasons(res))
        self.assertIn("market protection", warnings(res))

    def test_a_stale_or_future_price_is_not_used(self):
        o = demo(order_type="MARKET")
        o.pop("price")
        for now in ("2026-10-19 11:10", "2026-10-19 10:00"):
            res = check.check([o], cfg(), prices={"DEMO": 1369.85}, now=at(now), price_times={"DEMO": QUOTE_TIME})
            self.assertEqual(verdicts(res), ["PASS"], now)
            self.assertEqual(res["results"][0]["entry_ref"], 1366.4)
            self.assertIn("older than 15 minutes", warnings(res))

    def test_a_lower_price_does_not_shrink_the_risk(self):
        o = demo(order_type="MARKET")
        o.pop("price")
        res = check.check([o], cfg(), prices={"DEMO": 1360.00}, now=at(NOW))
        self.assertEqual(res["results"][0]["entry_ref"], 1366.4)

    def test_daily_loss_and_trades_per_day(self):
        two = {"orders": [placed("INFY", max_loss=1000), placed("TCS", max_loss=1000)]}
        res = check.check([demo()], cfg(), state=two, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("today before this: 2 of 3 trades, Rs 2,000 of Rs 3,000 daily loss", res["results"][0]["info"][-1])
        three = {"orders": [placed(s, max_loss=1000) for s in ("INFY", "TCS", "ITC")]}
        res = check.check([demo()], cfg(), state=three, now=at(NOW))
        why = reasons(res)
        self.assertIn("trades: 3 entries placed today", why)
        self.assertIn("daily loss: Rs 3,000 already at risk today plus Rs 553.67 for this one = Rs 3,553.67, over "
                      "your Rs 3,000", why)
        yesterday = {"orders": [placed(s, day="2026-10-16") for s in ("INFY", "TCS", "ITC")]}
        self.assertEqual(verdicts(check.check([demo()], cfg(), state=yesterday, now=at(NOW))), ["PASS"])

    def test_exits_declines_and_stop_legs_do_not_count_as_trades(self):
        st = {"orders": [placed("INFY"), placed("INFY", side="sell", leg="stop", max_loss=None),
                         placed("INFY", side="sell", kind="exit", max_loss=None),
                         placed("TCS", status="declined"), placed("ITC", status="failed")]}
        res = check.check([demo()], cfg(), state=st, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertEqual((res["today"]["entries_placed"], res["today"]["orders_placed"]), (1, 2))

    def test_the_batch_counts_toward_the_daily_limits(self):
        orders = [entry(s, 1, 1000.0, 100.0, 2000.0) for s in ("AAA", "BBB", "CCC", "DDD")]
        res = check.check(orders, cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS", "PASS", "BLOCK"])
        self.assertEqual(res["results"][0]["max_loss"], 900.48)
        why = reasons(res, 3)
        self.assertIn("trades: 0 entries placed today plus 3 passed earlier", why)
        self.assertIn("daily loss: Rs 2,701.44 already at risk today plus Rs 900.48 for this one = Rs 3,601.92", why)

    def test_entry_window_india_time(self):
        cases = [("2026-10-19 09:20", "no new entries before 09:30 IST (it is 09:20)"),
                 ("2026-10-19 14:50", "no new entries after 14:45 IST (it is 14:50)"),
                 ("2026-10-19 08:00", "the market is closed (Mon 2026-10-19 08:00 IST; the session is 09:15 to 15:30)"),
                 ("2026-10-19 15:35", "the market is closed"),
                 ("2026-10-17 10:15", "the market is closed today (weekend"),
                 ("2026-10-20 10:15", "the market is closed today (NSE holiday: Dussehra"),
                 ("2026-11-08 10:00", "the market is closed (Sun 2026-11-08 10:00 IST; the session is 14:20 to 15:30)"),
                 ("2026-11-08 14:50", "no new entries after 14:45 IST"),
                 ("2026-10-19T03:55:00Z", "no new entries before 09:30 IST (it is 09:25)")]
        for now, want in cases:
            res = self.one(demo(), now=now)
            self.assertIn("window: " + want, reasons(res), now)
        for now in ("2026-10-19 09:30", "2026-10-19 14:45", "2026-10-19T04:00:00Z", "2026-11-08 14:30",
                    "2026-02-01 10:00"):
            self.assertEqual(verdicts(self.one(demo(), now=now)), ["PASS"], now)
        self.assertIn("Muhurat", " ".join(self.one(demo(), now="2026-11-08 14:30")["warnings"]))
        self.assertIn("covers 2026 only", " ".join(self.one(demo(), now="2027-01-04 10:00")["warnings"]))
        res = self.one(demo(), now="2026-10-17 10:15", no_entries_before=None, no_entries_after=None)
        self.assertEqual(verdicts(res), ["PASS"])
        with self.assertRaises(check.RiskError):
            self.one(demo(), no_entries_before="9.30am")

    def test_the_window_uses_india_time_not_the_machine(self):
        self.assertEqual(check.to_ist(at("2026-10-19 10:15")), datetime.datetime(2026, 10, 19, 10, 15))
        self.assertEqual(at("2026-10-19 10:15"), datetime.datetime(2026, 10, 19, 4, 45))
        self.assertEqual(at("2026-12-01 10:15"), datetime.datetime(2026, 12, 1, 4, 45))   # no daylight saving

    def test_a_short_entry_when_allowed(self):
        short = kite("DEMO", 54, 1366.4, side="SELL", product="MIS",
                     stop_order={"variety": "regular", "exchange": "NSE", "tradingsymbol": "DEMO",
                                 "transaction_type": "BUY", "quantity": 54, "product": "MIS", "order_type": "SL",
                                 "trigger_price": 1375.3, "price": 1378.8, "validity": "DAY"},
                     target_price=1357.5, plan={"entry": 1366.4})
        res = self.one(short)
        self.assertIn("short: selling more DEMO than you hold", reasons(res))
        res = self.one(short, allow_short=True)
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["kind"], r["max_loss"], r["reward_risk"]), ("PASS", "entry", 553.84, 1.0))
        res = self.one(dict(short, stop_order=dict(short["stop_order"], trigger_price=1360.0, price=1362.0)),
                       allow_short=True)
        self.assertIn("must be above the entry 1366.40 for a sell", reasons(res))


class KiteFormat(unittest.TestCase):
    def one(self, order, **over):
        return check.check([order], cfg(**over), now=at(NOW))

    def test_numbers_not_strings(self):
        self.assertIn("quantity must be a number (54), not the string '54'", reasons(self.one(demo(quantity="54"))))
        self.assertIn("price must be a number", reasons(self.one(demo(price="1366.40"))))

    def test_whole_shares_variety_type_and_validity(self):
        self.assertIn("whole shares", reasons(self.one(demo(quantity=54.5))))
        self.assertIn("variety 'co'", reasons(self.one(demo(variety="co"))))
        self.assertIn("variety 'amo'", reasons(self.one(demo(variety="amo"))))
        self.assertIn("unknown order_type 'STOP'", reasons(self.one(demo(order_type="STOP"))))
        self.assertIn("a TTL order needs validity_ttl", reasons(self.one(demo(validity="TTL"))))
        self.assertEqual(verdicts(self.one(demo(validity="TTL", validity_ttl=5))), ["PASS"])

    def test_products(self):
        self.assertIn("NRML is for futures and options", reasons(self.one(demo(product="NRML"))))
        self.assertIn("margin: product MTF", reasons(self.one(demo(product="MTF"))))
        res = self.one(demo(product="CNC", stop_order=stop_of(demo(), product="CNC")))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("CNC is delivery", warnings(res))

    def test_prices_on_the_tick(self):
        res = self.one(demo(price=1366.43))
        self.assertIn("price 1366.43 is not on the 0.10 tick for that price; NSE rejects it (1366.40 or 1366.50)",
                      reasons(res))
        o = demo()
        res = self.one(dict(o, stop_order=stop_of(o, price=1354.12)))
        self.assertIn("stop_order: price 1354.12 is not on the 0.10 tick", reasons(res))
        self.assertEqual(verdicts(self.one(entry("PENNY", 10, 212.37, 209.12, 216.0))), ["PASS"])  # 0.01 tick

    def test_order_type_needs(self):
        o = demo()
        o.pop("price")
        self.assertIn("a LIMIT order needs a price", reasons(self.one(o)))
        s = stop_of(demo())
        s.pop("trigger_price")
        self.assertIn("stop_order: an SL order needs a trigger_price", reasons(self.one(demo(stop_order=s))))
        self.assertIn("an SL sell's price 1358.00 must be at or under its trigger 1357.50",
                      reasons(self.one(demo(stop_order=stop_of(demo(), price=1358.0)))))
        res = self.one(demo(stop_order=stop_of(demo(), order_type="SL-M", price=None)))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("market protection", warnings(res))

    def test_the_stop_order_matches_the_entry(self):
        o = demo()
        self.assertIn("more than the 54 this order buys", reasons(self.one(dict(o, stop_order=stop_of(o, quantity=60)))))
        res = self.one(dict(o, stop_order=stop_of(o, quantity=50)))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("covers 50 of the 54 shares", warnings(res))
        self.assertIn("must be a SELL", reasons(self.one(dict(o, stop_order=stop_of(o, transaction_type="BUY")))))
        self.assertIn("must be an SL or SL-M order",
                      reasons(self.one(dict(o, stop_order=stop_of(o, order_type="LIMIT")))))
        self.assertIn("the stop_order is for INFY, not DEMO",
                      reasons(self.one(dict(o, stop_order=stop_of(o, tradingsymbol="INFY")))))
        self.assertIn("product 'CNC' differs", reasons(self.one(dict(o, stop_order=stop_of(o, product="CNC")))))
        self.assertIn("stop_order must be a Kite order", reasons(self.one(dict(o, stop_order="1357.50"))))

    def test_exchanges_and_assets(self):
        self.assertIn("exchange BSE: this pack trades on the NSE only", reasons(self.one(demo(exchange="BSE"))))
        self.assertIn("exchange MCX", reasons(self.one(demo(exchange="MCX"))))
        self.assertIn("no exchange", reasons(self.one(demo(exchange=None))))
        fut = kite("RELIANCE26OCTFUT", 500, 1412.4, product="MIS")
        self.assertIn("futures or options contract; F&O is off", reasons(self.one(fut)))
        self.assertIn("does not look like an NSE equity symbol", reasons(self.one(kite("BTC/USD", 1, 50.0))))

    def test_market_orders_warn_about_market_protection(self):
        o = demo(order_type="MARKET")
        o.pop("price")
        res = self.one(o)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("Kite's MCP place_order cannot send market protection", warnings(res))


class Exits(unittest.TestCase):
    PF = {"cash": 26214.4, "positions": [{"ticker": "DEMO", "shares": 54,
                                           "lots": [{"date": TODAY, "shares": 54, "cost": 73785.6}]}]}
    SELL = kite("DEMO", 54, 1364.3, side="SELL", product="MIS",
                cancel_first="the open stop-loss order for DEMO (get_orders: status TRIGGER PENDING), "
                             "cancel_order it first")

    def test_getting_out_is_never_blocked_by_the_day_limits(self):
        full = {"orders": [placed("DEMO"), placed("INFY"), placed("TCS")] +
                          [placed("T%d" % i, max_loss=1) for i in range(6)] +
                          [placed("DEMO", side="sell", kind="exit", max_loss=None)]}
        c = cfg(blocked=["DEMO"], max_order_inr=100, max_daily_loss_inr=50)
        res = check.check([self.SELL], c, portfolio=self.PF, state=full, now=at("2026-10-19 15:20"))
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["kind"]), ("PASS", "exit"), r["reasons"])
        why = warnings(res)
        for word in ("blocked:", "duplicate:", "daily limit:", "size:"):
            self.assertIn("exit, not blocked: " + word, why)
        res = check.check([self.SELL], cfg(), portfolio=self.PF, now=at("2026-10-19 15:05"))
        self.assertEqual(verdicts(res), ["PASS"])          # after the 14:45 entry cutoff: an exit passes
        res = check.check([self.SELL], cfg(), portfolio=self.PF, now=at("2026-10-20 10:00"))
        self.assertEqual(verdicts(res), ["PASS"])          # even on a holiday it is never blocked

    def test_a_partial_exit_and_the_rest(self):
        half = dict(self.SELL, quantity=27)
        res = check.check([half, half], cfg(), portfolio=self.PF, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        self.assertEqual([r["kind"] for r in res["results"]], ["exit", "exit"])
        self.assertIn("exit, not blocked: duplicate", warnings(res, 1))
        res = check.check([half, half, half], cfg(), portfolio=self.PF, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS", "BLOCK"])
        self.assertIn("short:", reasons(res, 2))

    def test_format_asset_and_short_still_apply_to_a_sell(self):
        o = dict(self.SELL)
        o.pop("price")
        res = check.check([o], cfg(), portfolio=self.PF, now=at(NOW))
        self.assertIn("a LIMIT order needs a price", reasons(res))
        res = check.check([dict(self.SELL, quantity=60)], cfg(), portfolio=self.PF, now=at(NOW))
        self.assertIn("short: selling more DEMO than you hold (54 shares)", reasons(res))
        res = check.check([self.SELL], cfg(), now=at(NOW))
        self.assertIn("record the fill first", reasons(res))

    def test_buy_then_sell_in_one_batch(self):
        res = check.check([demo(), self.SELL], cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        self.assertEqual(res["results"][1]["kind"], "exit")


class LongTermChecks(unittest.TestCase):
    """The long-term checks still hold (day-trade checks off)."""

    def one(self, order, **over):
        return check.check([order], split(**over), prices=PRICES, today=SPLIT_DAY)["results"][0]

    def test_the_five_buys_pass_on_a_fresh_one_lakh_paper_account(self):
        res = check.check(split_orders(), split(), today=SPLIT_DAY)
        self.assertEqual(verdicts(res), ["PASS"] * 5)
        self.assertEqual(res["cash_after"], 10147.35)
        self.assertEqual(res["results"][0]["value_inr"], 24010.8)
        self.assertEqual(res["results"][0]["amount"], "17 shares")
        self.assertIn("CNC is delivery", warnings(res))

    def test_a_sixth_buy_is_blocked_three_ways(self):
        res = check.check(split_orders() + [kite("RELIANCE", 1, 1412.4)], split(), today=SPLIT_DAY)
        self.assertEqual(verdicts(res), ["PASS"] * 5 + ["BLOCK"])
        why = reasons(res, 5)
        self.assertIn("cash:", why)
        self.assertIn("position:", why)
        self.assertIn("duplicate:", why)

    def test_the_day_trade_config_blocks_buys_without_a_stop(self):
        res = check.check(split_orders(), cfg(), now=at(NOW))
        self.assertEqual(verdicts(res)[0], "BLOCK")
        self.assertIn("stop: no stop", reasons(res))

    def test_max_and_min_order(self):
        r = self.one(kite("ITC", 130, 412.55), max_position_pct=100)
        self.assertIn("over your Rs 50,000 max order", " ".join(r["reasons"]))
        self.assertIn("under your Rs 100 minimum", " ".join(self.one(kite("IDEA", 1, 9.5))["reasons"]))

    def test_blocked_list(self):
        r = self.one(kite("ITC", 2, 412.55), blocked=["itc"])
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertIn("blocked list", r["reasons"][0])

    def test_options_off_unless_allowed(self):
        opt = kite("NIFTY26OCT25000CE", 75, 12.5, product="NRML", exchange="NFO")
        r = self.one(opt)
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertIn("F&O is off", " ".join(r["reasons"]))
        r = self.one(opt, allow_options=True, max_position_pct=100)
        self.assertEqual(r["verdict"], "PASS", r["reasons"])
        self.assertTrue(r["warnings"])

    def test_format_rules(self):
        self.assertIn("transaction_type must be BUY or SELL", " ".join(self.one(kite("ITC", 2, 412.55,
                                                                                      side="HOLD"))["reasons"]))
        o = kite("ITC", 2, 412.55)
        o.pop("quantity")
        self.assertIn("no quantity", " ".join(self.one(o)["reasons"]))
        self.assertIn("no product", " ".join(self.one(kite("ITC", 2, 412.55, product=""))["reasons"]))

    def test_share_orders_need_a_price(self):
        q = kite("ZZZZ", 2, order_type="MARKET")
        self.assertIn("no price for ZZZZ", " ".join(self.one(q)["reasons"]))
        q["tradingsymbol"] = "ITC"
        r = self.one(q)
        self.assertEqual(r["verdict"], "PASS")
        self.assertAlmostEqual(r["value_inr"], 825.1)

    def test_leverage(self):
        r = check.check([kite("TCS", 1, 3120.0)], split(cash_buffer_pct=0, max_position_pct=100),
                        portfolio={"cash": 1000, "positions": []}, today=SPLIT_DAY)["results"][0]
        self.assertIn("MIS leverage", " ".join(r["reasons"]))

    PF = {"cash": 500.0, "positions": [{"ticker": "ITC", "shares": 23,
                                         "lots": [{"date": SPLIT_DAY, "shares": 23, "cost": 9488.65}]}]}

    def test_short_sales_are_blocked(self):
        sell = kite("ITC", 30, 412.55, side="SELL")
        r = check.check([sell], split(), portfolio=self.PF, prices=PRICES, today=SPLIT_DAY)["results"][0]
        self.assertIn("short:", " ".join(r["reasons"]))
        sell["quantity"] = 10
        r = check.check([sell], split(), portfolio=self.PF, prices=PRICES, today=SPLIT_DAY)["results"][0]
        self.assertEqual(r["verdict"], "PASS")

    def test_a_sell_frees_cash_for_a_later_buy(self):
        orders = [kite("ITC", 20, 412.55, side="SELL"), kite("TCS", 2, 3120.0)]
        res = check.check(orders, split(max_position_pct=100), portfolio=self.PF, prices=PRICES, today=SPLIT_DAY)
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        res = check.check([kite("TCS", 2, 3120.0)], split(max_position_pct=100), portfolio=self.PF, prices=PRICES,
                          today=SPLIT_DAY)
        self.assertEqual(verdicts(res), ["BLOCK"])

    def test_daily_limit_and_already_placed_today(self):
        st = {"orders": [{"symbol": "T%d" % i, "side": "buy", "status": "placed", "at": SPLIT_DAY + "T10:00:00"}
                         for i in range(10)]}
        res = check.check([kite("ITC", 2, 412.55)], split(), state=st, today=SPLIT_DAY)
        self.assertIn("daily limit", reasons(res))
        res = check.check([kite("ITC", 2, 412.55)], split(), state=st, today="2026-10-14")
        self.assertEqual(verdicts(res), ["PASS"])
        itc = {"orders": [{"symbol": "ITC", "side": "buy", "status": "placed", "at": SPLIT_DAY + "T10:00:00"}]}
        res = check.check([kite("ITC", 2, 412.55)], split(), state=itc, today=SPLIT_DAY)
        self.assertIn("already placed today", reasons(res))
        declined = {"orders": [{"symbol": "ITC", "side": "buy", "status": "declined", "at": SPLIT_DAY + "T10:00:00"}]}
        self.assertEqual(verdicts(check.check([kite("ITC", 2, 412.55)], split(), state=declined, today=SPLIT_DAY)),
                         ["PASS"])

    def test_live_account_warns(self):
        res = check.check([kite("ITC", 2, 412.55)], split(paper=False), today=SPLIT_DAY)
        self.assertIn("LIVE", res["warnings"][0])


class Cli(unittest.TestCase):
    def test_prices_file_freshness(self):
        with tempfile.TemporaryDirectory() as d:
            o = demo(order_type="MARKET")
            o.pop("price")
            orders = os.path.join(d, "orders.json")
            with open(orders, "w") as fh:
                json.dump({"orders": [o]}, fh)
            pp = os.path.join(d, "prices.json")
            with open(pp, "w") as fh:
                json.dump({"as_of": "2026-10-19T05:20:00Z", "prices": {"DEMO": 1369.85},
                           "detail": {"DEMO": {"time": "2026-10-19T05:19:45Z", "fetched": "2026-10-19T05:20:00Z"}}},
                          fh)
            none = os.path.join(d, "none")
            base = ["--orders", orders, "--config", CFG_PATH, "--portfolio", none, "--prices", pp,
                    "--state", none, "--no-write"]
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(check.main(base + ["--now", "2026-10-19 10:55"]), 1)
            self.assertIn("reward:risk: 0.44 (target 1375.30, entry 1369.85, stop 1357.50)", buf.getvalue())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(check.main(base + ["--now", "2026-10-19 11:10"]), 0)

    def test_split_orders_with_a_split_config_file(self):
        with tempfile.TemporaryDirectory() as d:
            c = os.path.join(d, "config.json")
            with open(c, "w") as fh:
                json.dump(split(), fh)
            none = os.path.join(d, "none")
            out = os.path.join(d, "state", "risk.json")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = check.main(["--orders", SPLIT_PATH, "--config", c, "--portfolio", none, "--prices", none,
                                   "--state", none, "--today", SPLIT_DAY, "--out", out])
            self.assertEqual(code, 0)
            self.assertIn("5 passed, 0 blocked", buf.getvalue())
            with open(out) as fh:
                rec = json.load(fh)
            self.assertEqual(rec["date"], SPLIT_DAY)
            self.assertTrue(all(len(r["fingerprint"]) == 16 for r in rec["results"]))
            bad = os.path.join(d, "orders.json")
            with open(bad, "w") as fh:
                json.dump({"orders": split_orders() + [kite("RELIANCE", 1, 1412.4)]}, fh)
            with redirect_stdout(io.StringIO()):
                code = check.main(["--orders", bad, "--config", c, "--portfolio", none, "--prices", none,
                                   "--state", none, "--today", SPLIT_DAY, "--no-write"])
            self.assertEqual(code, 1)

    def test_errors_are_one_line(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(check.main(["--orders", "/nonexistent.json", "--no-write"]), 1)
        self.assertIn("no orders file", err.getvalue())
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(check.main(["--orders", DEMO_PATH, "--now", "noon", "--no-write"]), 1)
        self.assertIn("--now wants", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(check.main(["--orders", DEMO_PATH, "--today", "monday", "--no-write"]), 1)
        self.assertIn("--today wants", err.getvalue())


if __name__ == "__main__":
    unittest.main()
