"""trader-budget tests. Fixtures in tests/fixtures/trader-budget/:

plan-demo.json   trader-exit's plan for the SYNTHETIC DEMO story (a made-up NSE stock, see
                 tests/fixtures/make_synthetic.py): entry 1366.40 at 10:00 AM on Monday
                 2026-10-19, stop 1357.50 (opening-range midpoint), target 1375.30 (1R).
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

size = load("trader-budget", "size")

CFG = os.path.join(ROOT, "trader-budget", "config.example.json")
PLAN = os.path.join(FIX, "trader-budget", "plan-demo.json")
TARGETS = os.path.join(ROOT, "trader-budget", "targets.example.json")
ORDERS = os.path.join(FIX, "trader-risk", "orders-demo.json")
NOW = "2026-10-19 10:00"
TODAY = "2026-10-19"
SPLIT = {"RELIANCE": 25000, "HDFCBANK": 23214, "INFY": 18571, "TCS": 13929, "ITC": 9286}
PRICES = {"RELIANCE": 1412.35, "HDFCBANK": 987.6, "INFY": 1503.2, "TCS": 3120.0, "ITC": 412.55}  # made up
# the India edition's config, key for key
CONTRACT_CFG = {
    "budget": 100000, "cash_buffer_pct": 10, "max_position_pct": 75, "max_order_inr": 75000, "min_order_inr": 100,
    "max_loss_per_trade_inr": 1000, "max_daily_loss_inr": 3000, "max_trades_per_day": 3, "max_orders_per_day": 10,
    "min_reward_risk": 1.0, "require_stop": True,
    "no_entries_before": "09:30", "no_entries_after": "14:45", "flat_by": "15:05",
    "exchange": "NSE", "product": "MIS", "entry_order_type": "LIMIT", "entry_buffer_pct": 0,
    "stop_order_type": "SL", "stop_buffer_pct": 0.25, "exit_buffer_pct": 0.5,
    "charges": {"brokerage_pct": 0.03, "brokerage_max_inr": 20, "stt_sell_pct": 0.025,
                "exchange_txn_pct": 0.00297, "sebi_per_crore_inr": 10, "stamp_buy_pct": 0.003, "gst_pct": 18},
    "fractional": False, "broker": "zerodha", "paper": True, "blocked": [],
    "allow_options": False, "allow_margin": False, "allow_short": False,
}
# a long-term split's rules, for the weights mode tests
SPLIT_CFG = {"budget": 100000, "cash_buffer_pct": 10, "max_position_pct": 25, "max_order_inr": 50000,
             "min_order_inr": 100, "fractional": True, "paper": True, "blocked": []}


def cfg(**over):
    with open(CFG) as fh:
        c = json.load(fh)
    c.update(over)
    return c


def split_cfg(**over):
    c = dict(SPLIT_CFG)
    c.update(over)
    return c


def plan(**over):
    with open(PLAN) as fh:
        p = json.load(fh)
    p.update(over)
    return p


def rows(path):
    with open(path) as fh:
        return size.parse_weights(json.load(fh))


def rupees(result):
    return {p["ticker"]: p["rupees"] for p in result["positions"]}


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = size.main(argv)
    return code, out.getvalue(), err.getvalue()


def prices_doc(price, quote_time="2026-10-19T05:19:45Z"):
    return {"as_of": quote_time, "source": "yahoo", "prices": {"DEMO": price},
            "detail": {"DEMO": {"prev_close": 1352.4, "time": quote_time, "source": "yahoo"}}}


class TheStory(unittest.TestCase):
    """Rs 1,00,000, 10% buffer, Rs 1,000 max loss, DEMO risk 8.90 -> 54 shares (position cap), Rs 553.67."""

    def test_config_example_is_the_contract(self):
        with open(CFG) as fh:
            self.assertEqual(json.load(fh), CONTRACT_CFG)

    def test_demo_is_54_shares_bound_by_the_position_cap(self):
        s = size.size_trade(plan(), cfg(), today=TODAY)
        self.assertEqual(s["qty"], 54)
        self.assertEqual(s["bound_by"], "position cap")
        self.assertEqual((s["entry"], s["entry_from"], s["order_type"]), (1366.4, "plan limit", "LIMIT"))
        self.assertEqual(s["risk_per_share"], 8.9)
        self.assertEqual(s["cost"], 73785.6)
        self.assertEqual((s["loss_at_stop"], s["charges"], s["max_loss"]), (480.6, 73.07, 553.67))
        self.assertEqual((s["stop"], s["stop_limit"], s["target"]), (1357.5, 1354.1, 1375.3))
        self.assertEqual(s["reward_risk"], 1.0)
        self.assertEqual(s["cash_buffer"], 10000)
        self.assertEqual([(l["name"], l["shares"]) for l in s["limits"]],
                         [("max loss", 101), ("position cap", 54), ("max order", 54), ("cash", 65)])
        self.assertEqual([l["name"] for l in s["limits"] if l["binds"]], ["position cap"])
        self.assertIsNone(s["refusal"])

    def test_the_max_loss_limit_counts_the_charges(self):
        # 102 shares: 907.80 at the stop + 96.06 charges = 1003.86, over Rs 1,000; 101 fits (994.48)
        self.assertEqual(size.max_loss_shares(1000, 1366.4, 1357.5, size.CHARGES), 101)
        self.assertEqual(size.round_trip_charges(101, 1366.4, 1357.5), 95.58)
        self.assertEqual(size.round_trip_charges(102, 1366.4, 1357.5), 96.06)
        s = size.size_trade(plan(), cfg(max_position_pct=100, max_order_inr=500000, budget=500000), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"], s["max_loss"]), (101, "max loss", 994.48))

    def test_the_kite_order(self):
        o = size.kite_order(size.size_trade(plan(), cfg(), today=TODAY))
        why = o.pop("why")
        self.assertEqual(o, {
            "variety": "regular", "exchange": "NSE", "tradingsymbol": "DEMO", "transaction_type": "BUY",
            "quantity": 54, "product": "MIS", "order_type": "LIMIT", "price": 1366.4, "validity": "DAY",
            "stop_order": {"variety": "regular", "exchange": "NSE", "tradingsymbol": "DEMO",
                           "transaction_type": "SELL", "quantity": 54, "product": "MIS", "order_type": "SL",
                           "trigger_price": 1357.5, "price": 1354.1, "validity": "DAY"},
            "target_price": 1375.3,
            "plan": {"entry": 1366.4, "stop": 1357.5, "stop_limit": 1354.1, "target": 1375.3,
                     "risk_per_share": 8.9, "charges": 73.07, "max_loss": 553.67, "setup": "orb",
                     "entry_time": "10:00 AM", "flat_by": "15:05"}})
        self.assertIn("bound by your position cap", why)
        self.assertIn("Rs 480.60 at the stop, plus about Rs 73.07 charges = Rs 553.67", why)
        for k in ("quantity", "price"):
            self.assertNotIsInstance(o[k], str)         # Kite's place_order takes numbers

    def test_the_fixture_orders_file_is_what_it_writes(self):
        with open(ORDERS) as fh:
            fixture = json.load(fh)["orders"][0]
        self.assertEqual(size.kite_order(size.size_trade(plan(), cfg(), today=TODAY)), fixture)

    def test_your_limit_and_the_entry_buffer(self):
        s = size.size_trade(plan(), cfg(), limit=1366.0, today=TODAY)
        self.assertEqual((s["entry"], s["risk_per_share"], s["qty"], s["entry_from"]), (1366.0, 8.5, 54, "limit"))
        self.assertEqual(size.kite_order(s)["price"], 1366.0)
        s = size.size_trade(plan(), cfg(), limit=1366.03, today=TODAY)       # off the 0.10 tick: up to 1366.10
        self.assertEqual(s["entry"], 1366.1)
        self.assertIn("rounded up to 1366.10", s["notes"][0])
        s = size.size_trade(plan(), cfg(entry_buffer_pct=0.1), today=TODAY)  # 1366.40 x 1.001 = 1367.77 -> 1367.80
        self.assertEqual((s["entry"], s["entry_from"], s["risk_per_share"]), (1367.8, "buffered limit", 10.3))
        self.assertIn("reward:risk 0.73", s["refusal"])                     # a buffer costs reward:risk

    def test_a_market_buy(self):
        s = size.size_trade(plan(), cfg(), market=True, today=TODAY)
        o = size.kite_order(s)
        self.assertEqual((o["order_type"], "price" in o, s["entry_from"]), ("MARKET", False, "plan"))
        self.assertIn("market protection", size.plan_report(s, o))
        o = size.kite_order(size.size_trade(plan(), cfg(stop_order_type="SL-M"), today=TODAY))
        self.assertEqual(o["stop_order"]["order_type"], "SL-M")
        self.assertNotIn("price", o["stop_order"])

    def test_cli_writes_one_order(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "trader", "orders.json")
            code, text, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", os.path.join(d, "none"),
                                 "--prices", os.path.join(d, "none"), "--now", NOW, "--write", out])
            self.assertEqual(code, 1)          # an explicit --prices file that does not exist is an error
            code, text, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", os.path.join(d, "none"),
                                 "--now", NOW, "--write", out])
            self.assertEqual(code, 0)
            self.assertIn("Size: 54 shares of DEMO = Rs 73,785.60 at 1366.40", text)
            self.assertIn("54 x 8.90 = Rs 480.60, plus about Rs 73.07 charges = Rs 553.67", text)
            self.assertIn("75% of Rs 1,00,000 = Rs 75,000 / 1366.40", text)
            self.assertIn("<- binds", text)
            self.assertIn("1. BUY 54 DEMO, LIMIT 1366.40, MIS intraday, NSE, day", text)
            self.assertIn("2. after it fills: SELL 54 DEMO, SL trigger 1357.50 limit 1354.10, MIS, day", text)
            self.assertIn("target 1375.30 is watched by trader-alerts, not sent to Zerodha", text)
            self.assertIn("Flat by 15:05 IST", text)
            self.assertIn("not financial advice", text)
            with open(out) as fh:
                doc = json.load(fh)
            self.assertEqual(doc["status"], "proposed")
            self.assertIs(doc["paper"], True)
            self.assertEqual(doc["created_at"], "2026-10-19T04:30:00Z")      # 10:00 IST
            self.assertEqual(len(doc["orders"]), 1)
            self.assertEqual(doc["orders"][0]["plan"]["max_loss"], 553.67)

    def test_cli_json_and_limit(self):
        code, text, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", "/nonexistent", "--now", NOW,
                             "--limit", "1366", "--json"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual(out["sizing"]["qty"], 54)
        self.assertEqual(out["orders"][0]["price"], 1366.0)
        code, _, err = run(["--plan", PLAN, "--config", CFG, "--now", NOW, "--limit", "1366", "--market"])
        self.assertEqual(code, 1)
        self.assertIn("not both", err)


class PlanRules(unittest.TestCase):
    def test_zero_shares_is_a_plain_refusal(self):
        s = size.size_trade(plan(stop=300.0), cfg(), today=TODAY)
        self.assertEqual(s["qty"], 0)
        self.assertIn("one share risks Rs 1,067.17 at the stop with charges", s["refusal"])
        self.assertIn("more than your Rs 1,000 max loss per trade", s["refusal"])
        self.assertIsNone(size.kite_order(s))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "plan.json")
            with open(p, "w") as fh:
                json.dump(plan(stop=300.0), fh)
            out = os.path.join(d, "orders.json")
            code, text, _ = run(["--plan", p, "--config", CFG, "--portfolio", os.path.join(d, "none"),
                                 "--now", NOW, "--write", out])
            self.assertEqual(code, 1)
            self.assertIn("No order:", text)
            self.assertFalse(os.path.exists(out))

    def test_each_limit_can_bind(self):
        self.assertEqual(size.size_trade(plan(), cfg(max_order_inr=20000), today=TODAY)["bound_by"], "max order")
        s = size.size_trade(plan(), cfg(max_position_pct=20), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (14, "position cap"))
        s = size.size_trade(plan(), cfg(), cash=12000, today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"], s["cash_available"]), (1, "cash", 2000))
        s = size.size_trade(plan(), cfg(max_loss_per_trade_inr=300), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (29, "max loss"))
        self.assertLessEqual(s["max_loss"], 300)

    def test_a_limit_above_a_fresh_price_is_noted(self):
        s = size.size_trade(plan(), cfg(), price=1369.85, today=TODAY)
        self.assertEqual((s["entry"], s["qty"]), (1366.4, 54))
        self.assertIn("DEMO last 1369.85 is above your limit 1366.40", s["notes"][0])

    def test_a_fresh_higher_price_means_a_bigger_risk_for_a_market_buy(self):
        s = size.size_trade(plan(), cfg(min_reward_risk=0), price=1369.85, market=True, today=TODAY)
        self.assertEqual((s["entry"], s["entry_from"], s["risk_per_share"]), (1369.85, "price", 12.35))
        self.assertEqual((s["qty"], s["reward_risk"]), (54, 0.44))
        self.assertIsNone(s["refusal"])
        s = size.size_trade(plan(), cfg(), price=1369.85, market=True, today=TODAY)
        self.assertIn("reward:risk 0.44 (target 1375.30, entry 1369.85, stop 1357.50) is under your minimum 1",
                      s["refusal"])
        self.assertIsNone(size.kite_order(s))
        lower = size.size_trade(plan(), cfg(), price=1360.00, market=True, today=TODAY)
        self.assertEqual((lower["entry"], lower["qty"]), (1366.4, 54))

    def test_prices_file_is_used_only_when_fresh(self):
        with tempfile.TemporaryDirectory() as d:
            pp = os.path.join(d, "prices.json")
            with open(pp, "w") as fh:
                json.dump(prices_doc(1369.85), fh)
            base = ["--plan", PLAN, "--config", CFG, "--portfolio", os.path.join(d, "none"), "--prices", pp,
                    "--json", "--market"]
            code, text, _ = run(base + ["--now", "2026-10-19 10:55"])
            out = json.loads(text)
            self.assertEqual((code, out["sizing"]["entry"], out["orders"]), (1, 1369.85, []))
            self.assertIn("above the plan's entry", out["notes"][0])
            out = json.loads(run(base + ["--now", "2026-10-19 11:10"])[1])
            self.assertEqual((out["sizing"]["entry"], out["sizing"]["qty"]), (1366.4, 54))
            self.assertIn("not used", out["notes"][0])

    def test_the_portfolio_cash_caps_the_size(self):
        with tempfile.TemporaryDirectory() as d:
            pf = os.path.join(d, "portfolio.json")
            with open(pf, "w") as fh:
                json.dump({"cash": 12000, "positions": []}, fh)
            out = json.loads(run(["--plan", PLAN, "--config", CFG, "--portfolio", pf, "--now", NOW, "--json"])[1])
            self.assertEqual((out["sizing"]["qty"], out["sizing"]["bound_by"]), (1, "cash"))

    def test_bad_plans(self):
        for p, word in ((plan(stop=1367.0), "not below the entry"),
                        (plan(target=1360.0), "already happened"),
                        (plan(target=None), "positive target"),
                        (plan(side="sell"), "long (buy) day trades only"),
                        (plan(symbol=""), "no symbol"),
                        (plan(symbol="DEMO.BO"), "BSE listing")):
            with self.assertRaises(size.SizeError) as e:
                size.size_trade(p, cfg(), today=TODAY)
            self.assertIn(word, str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(), limit=1376.0, today=TODAY)
        self.assertIn("already happened", str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(blocked=["demo"]), today=TODAY)
        self.assertIn("blocked list", str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(), today="2026-10-21")
        self.assertIn("make a new plan", str(e.exception))

    def test_a_plan_with_an_nse_suffix_is_the_same_stock(self):
        self.assertEqual(size.size_trade(plan(symbol="demo.ns"), cfg(), today=TODAY)["symbol"], "DEMO")

    def test_under_the_minimum_order(self):
        cheap = plan(symbol="ABC", entry=40.0, stop=30.0, target=50.0)
        s = size.size_trade(cheap, cfg(max_loss_per_trade_inr=20, min_order_inr=100), today=TODAY)
        self.assertEqual(s["qty"], 1)
        self.assertIn("under your Rs 100 minimum order", s["refusal"])
        self.assertIsNone(size.kite_order(s))

    def test_the_stop_and_target_land_on_the_tick(self):
        s = size.size_trade(plan(stop=1357.55, target=1375.33), cfg(), today=TODAY)
        self.assertEqual((s["stop"], s["target"]), (1357.5, 1375.3))      # stop down, target to the nearest
        cheap = size.size_trade(plan(symbol="PENNY", entry=212.37, stop=209.123, target=216.0), cfg(), today=TODAY)
        self.assertEqual((cheap["stop"], cheap["stop_limit"]), (209.12, 208.59))   # the 0.01 tick under Rs 250

    def test_missing_plan_and_weights_only_flags(self):
        code, _, err = run(["--plan", "/nonexistent/plan.json", "--config", CFG, "--now", NOW])
        self.assertEqual(code, 1)
        self.assertIn("no plan", err)
        code, _, err = run(["--plan", PLAN, "--config", CFG, "--now", NOW, "--top", "2"])
        self.assertEqual(code, 1)
        self.assertIn("--weights", err)
        code, _, err = run(["--plan", PLAN, "--config", CFG, "--now", "tomorrow"])
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)


class Money(unittest.TestCase):
    def test_indian_digit_grouping(self):
        self.assertEqual(size.inr(100000), "Rs 1,00,000")
        self.assertEqual(size.inr(73785.6), "Rs 73,785.60")
        self.assertEqual(size.inr(12345678.9), "Rs 1,23,45,678.90")
        self.assertEqual(size.inr(553.67), "Rs 553.67")

    def test_the_story_charges(self):
        # buy 54 at 1366.40 (Rs 73,785.60), sell at 1357.50 (Rs 73,305): Rs 40 brokerage (two Rs 20 caps),
        # Rs 4.37 NSE, Rs 0.15 SEBI, Rs 18.33 STT, Rs 2.21 stamp, Rs 8.01 GST
        self.assertEqual(size.round_trip_charges(54, 1366.4, 1357.5), 73.07)
        self.assertEqual(size.round_trip_charges(54, 1366.4, 1357.5, {"brokerage_max_inr": 0}), 25.87)


class WeightsMode(unittest.TestCase):
    """v1's split, kept behind --weights / --targets: Rs 1,00,000, 10% cash, 25% cap."""

    def check_split(self, r):
        self.assertEqual(rupees(r), SPLIT)
        self.assertEqual(r["cash_buffer"], 10000)
        self.assertEqual(r["invested"], 90000)
        self.assertEqual(r["idle"], 0)
        self.assertEqual(r["cap"], 25000)
        self.assertEqual([p["ticker"] for p in r["positions"] if p["capped"]], ["RELIANCE"])

    def test_from_the_example_weights_file(self):
        self.check_split(size.size(rows(TARGETS), split_cfg()))

    def test_from_inline_weights(self):
        w = size.parse_weights(size.weights_from_arg("RELIANCE=30,HDFCBANK=25,INFY=20,TCS=15,ITC=10"))
        self.check_split(size.size(w, split_cfg()))

    def test_the_example_says_it_is_an_example(self):
        with open(TARGETS) as fh:
            self.assertIn("not a recommendation", json.load(fh)["source"])
        r = size.size(rows(TARGETS), split_cfg())
        self.assertIn("not a recommendation", size.report(r, size.orders_from(r)))

    def test_raw_weights_are_renormalised(self):
        w = size.parse_weights({"RELIANCE": 3, "HDFCBANK": 2.5, "INFY": 2, "TCS": 1.5, "ITC": 1})
        self.check_split(size.size(w, split_cfg()))

    def test_a_rupee_split_writes_no_orders(self):
        r = size.size(rows(TARGETS), split_cfg())
        self.assertFalse(r["whole_shares"])
        self.assertEqual(size.orders_from(r), [])
        self.assertIn("Zerodha takes whole shares only", size.report(r, []))

    def test_paise_mode_keeps_the_total(self):
        r = size.size(rows(TARGETS), split_cfg(), paise_mode=True)
        d = rupees(r)
        self.assertEqual(d["RELIANCE"], 25000)
        self.assertAlmostEqual(sum(d.values()), 90000, places=2)
        self.assertAlmostEqual(d["HDFCBANK"], 23214.29, places=2)

    def test_cap_redistributes_until_nobody_is_over(self):
        w = size.parse_weights({"AAA": 60, "BBB": 30, "CCC": 10})
        r = size.size(w, {"budget": 10000, "cash_buffer_pct": 0, "max_position_pct": 40, "min_order_inr": 1,
                                      "fractional": True})
        self.assertEqual(rupees(r), {"AAA": 4000, "BBB": 4000, "CCC": 2000})
        self.assertEqual(sorted(p["ticker"] for p in r["positions"] if p["capped"]), ["AAA", "BBB"])

    def test_everything_capped_leaves_money_idle(self):
        w = size.parse_weights({"RELIANCE": 50, "ITC": 50})
        r = size.size(w, split_cfg())
        self.assertEqual(rupees(r), {"RELIANCE": 25000, "ITC": 25000})
        self.assertEqual(r["idle"], 40000)
        self.assertIn("every stock is at its cap", size.report(r, size.orders_from(r)))

    def test_slices_under_the_minimum_are_dropped_and_shared_out(self):
        w = size.parse_weights({"AAA": 90, "BBB": 7, "CCC": 3})
        r = size.size(w, {"budget": 1000, "cash_buffer_pct": 10, "max_position_pct": 100, "min_order_inr": 50,
                              "fractional": True})
        self.assertNotIn("CCC", rupees(r))
        self.assertEqual(sum(rupees(r).values()), 900)
        self.assertTrue(all(v >= 50 for v in rupees(r).values()))
        self.assertEqual(r["dropped"][0]["ticker"], "CCC")

    def test_blocked_tickers_are_dropped_first(self):
        r = size.size(rows(TARGETS), split_cfg(blocked=["itc"]))
        self.assertNotIn("ITC", rupees(r))
        self.assertEqual(r["invested"], 90000)
        self.assertIn("blocked", r["dropped"][0]["reason"])

    def test_top_n(self):
        r = size.size(rows(TARGETS), split_cfg(max_position_pct=100), top=2)
        self.assertEqual(sorted(rupees(r)), ["HDFCBANK", "RELIANCE"])

    def test_whole_shares_need_prices(self):
        with self.assertRaises(size.SizeError):
            size.size(rows(TARGETS), split_cfg(fractional=False))
        with self.assertRaises(size.SizeError):
            size.size(rows(TARGETS), cfg())          # the example config is whole shares

    def test_whole_shares_with_prices_are_kite_cnc_orders(self):
        r = size.size(rows(TARGETS), split_cfg(fractional=False), prices=PRICES)
        self.assertTrue(r["whole_shares"])
        for p in r["positions"]:
            self.assertEqual(p["shares"], int(p["shares"]))
            self.assertAlmostEqual(p["rupees"], p["shares"] * PRICES[p["ticker"]], places=2)
            self.assertLessEqual(p["rupees"], 25000)
        self.assertAlmostEqual(r["invested"] + r["idle"], 90000, places=2)
        self.assertLess(r["idle"], min(PRICES.values()) * 2)
        orders = size.orders_from(r)
        self.assertEqual(orders[0]["tradingsymbol"], "RELIANCE")
        for o in orders:
            self.assertEqual((o["exchange"], o["product"], o["order_type"], o["transaction_type"]),
                             ("NSE", "CNC", "LIMIT", "BUY"))
            self.assertIsInstance(o["quantity"], int)
        self.assertEqual(orders[0]["price"], 1412.4)     # 1412.35 rounded up to the 0.10 tick

    def test_bad_symbols_and_duplicates_are_errors(self):
        with self.assertRaises(size.SizeError):
            size.size(rows(TARGETS), {"cash_buffer_pct": 10})
        with self.assertRaises(size.SizeError):
            size.size([{"ticker": "ITC", "name": "", "weight": 1}, {"ticker": "ITC", "name": "", "weight": 2}],
                      split_cfg())
        with self.assertRaises(size.SizeError):
            size.parse_weights({"BTC/USD": 1})

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            c = os.path.join(d, "config.json")
            with open(c, "w") as fh:
                json.dump(split_cfg(fractional=False), fh)
            pp = os.path.join(d, "prices.json")
            with open(pp, "w") as fh:
                json.dump({"prices": PRICES}, fh)
            out = os.path.join(d, "trader", "orders.json")
            code, text, _ = run(["--targets", TARGETS, "--config", c, "--prices", pp, "--write", out])
            self.assertEqual(code, 0)
            self.assertIn("left idle", text)
            self.assertIn("require_stop", text)
            with open(out) as fh:
                doc = json.load(fh)
            self.assertEqual([o["tradingsymbol"] for o in doc["orders"]],
                             ["RELIANCE", "HDFCBANK", "INFY", "TCS", "ITC"])
            code, text, _ = run(["--targets", TARGETS, "--config", c, "--prices", pp, "--json"])
            self.assertLess(json.loads(text)["result"]["idle"], 1000)
            code, _, err = run(["--targets", "/nonexistent/targets.json", "--config", c])
            self.assertEqual(code, 1)
            self.assertIn("no weights file", err)


if __name__ == "__main__":
    unittest.main()
