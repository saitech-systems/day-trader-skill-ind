"""trader-broker tests, on the SYNTHETIC DEMO story (a made-up NSE stock, see
tests/fixtures/make_synthetic.py): tests/fixtures/trader-risk/orders-demo.json is what
trader-budget writes, tests/fixtures/trader-budget/plan-demo.json is trader-exit's plan.
"""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

ticket = load("trader-broker", "ticket")
check = load("trader-risk", "check")

CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
ORDERS_PATH = os.path.join(FIX, "trader-risk", "orders-demo.json")
PLAN_PATH = os.path.join(FIX, "trader-budget", "plan-demo.json")
NOW = "2026-10-19 10:00"
TODAY = "2026-10-19"
ITC = {"variety": "regular", "exchange": "NSE", "tradingsymbol": "ITC", "transaction_type": "BUY", "quantity": 23,
       "product": "CNC", "order_type": "LIMIT", "price": 412.55, "validity": "DAY"}
BATCH = ["RELIANCE", "HDFCBANK", "INFY", "TCS", "ITC"]
DEMO_CALL = {"variety": "regular", "exchange": "NSE", "tradingsymbol": "DEMO", "transaction_type": "BUY",
             "quantity": 54, "product": "MIS", "order_type": "LIMIT", "price": 1366.4, "validity": "DAY"}
DEMO_STOP_CALL = {"variety": "regular", "exchange": "NSE", "tradingsymbol": "DEMO", "transaction_type": "SELL",
                  "quantity": 54, "product": "MIS", "order_type": "SL", "price": 1354.1, "trigger_price": 1357.5,
                  "validity": "DAY"}
DEMO_PLAIN = ("BUY 54 DEMO, limit 1366.40, MIS intraday, NSE, good for today / then stop-loss: SELL 54 DEMO, "
              "SL trigger 1357.50 limit 1354.10, MIS intraday, NSE, good for today, placed after the buy fills / "
              "target 1375.30 (watched by trader-alerts, not sent to Zerodha) / max loss about Rs 553.67 if the "
              "stop fills at 1357.50, incl. about Rs 73.07 charges (it can fill worse, or not at all, in a fast "
              "market)")


def demo(**over):
    with open(ORDERS_PATH) as fh:
        o = json.load(fh)["orders"][0]
    o.update(over)
    return o


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ticket.main(argv)
    return code, out.getvalue(), err.getvalue()


def json_blocks(text):
    """Every top-level {...} block printed in text, parsed."""
    out, depth, start = [], 0, None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                out.append(json.loads(text[start:i + 1]))
    return out


class ExplicitYes(unittest.TestCase):
    def yes(self, reply, order=ITC, batch=BATCH):
        return ticket.parse_yes(reply, order, batch)[0]

    def test_plain_yes_counts(self):
        for r in ("yes", "Yes.", "YES", "y", "yes place it", "Yes, buy it", "yes ITC", "yes 23 ITC", "yes 412.55"):
            self.assertTrue(self.yes(r), r)

    def test_anything_else_does_not(self):
        for r in ("", "sure", "ok", "okay", "go", "do it", "yeah", "yep", "haan", "theek hai", "absolutely",
                  "send it", "yes to all", "yes, all of them", "yes to the rest", "yes both", "yes INFY",
                  "yes 1000", "yes but make it 20", "yes?", "no", "not yes", "yes, wait", "yes if it is under 400"):
            self.assertFalse(self.yes(r), r)

    def test_amount_must_match_this_order(self):
        sell = dict(ITC, transaction_type="SELL", quantity=3)
        self.assertTrue(self.yes("yes sell 3 ITC", sell))
        self.assertFalse(self.yes("yes sell 4", sell))

    def test_a_yes_for_an_entry_and_its_stop(self):
        o = demo()
        for r in ("yes", "yes 54 DEMO", "yes 1357.50", "yes max loss 553.67", "yes target 1375.30", "yes 1366.40",
                  "yes Rs 553.67"):
            self.assertTrue(self.yes(r, o, ["DEMO"]), r)
        for r in ("yes 55", "yes 100 DEMO", "yes but stop at 1350", "yes, stop 1357.50", "yes INFY", "sure"):
            self.assertFalse(self.yes(r, o, ["DEMO", "INFY"]), r)


class TicketShape(unittest.TestCase):
    def test_tool_input_is_the_kite_call(self):
        self.assertEqual(ticket.tool_input(dict(ITC, why="from a list")), ITC)
        self.assertIsInstance(ticket.tool_input(ITC)["quantity"], int)

    def test_the_entry_call_and_its_stop_call_leave_out_our_notes(self):
        self.assertEqual(ticket.tool_input(demo()), DEMO_CALL)
        self.assertEqual(ticket.stop_input(demo()), DEMO_STOP_CALL)
        self.assertIsNone(ticket.stop_input(ITC))
        self.assertEqual(ticket.fingerprint(demo()), ticket.fingerprint(dict(demo(), why="x", plan={})))

    def test_plain_english(self):
        self.assertEqual(ticket.plain(ITC), "BUY 23 shares of ITC, limit order at 412.55, CNC delivery, good for today")
        self.assertEqual(ticket.plain(dict(ITC, tradingsymbol="DEMO", transaction_type="SELL", quantity=1,
                                           product="MIS", order_type="MARKET", price=None)),
                         "SELL 1 share of DEMO, market order, MIS intraday, good for today")
        self.assertEqual(ticket.plain(demo()), DEMO_PLAIN)
        exit_ = dict(ITC, transaction_type="SELL", cancel_first="the open stop-loss order for ITC")
        self.assertIn(" / first: cancel the open stop-loss order for ITC", ticket.plain(exit_))

    def test_max_loss(self):
        self.assertEqual(ticket.max_loss(demo()), (553.67, 1357.5, 73.07))
        self.assertEqual(ticket.max_loss(demo(price=1366.0)), (532.07, 1357.5, 73.07))
        bare = dict(DEMO_CALL)
        self.assertEqual(ticket.max_loss(bare), (None, None, None))


class DesktopConfig(unittest.TestCase):
    def test_example_has_placeholder_keys_and_the_hosted_server(self):
        with open(os.path.join(ROOT, "trader-broker", "claude_desktop_config.example.json")) as fh:
            servers = json.load(fh)["mcpServers"]
        kite = servers["kite"]
        self.assertEqual(kite["env"]["APP_MODE"], "stdio")
        for k in ("KITE_API_KEY", "KITE_API_SECRET"):
            self.assertIn("NEVER_IN_CHAT", kite["env"][k])
        self.assertEqual((servers["kite-hosted"]["command"], servers["kite-hosted"]["args"]),
                         ("npx", ["mcp-remote", "https://mcp.kite.trade/mcp"]))


class Flow(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.orders = os.path.join(self.d, "orders.json")
        shutil.copy(ORDERS_PATH, self.orders)
        self.cfg = os.path.join(self.d, "config.json")
        shutil.copy(CFG_PATH, self.cfg)
        self.risk = os.path.join(self.d, "state", "risk.json")
        self.state = os.path.join(self.d, "state", "broker.json")
        self.pf = os.path.join(self.d, "portfolio.json")
        self.none = os.path.join(self.d, "none")

    def tearDown(self):
        shutil.rmtree(self.d)

    def live(self):
        with open(self.cfg) as fh:
            c = json.load(fh)
        c["paper"] = False
        with open(self.cfg, "w") as fh:
            json.dump(c, fh)

    def args(self, *extra, **kw):
        return list(extra) + ["--orders", self.orders, "--config", self.cfg, "--risk", self.risk,
                              "--state", self.state, "--now", kw.get("now", NOW)]

    def run_risk(self, now=NOW):
        with redirect_stdout(io.StringIO()):
            return check.main(["--orders", self.orders, "--config", self.cfg, "--portfolio", self.pf,
                               "--prices", self.none, "--state", self.state, "--now", now, "--out", self.risk])

    def state_doc(self):
        with open(self.state) as fh:
            return json.load(fh)

    def buy_and_fill(self):
        self.run_risk()
        run(self.args("approve", "--index", "1", "--reply", "yes"))
        run(self.args("result", "--index", "1", "--status", "placed", "--filled-qty", "54",
                      "--filled-price", "1366.40", "--portfolio", self.pf, now="2026-10-19 10:01"))

    def test_nothing_is_ready_before_the_risk_check(self):
        code, text, _ = run(self.args("show"))
        self.assertEqual(code, 1)
        self.assertIn("NOT READY", text)
        self.assertNotIn("Reply \"yes\"", text)
        code, _, err = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 1)
        self.assertIn("not ready", err)

    def test_the_paper_flow(self):
        self.assertEqual(self.run_risk(), 0)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 0)
        self.assertIn("ORDER 1 of 1, PAPER account (nothing is sent to Zerodha)", text)
        for part in DEMO_PLAIN.split(" / "):
            self.assertIn("  " + part + "\n", text)
        self.assertIn("READY: trader-risk PASS", text)
        self.assertIn("Claude calls no Kite tool for this order", text)
        self.assertIn('"trigger_price": 1357.5', text)
        self.assertNotIn('"plan"', text)
        self.assertNotIn('"why"', text)
        self.assertNotIn("Allow once", text)
        self.assertEqual(json_blocks(text), [DEMO_CALL, DEMO_STOP_CALL])

        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "sure"))
        self.assertEqual(code, 1)
        self.assertIn("Not recorded", text)
        self.assertFalse(os.path.exists(self.state))

        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 0)
        self.assertIn("Paper account: do not call any Kite tool", text)
        self.assertNotIn("place_order with exactly", text)
        code, _, err = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 1)
        self.assertIn("APPROVED", err)

        code, text, _ = run(self.args("result", "--index", "1", "--status", "placed", "--filled-qty", "54",
                                      "--filled-price", "1366.40", "--portfolio", self.pf, now="2026-10-19 10:01"))
        self.assertEqual(code, 0)
        self.assertIn("Recorded order 1 as placed (paper)", text)
        self.assertIn("Paper stop-loss: ticket.py result --index 1 --leg stop", text)
        st = self.state_doc()
        self.assertEqual([r["status"] for r in st["orders"]], ["approved", "placed", "filled"])
        appr, plc = st["orders"][0], st["orders"][1]
        self.assertEqual((appr["reply"], appr["kind"], appr["max_loss"], appr["date"], appr["paper"]),
                         ("yes", "entry", 553.67, TODAY, True))
        self.assertEqual(appr["at"], "2026-10-19T10:00:00+05:30")
        self.assertEqual((plc["max_loss"], plc["kind"], plc["paper"]), (553.67, "entry", True))
        with open(self.pf) as fh:
            pf = json.load(fh)
        self.assertEqual(pf["cash"], 26214.4)
        self.assertEqual(pf["positions"][0]["shares"], 54)

        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("DONE: placed", text)
        self.assertIn("stop-loss not recorded yet", text)
        code, text, _ = run(self.args("result", "--index", "1", "--leg", "stop", "--status", "placed",
                                      now="2026-10-19 10:01"))
        self.assertEqual(code, 0)
        self.assertIn("stop-loss as placed (paper)", text)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("stop-loss placed at 2026-10-19T10:01:00+05:30", text)
        code, text, _ = run(["log", "--state", self.state, "--now", NOW])
        self.assertIn("1 placed today", text)
        self.assertIn("max loss Rs 553.67", text)
        self.assertIn("stop placed", text)

        # trader-risk now counts it once: a second DEMO buy today is a duplicate, and Rs 553.67 is used
        st = self.state_doc()
        res = check.check([demo()], {"budget": 100000}, state=st, now=check.parse_now("2026-10-19 10:30"))
        self.assertIn("already placed today", " ".join(res["results"][0]["reasons"]))
        self.assertEqual((res["today"]["entries_placed"], res["today"]["orders_placed"],
                          res["today"]["loss_at_risk"]), (1, 1, 553.67))

    def test_the_live_flow_places_the_stop_after_the_fill(self):
        self.live()
        self.assertEqual(self.run_risk(), 0)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("ORDER 1 of 1, LIVE ACCOUNT, REAL MONEY, Zerodha (Kite)", text)
        self.assertIn("After your yes, Claude calls place_order (Kite MCP) with exactly:", text)
        self.assertIn("Once it fills (get_orders shows COMPLETE)", text)
        self.assertIn("One yes covers both calls", text)
        self.assertIn("Allow once", text)
        self.assertEqual(json_blocks(text), [DEMO_CALL, DEMO_STOP_CALL])
        code, _, err = run(self.args("result", "--index", "1", "--leg", "stop", "--status", "placed"))
        self.assertEqual(code, 1)
        self.assertIn("not recorded as placed today", err)

        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "yes 54 DEMO"))
        self.assertEqual(code, 0)
        self.assertEqual(json_blocks(text), [DEMO_CALL, DEMO_STOP_CALL])
        self.assertIn("After the buy fills, call place_order for the stop-loss", text)
        code, _, err = run(self.args("result", "--index", "1", "--leg", "stop", "--status", "placed"))
        self.assertEqual(code, 1)
        self.assertIn("goes in only after the buy fills", err)

        code, text, _ = run(self.args("result", "--index", "1", "--status", "placed", "--broker-id",
                                      "261019000123456"))
        self.assertEqual(code, 0)
        self.assertIn("When it fills, record it: ticket.py fill --symbol DEMO --side buy", text)
        self.assertEqual(json_blocks(text), [DEMO_STOP_CALL])
        code, text, _ = run(self.args("result", "--index", "1", "--leg", "stop", "--status", "placed",
                                      "--broker-id", "261019000123999"))
        self.assertEqual(code, 0)
        st = self.state_doc()
        self.assertEqual([(r["status"], r.get("leg")) for r in st["orders"]],
                         [("approved", None), ("placed", None), ("placed", "stop")])
        self.assertEqual((st["orders"][1]["broker_order_id"], st["orders"][1]["paper"]), ("261019000123456", False))
        code, text, _ = run(["log", "--state", self.state, "--now", NOW])
        self.assertIn("id 261019000123999", text)
        self.assertIn("1 placed today", text)

    def test_a_failed_stop_says_to_fix_it(self):
        self.buy_and_fill()
        code, text, _ = run(self.args("result", "--index", "1", "--leg", "stop", "--status", "failed",
                                      "--note", "RMS rejected"))
        self.assertEqual(code, 0)
        self.assertIn("The shares have no stop at Zerodha", text)

    def test_flatten_by_the_close(self):
        self.buy_and_fill()
        late = "2026-10-19 15:05"
        code, text, _ = run(["flatten", "--symbol", "DEMO", "--price", "1371.20", "--orders", self.orders,
                             "--portfolio", self.pf, "--plan", PLAN_PATH, "--config", self.cfg, "--now", late])
        self.assertEqual(code, 0)
        self.assertIn("SELL 54 shares of DEMO, limit order at 1364.30, MIS intraday, good for today "
                      "(flat by 15:05 IST)", text)
        self.assertIn("the last price 1371.20 minus your 0.5% exit buffer", text)
        self.assertIn("replaced 1 earlier proposal", text)
        with open(self.orders) as fh:
            doc = json.load(fh)
        o = doc["orders"][0]
        self.assertEqual(ticket.tool_input(o), {"variety": "regular", "exchange": "NSE", "tradingsymbol": "DEMO",
                                                "transaction_type": "SELL", "quantity": 54, "product": "MIS",
                                                "order_type": "LIMIT", "price": 1364.3, "validity": "DAY"})
        self.assertIn("TRIGGER PENDING", o["cancel_first"])
        self.assertEqual(self.run_risk(now=late), 0)       # after the 14:45 entry cutoff: an exit still passes
        with open(self.risk) as fh:
            self.assertEqual(json.load(fh)["results"][0]["kind"], "exit")
        code, text, _ = run(self.args("show", now=late))
        self.assertEqual(code, 0)
        self.assertIn("READY", text)
        self.assertIn("first: cancel the open stop-loss order for DEMO", text)
        self.assertEqual(run(self.args("approve", "--index", "1", "--reply", "yes sell 54 DEMO", now=late))[0], 0)
        code, _, _ = run(self.args("result", "--index", "1", "--status", "placed", "--filled-qty", "54",
                                   "--filled-price", "1364.30", "--portfolio", self.pf, now="2026-10-19 15:06"))
        self.assertEqual(code, 0)
        with open(self.pf) as fh:
            pf = json.load(fh)
        self.assertEqual((pf["cash"], pf["positions"]), (99886.6, []))
        code, _, err = run(["flatten", "--symbol", "DEMO", "--orders", self.orders, "--portfolio", self.pf,
                            "--now", late])
        self.assertEqual(code, 1)
        self.assertIn("nothing to flatten", err)

    def test_a_live_flatten_cancels_the_stop_first(self):
        self.live()
        code, text, _ = run(["flatten", "--symbol", "DEMO", "--qty", "54", "--price", "1371.20", "--orders",
                             self.orders, "--portfolio", self.none, "--plan", PLAN_PATH, "--config", self.cfg,
                             "--now", "2026-10-19 15:05"])
        self.assertIn("cancel_order (variety regular, its order_id; Needs approval)", text)
        self.run_risk(now="2026-10-19 15:05")
        code, text, _ = run(self.args("show", now="2026-10-19 15:05"))
        self.assertIn("First cancel the open stop-loss order for DEMO", text)

    def test_flatten_from_the_prices_file_or_market(self):
        pp = os.path.join(self.d, "prices.json")
        with open(pp, "w") as fh:
            json.dump({"as_of": "2026-10-19T09:30:00Z", "prices": {"DEMO": 1371.2},
                       "detail": {"DEMO": {"time": "2026-10-19T09:30:00Z"}}}, fh)
        base = ["flatten", "--symbol", "DEMO", "--qty", "54", "--prices", pp, "--orders", self.orders,
                "--portfolio", self.none, "--plan", self.none, "--config", self.cfg]
        code, text, _ = run(base + ["--now", "2026-10-19 15:05"])
        self.assertIn("limit order at 1364.30", text)
        self.assertIn("(15:00 IST)", text)
        code, text, _ = run(base + ["--now", "2026-10-19 15:25"])          # the price is 25 minutes old
        self.assertIn("market order", text)
        self.assertIn("No fresh price for DEMO", text)
        self.assertIn("market protection", text)

    def test_flatten_with_the_broker_qty(self):
        code, text, _ = run(["flatten", "--symbol", "demo", "--qty", "54", "--orders", self.orders,
                             "--portfolio", self.none, "--plan", self.none, "--config", self.cfg, "--now", NOW])
        self.assertEqual(code, 0)
        self.assertNotIn("Check it against get_positions", text)
        self.assertEqual(run(["flatten", "--symbol", "DEMO", "--qty", "0", "--orders", self.orders])[0], 1)
        self.assertEqual(run(["flatten", "--symbol", "DEMO", "--qty", "54.5", "--orders", self.orders])[0], 1)

    def test_an_edited_order_is_not_ready(self):
        self.run_risk()
        with open(self.orders) as fh:
            doc = json.load(fh)
        doc["orders"][0]["quantity"] = 55
        with open(self.orders, "w") as fh:
            json.dump(doc, fh)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 1)
        self.assertIn("was not in the last risk check", text)

    def test_yesterdays_check_does_not_count(self):
        self.run_risk(now="2026-10-16 10:00")
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("not today", text)

    def test_blocked_orders_are_never_ready(self):
        self.assertEqual(self.run_risk(now="2026-10-19 09:20"), 1)
        code, text, _ = run(self.args("show", "--index", "1", now="2026-10-19 09:20"))
        self.assertIn("BLOCKED by trader-risk: window: no new entries before 09:30", text)
        code, _, _ = run(self.args("approve", "--index", "1", "--reply", "yes", now="2026-10-19 09:20"))
        self.assertEqual(code, 1)

    def test_result_needs_an_approval_and_decline_is_logged(self):
        self.run_risk()
        code, _, err = run(self.args("result", "--index", "1", "--status", "placed"))
        self.assertEqual(code, 1)
        self.assertIn("no yes recorded", err)
        code, text, _ = run(self.args("decline", "--index", "1", "--reply", "no thanks"))
        self.assertEqual(code, 0)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("DONE: declined", text)

    def test_fill_sell_relieves_the_oldest_lot(self):
        pf = {"cash": 0, "positions": [{"ticker": "ITC", "shares": 5, "lots": [
            {"id": "ITC-1", "date": "2025-01-02", "shares": 3, "cost": 1200.0},
            {"id": "ITC-2", "date": "2026-01-02", "shares": 2, "cost": 840.0}]}]}
        with open(self.pf, "w") as fh:
            json.dump(pf, fh)
        code, text, _ = run(["fill", "--symbol", "ITC", "--side", "sell", "--qty", "4", "--price", "412",
                             "--date", TODAY, "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
        self.assertEqual(code, 0)
        self.assertIn("Cash now Rs 1,648", text)
        with open(self.pf) as fh:
            pf = json.load(fh)
        self.assertEqual(pf["cash"], 1648.0)
        lots = pf["positions"][0]["lots"]
        self.assertEqual([l["id"] for l in lots], ["ITC-2"])
        self.assertEqual((lots[0]["shares"], lots[0]["cost"]), (1, 420.0))
        self.assertEqual(self.state_doc()["orders"][-1]["status"], "filled")
        code, _, err = run(["fill", "--symbol", "ITC", "--side", "sell", "--qty", "5", "--price", "412",
                            "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)
        code, _, err = run(["fill", "--symbol", "ITC.BO", "--side", "buy", "--qty", "1", "--price", "412",
                            "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
        self.assertEqual(code, 1)
        self.assertIn("BSE listing", err)


if __name__ == "__main__":
    unittest.main()
