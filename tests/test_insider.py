"""trader-insider tests. Fixtures in tests/fixtures/trader-insider/, all SYNTHETIC
(made-up companies and people, written by hand in the shapes NSE serves; not market data):

nse-2026-10-19-synthetic/pit.json         an NSE corporates-pit answer: a promoter's market buy,
                                          a director's market sale, a promoter-group pledge, a
                                          promoter's pledge release, an employee's ESOP, a
                                          relative's inter-se transfer (no "did") and a KMP sale
                                          disclosed 9 trading days late (across Gandhi Jayanti).
nse-2026-10-19-synthetic/bulk.csv         NSE's archive bulk.csv for 19-Oct-2026 (one quantity
                                          written "6,10,000"), block.csv the same for block deals.
nse-2026-10-19-synthetic/*_deals.json     NSE's historical bulk/block answers, 14 to 19 Oct.
block-none-synthetic.csv                  an archive file on a day with no deals.
nse-403-synthetic.html                    the "Access Denied" page NSE's bot protection sends.
"""

import datetime
import io
import json
import os
import tempfile
import unittest
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load

ins = load("trader-insider", "insider")
DIR = os.path.join(FIX, "trader-insider")
NSE = os.path.join(DIR, "nse-2026-10-19-synthetic")


def read(name, folder=NSE):
    with open(os.path.join(folder, name), encoding="utf-8") as fh:
        return fh.read()


def pit():
    return ins.pit_rows(json.loads(read("pit.json")))


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ins.main(args)
    return code, out.getvalue(), err.getvalue()


def by_person(rows):
    return {r["person"]: r for r in rows}


class Calendar(unittest.TestCase):
    def test_trading_days_skip_weekends_and_nse_holidays(self):
        d = datetime.date
        self.assertEqual(ins.trading_days_after(d(2026, 10, 14), d(2026, 10, 16)), 2)
        self.assertEqual(ins.trading_days_after(d(2026, 10, 16), d(2026, 10, 19)), 1)     # a weekend
        self.assertEqual(ins.trading_days_after(d(2026, 10, 1), d(2026, 10, 5)), 1)       # Gandhi Jayanti
        self.assertEqual(ins.trading_days_after(d(2026, 10, 19), d(2026, 10, 21)), 1)     # Dussehra
        self.assertEqual(ins.trading_days_after(d(2026, 10, 19), d(2026, 10, 19)), 0)
        self.assertEqual(ins.trading_days_after(d(2026, 11, 6), d(2026, 11, 9)), 2)       # Muhurat Sunday

    def test_dates_in_every_nse_shape(self):
        self.assertEqual(ins.day_of("16-Oct-2026 18:20"), "2026-10-16")
        self.assertEqual(ins.day_of("01-DEC-2025"), "2025-12-01")
        self.assertEqual(ins.day_of("19-10-2026"), "2026-10-19")
        self.assertEqual(ins.day_of("2026-10-19T14:52:13"), "2026-10-19")
        self.assertIsNone(ins.day_of("-"))
        self.assertIsNone(ins.day_of("31-Feb-2026"))


class InsiderTrades(unittest.TestCase):
    def setUp(self):
        self.rows = pit()
        self.who = by_person(self.rows)

    def test_seven_disclosures_newest_first(self):
        self.assertEqual(len(self.rows), 7)
        self.assertEqual([r["disclosed"] for r in self.rows[:2]], ["2026-10-19 10:05", "2026-10-19 09:40"])
        self.assertEqual(self.rows[-1]["person"], "Arun Placeholder")

    def test_the_promoter_market_buy(self):
        r = self.who["Asha Demo"]
        self.assertEqual((r["symbol"], r["category"], r["action"], r["mode"]),
                         ("DEMO", "promoter", "buy", "market purchase"))
        self.assertEqual((r["shares"], r["value_inr"], r["price"]), (25000, 34012500.0, 1360.5))
        self.assertEqual((r["before_pct"], r["after_pct"]), (41.2, 41.25))
        self.assertEqual((r["before_shares"], r["after_shares"]), (41200000.0, 41225000.0))
        self.assertEqual((r["from"], r["to"], r["intimated"]), ("2026-10-13", "2026-10-14", "2026-10-15"))
        self.assertEqual((r["days_to_disclose"], r["insider_days"], r["company_days"]), (2, 1, 1))
        self.assertFalse(r["late"])
        self.assertTrue(r["highlight"] and r["key_person"] and r["open_market"])
        self.assertEqual(r["id"], "pit:9001")
        self.assertTrue(r["url"].endswith("IT_SYNTHETIC_9001.xml"))

    def test_categories_actions_and_modes_in_plain_words(self):
        w = self.who
        self.assertEqual((w["Ravi Kumar Sample"]["category"], w["Ravi Kumar Sample"]["action"],
                          w["Ravi Kumar Sample"]["mode"]), ("director", "sell", "market sale"))
        self.assertEqual(w["Sample Holdings Private Limited"]["category"], "promoter group")
        self.assertEqual(w["Arun Placeholder"]["category"], "KMP")
        self.assertEqual((w["Neha Example"]["category"], w["Neha Example"]["mode"]), ("designated employee", "ESOP"))
        self.assertEqual((w["Kiran Demo"]["category"], w["Kiran Demo"]["mode"]), ("relative", "inter-se transfer"))
        self.assertEqual(w["Vikram Sample"]["action"], "pledge revoked")

    def test_only_key_people_in_the_open_market_are_highlighted(self):
        hi = sorted(r["person"] for r in self.rows if r["highlight"])
        self.assertEqual(hi, ["Arun Placeholder", "Asha Demo", "Ravi Kumar Sample"])
        self.assertFalse(self.who["Neha Example"]["highlight"])       # ESOP, an employee
        self.assertFalse(self.who["Kiran Demo"]["highlight"])         # off market, a relative

    def test_a_pledge_is_not_a_trade(self):
        r = self.who["Sample Holdings Private Limited"]
        self.assertEqual((r["action"], r["pledge"], r["value_inr"], r["shares"]), ("pledge", True, None, 500000))
        self.assertFalse(r["highlight"])
        line = ins.describe_pit(r)
        self.assertIn("pledged 5,00,000 shares, a pledge, not a trade", line)
        self.assertIn("dated 2026-10-16", line)
        self.assertNotIn("Rs ", line)

    def test_a_slow_disclosure_is_flagged_with_who_was_slow(self):
        r = self.who["Arun Placeholder"]
        self.assertEqual((r["to"], r["intimated"], r["disclosed"]), ("2026-09-29", "2026-10-09", "2026-10-13 17:45"))
        self.assertEqual((r["days_to_disclose"], r["insider_days"], r["company_days"]), (9, 7, 2))
        self.assertTrue(r["late"])
        line = ins.describe_pit(r)
        self.assertIn("9 trading days after the trade (SLOW: SEBI allows 4; the insider took 7 to tell the company)",
                      line)

    def test_a_row_without_an_id_gets_a_stable_one(self):
        r = self.who["Kiran Demo"]
        self.assertTrue(r["id"].startswith("pit:") and len(r["id"]) == 20)
        self.assertEqual(r["id"], by_person(pit())["Kiran Demo"]["id"])
        self.assertEqual(r["url"], ins.PIT_PAGE)

    def test_defensive_parsing(self):
        rows = ins.pit_rows([{"symbol": "demo", "acqName": "  A   Person ", "personCategory": "Promoters",
                              "secAcq": "1,000", "secVal": "Nil", "tdpTransactionType": "Acquisition",
                              "acqMode": "Market Purchase"}, "not a row", {}])
        self.assertEqual(len(rows), 2)
        r = rows[0] if rows[0]["symbol"] else rows[1]
        self.assertEqual((r["symbol"], r["person"], r["shares"], r["value_inr"], r["price"]),
                         ("DEMO", "A Person", 1000, None, None))
        self.assertEqual((r["action"], r["days_to_disclose"], r["late"]), ("buy", None, False))
        with self.assertRaises(ins.InsiderError):
            ins.pit_rows({"data": "nope"})

    def test_filters(self):
        rows = self.rows
        self.assertEqual(len(ins.select_pit(rows, symbols={"DEMO"})), 4)
        insiders = ins.select_pit(rows, categories=ins.categories_wanted(["insiders"]))
        self.assertEqual(len(insiders), 5)
        promoters = ins.select_pit(rows, categories=ins.categories_wanted(["promoters"]))
        self.assertEqual(sorted(r["person"] for r in promoters),
                         ["Asha Demo", "Sample Holdings Private Limited", "Vikram Sample"])
        self.assertEqual(len(ins.select_pit(rows, market_only=True)), 3)
        self.assertEqual(len(ins.select_pit(rows, action="pledge")), 2)
        self.assertEqual(len(ins.select_pit(rows, action="sell")), 3)
        self.assertIsNone(ins.categories_wanted(["all"]))
        with self.assertRaises(ins.InsiderError):
            ins.categories_wanted(["senator"])

    def test_render_says_public_and_flags_your_stocks(self):
        out = ins.render_pit(self.rows, mine={"DEMO"})
        self.assertIn("** DEMO: Asha Demo (promoter) bought 25,000 shares, market purchase, "
                      "Rs 3,40,12,500 (3.40 crore), about 1360.50 a share, holding 41.20% -> 41.25%", out)
        self.assertIn("<- you hold DEMO", out)
        self.assertNotIn("<- you hold TESTMETAL", out)
        self.assertIn("Not inside information", out)
        self.assertIn("not a buy or sell signal", out)
        self.assertNotIn(chr(0x2014), out)


class Deals(unittest.TestCase):
    def test_todays_bulk_csv(self):
        rows = ins.deals_from_csv(read("bulk.csv"), "bulk")
        self.assertEqual([(r["symbol"], r["side"], r["qty"]) for r in rows],
                         [("DEMO", "buy", 650000), ("DEMO", "sell", 610000), ("TESTMETAL", "buy", 300000),
                          ("TINYCO", "buy", 120000)])
        first = rows[0]
        self.assertEqual((first["date"], first["client"], first["price"], first["value_inr"], first["remarks"]),
                         ("2026-10-19", "EXAMPLE GROWTH FUND LLP", 1362.15, 885397500.0, None))
        self.assertEqual(ins.describe_deal(first),
                         "BULK 2026-10-19 DEMO: EXAMPLE GROWTH FUND LLP bought 6,50,000 shares at 1362.15 "
                         "= Rs 88,53,97,500 (88.54 crore)")

    def test_block_csv_and_an_empty_day(self):
        rows = ins.deals_from_csv(read("block.csv"), "block")
        self.assertEqual([(r["kind"], r["side"], r["value_inr"]) for r in rows],
                         [("block", "sell", 2030875000.0), ("block", "buy", 2030875000.0)])
        self.assertEqual(ins.deals_from_csv(read("block-none-synthetic.csv", DIR), "block"), [])
        with self.assertRaises(ins.InsiderError):
            ins.deals_from_csv("<html>nope</html>", "bulk")

    def test_history_json(self):
        rows = ins.sort_deals(ins.deals_from_json(json.loads(read("bulk_deals.json")), "bulk"))
        self.assertEqual([r["date"] for r in rows], ["2026-10-19", "2026-10-16", "2026-10-15"])
        self.assertEqual(rows[1]["value_inr"], 567462000.0)
        # the same deal from the CSV and from the history has the same id, so it alerts once
        csv_rows = ins.deals_from_csv(read("bulk.csv"), "bulk")
        self.assertEqual(rows[0]["id"], csv_rows[0]["id"])

    def test_filters_and_render(self):
        rows = ins.deals_from_csv(read("bulk.csv"), "bulk")
        self.assertEqual(len(ins.select_deals(rows, min_value_cr=10)), 3)
        self.assertEqual(len(ins.select_deals(rows, symbols={"TINYCO"})), 1)
        out = ins.render_deals(rows, mine={"TESTMETAL"})
        self.assertIn("Rs 54,36,000 (54.36 lakh)", out)
        self.assertIn("SAMPLE TRADING CO bought 3,00,000 shares at 405.20 = Rs 12,15,60,000 (12.16 crore)"
                      "  <- you hold TESTMETAL", out)
        self.assertIn("not why", out)


class Money(unittest.TestCase):
    def test_indian_grouping(self):
        self.assertEqual(ins.inr(34012500), "Rs 3,40,12,500")
        self.assertEqual(ins.count(2500000), "25,00,000")
        self.assertEqual(ins.amount(120000), "Rs 1,20,000 (1.20 lakh)")
        self.assertEqual(ins.amount(99999), "Rs 99,999")
        self.assertEqual(ins.nse_symbol("NSE:demo"), "DEMO")
        with self.assertRaises(ins.InsiderError):
            ins.nse_symbol("DEMO.BO")


class NewSinceLastCheck(unittest.TestCase):
    def base(self, tmp, *more):
        return ["new", "--offline", NSE, "--today", "2026-10-19", "--state", os.path.join(tmp, "s", "insider.json"),
                "--portfolio", os.path.join(tmp, "none.json")] + list(more)

    def test_alerts_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = run(self.base(tmp))
            self.assertEqual(code, 0)
            # the last 3 days (17 to 19 Oct): the director's sale and the promoter-group pledge
            self.assertIn("Ravi Kumar Sample (director) sold 8,000 shares", out)
            self.assertIn("NEW insider pledge", out)
            self.assertNotIn("Asha Demo", out)                  # disclosed on the 16th
            # deals: both block trades and the bulk deals over Rs 10 crore, not TINYCO's
            self.assertEqual(out.count("NEW block deal"), 2)
            self.assertEqual(out.count("NEW bulk deal"), 3)
            self.assertNotIn("TINYCO", out)
            self.assertIn("Not inside information", out)
            with open(os.path.join(tmp, "s", "insider.json")) as fh:
                saved = json.load(fh)
            self.assertEqual(len(saved["seen"]), 7)
            self.assertTrue(all(v == "2026-10-19" for v in saved["seen"].values()))
            code, out, _ = run(self.base(tmp))
            self.assertEqual(code, 1)
            self.assertIn("Nothing new", out)

    def test_all_and_a_longer_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = run(self.base(tmp, "--all", "--days", "10", "--dry-run", "--json"))
            doc = json.loads(out)
            self.assertEqual(len(doc["insider"]), 7)
            self.assertEqual(len(doc["deals"]), 6)
            self.assertFalse(os.path.exists(os.path.join(tmp, "s", "insider.json")))

    def test_your_stocks_always_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            pf = os.path.join(tmp, "portfolio.json")
            with open(pf, "w") as fh:
                json.dump({"cash": 1000, "positions": [{"ticker": "TINYCO", "shares": 10}]}, fh)
            args = self.base(tmp)
            args[args.index("--portfolio") + 1] = pf
            code, out, _ = run(args)
            self.assertIn("SMALL INVESTOR HUF bought 1,20,000 shares at 45.30 = Rs 54,36,000 (54.36 lakh)"
                          "  <- you hold TINYCO", out)

    def test_a_new_row_is_new(self):
        rows = pit()
        state = {"seen": {r["id"]: "2026-10-16" for r in rows if r["person"] != "Ravi Kumar Sample"}}
        self.assertEqual([r["person"] for r in ins.unseen(rows, state)], ["Ravi Kumar Sample"])
        ins.remember(state, rows, datetime.date(2027, 3, 1))
        self.assertEqual(set(state["seen"].values()), {"2027-03-01"})   # 2026-10-16 is over 90 days old


class Network(unittest.TestCase):
    """No real network: _http is swapped for a fake that answers from the fixtures."""

    def setUp(self):
        self.calls, self.answers = [], []
        self._http, self._sleep = ins._http, ins.SLEEP
        self.slept = []
        ins.SLEEP = self.slept.append

        def fake(url, headers, opener):
            self.calls.append((url, dict(headers)))
            return self.answers.pop(0)
        ins._http = fake

    def tearDown(self):
        ins._http, ins.SLEEP = self._http, self._sleep

    def test_warm_up_then_the_api_with_browser_headers(self):
        self.answers = [(200, "text/html", b"<html>home</html>"),
                        (200, "application/json", read("pit.json").encode("utf-8"))]
        rows = ins.load_pit(datetime.date(2026, 10, 13), datetime.date(2026, 10, 19), "DEMO")
        self.assertEqual(len(rows), 7)
        (home, h1), (api, h2) = self.calls
        self.assertEqual(home, "https://www.nseindia.com/")
        url = urllib.parse.urlparse(api)
        self.assertEqual(url.path, "/api/corporates-pit")
        self.assertEqual(urllib.parse.parse_qs(url.query),
                         {"index": ["equities"], "from_date": ["13-10-2026"], "to_date": ["19-10-2026"],
                          "symbol": ["DEMO"]})
        self.assertIn("Mozilla/5.0", h2["User-Agent"])
        self.assertEqual(h2["Referer"], ins.PIT_PAGE)
        self.assertIn("application/json", h2["Accept"])
        self.assertEqual(self.slept, [1.0])                   # a pause between the two calls

    def test_bot_protection_is_a_plain_refusal_with_the_page(self):
        page = read("nse-403-synthetic.html", DIR).encode("utf-8")
        self.answers = [(200, "text/html", b"<html>home</html>"), (403, "text/html", page)]
        with self.assertRaises(ins.InsiderError) as cm:
            ins.load_pit(datetime.date(2026, 10, 13), datetime.date(2026, 10, 19))
        msg = str(cm.exception)
        self.assertIn("NSE refused the script (HTTP 403)", msg)
        self.assertIn(ins.PIT_PAGE, msg)
        self.assertEqual(len(self.calls), 2)                  # no retry loop

    def test_html_with_status_200_is_a_refusal_too(self):
        self.answers = [(200, "text/html", b"<html>home</html>"), (200, "text/html", b"<html>challenge</html>")]
        with self.assertRaises(ins.InsiderError) as cm:
            ins.load_pit(datetime.date(2026, 10, 13), datetime.date(2026, 10, 19))
        self.assertIn("sent a web page, not data", str(cm.exception))

    def test_the_archive_csv_needs_no_warm_up(self):
        self.answers = [(200, "text/csv", read("bulk.csv").encode("utf-8"))]
        rows = ins.load_deals("bulk")
        self.assertEqual(len(rows), 4)
        self.assertEqual(self.calls[0][0], "https://nsearchives.nseindia.com/content/equities/bulk.csv")
        self.answers = [(404, "text/html", b"")]
        with self.assertRaises(ins.InsiderError) as cm:
            ins.load_deals("block")
        self.assertIn("no block.csv right now", str(cm.exception))

    def test_history_api_params(self):
        self.answers = [(200, "text/html", b""), (200, "application/json", read("block_deals.json").encode())]
        rows = ins.load_deals("block", datetime.date(2026, 10, 13), datetime.date(2026, 10, 19))
        self.assertEqual(len(rows), 1)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.calls[1][0]).query)
        self.assertEqual(q, {"optionType": ["block_deals"], "from": ["13-10-2026"], "to": ["19-10-2026"]})
        self.assertEqual(self.calls[1][1]["Referer"], ins.DEALS_PAGE)

    def test_new_carries_on_when_nse_refuses_the_insider_api(self):
        self.answers = [(403, "text/html", b"denied"),
                        (200, "text/csv", read("bulk.csv").encode()), (200, "text/csv", read("block.csv").encode())]
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = run(["new", "--today", "2026-10-19", "--state", os.path.join(tmp, "s.json"),
                                "--portfolio", os.path.join(tmp, "none.json")])
        self.assertEqual(code, 0)
        self.assertIn("NOTE insider trades: NSE refused the script (HTTP 403)", out)
        self.assertIn("NEW block deal", out)

    def test_every_source_failing_is_an_error(self):
        self.answers = [(403, "text/html", b""), (403, "text/html", b""), (403, "text/html", b"")]
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run(["new", "--today", "2026-10-19", "--state", os.path.join(tmp, "s.json")])
        self.assertEqual(code, 2)
        self.assertIn("every source failed", err)


class Cli(unittest.TestCase):
    def test_pit_filters_and_exit_codes(self):
        code, out, _ = run(["pit", "--offline", NSE, "--today", "2026-10-19", "--symbol", "demo",
                            "--market-only", "--portfolio", "/nonexistent"])
        self.assertEqual(code, 0)
        self.assertIn("Insider trades disclosed 2026-10-13 to 2026-10-19 for DEMO:", out)
        self.assertEqual(out.count("** DEMO"), 2)
        code, out, _ = run(["pit", "--offline", NSE, "--today", "2026-10-19", "--symbol", "NOSUCH",
                            "--portfolio", "/nonexistent"])
        self.assertEqual(code, 1)
        self.assertIn("No matching insider disclosures", out)

    def test_pit_window_and_json(self):
        code, out, _ = run(["pit", "--offline", NSE, "--from", "2026-09-01", "--to", "2026-10-19", "--json",
                            "--category", "kmp", "--portfolio", "/nonexistent"])
        rows = json.loads(out)
        self.assertEqual([r["person"] for r in rows], ["Arun Placeholder"])
        code, _, err = run(["pit", "--offline", NSE, "--from", "2026-10-19", "--to", "2026-10-01"])
        self.assertEqual(code, 2)
        self.assertIn("is after --to", err)

    def test_deals_today_and_history(self):
        code, out, _ = run(["deals", "--offline", NSE, "--kind", "block", "--portfolio", "/nonexistent"])
        self.assertEqual(code, 0)
        self.assertIn("Block deals, the latest session", out)
        self.assertEqual(out.count("BLOCK 2026-10-19 SAMPLEFIN"), 2)
        code, out, _ = run(["deals", "--offline", NSE, "--from", "2026-10-13", "--to", "2026-10-19",
                            "--min-value-cr", "50", "--json", "--portfolio", "/nonexistent"])
        self.assertEqual([(r["kind"], r["date"]) for r in json.loads(out)],
                         [("bulk", "2026-10-19"), ("bulk", "2026-10-16"), ("block", "2026-10-14")])

    def test_holdings_needs_a_portfolio(self):
        code, _, err = run(["pit", "--offline", NSE, "--holdings", "--portfolio", "/nonexistent"])
        self.assertEqual(code, 2)
        self.assertIn("no positions", err)
        code, _, err = run(["pit", "--offline", NSE, "--symbol", "RELIANCE.BO"])
        self.assertEqual(code, 2)
        self.assertIn("BSE listing", err)


if __name__ == "__main__":
    unittest.main()
