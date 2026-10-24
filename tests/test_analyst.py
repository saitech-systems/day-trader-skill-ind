"""trader-analyst tests. Fixtures in tests/fixtures/trader-analyst/ are SYNTHETIC (not
real company results), made by make_synthetic.py there in the shapes nseindia.com's
JSON answers with (amounts as strings, in Rs lakh):

results-DEMO.json, financial-results-DEMO.json, board-meetings-DEMO.json
    Demo Industries: five quarters to 2026-06-30 (Q1 FY27), its filings, its board meetings
results-DEMOBANK.json     a bank (interest earned, total income, gross NPA)
results-LAGCO.json + financial-results-LAGCO.json
    the results comparison stops at 2026-03-31 while the June quarter is filed
results-EMPTY.json        no rows;  results-BLOCKED.json  NSE's "Access Denied" page
"""

import datetime
import io
import json
import os
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load

facts = load("trader-analyst", "facts")
DIR = os.path.join(FIX, "trader-analyst")
NOW = ["--now", "2026-10-19 10:15"]


def doc(name):
    with open(os.path.join(DIR, name)) as fh:
        return json.load(fh)


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = facts.main(args)
    return code, out.getvalue(), err.getvalue()


class Demo(unittest.TestCase):
    def setUp(self):
        self.r = facts.analyse(doc("results-DEMO.json"))

    def test_latest_quarter(self):
        q = self.r["rows"][0]
        self.assertEqual((q["end"], q["start"], q["quarter"]), ("2026-06-30", "2026-04-01", "Q1 FY27"))
        self.assertFalse(q["audited"])
        self.assertEqual(q["revenue"], 10215.0)       # Rs lakh 10,21,500 is Rs 10,215 crore
        self.assertEqual(q["pbt"], 1747.0)
        self.assertEqual(q["net_profit"], 1305.0)
        self.assertEqual((q["eps_basic"], q["eps_diluted"]), (19.58, 19.5))
        self.assertEqual(q["revenue_yoy"], 12.0)        # vs 9,124 in Q1 FY26
        self.assertEqual(q["net_profit_yoy"], 35.4)
        self.assertEqual(q["eps_yoy"], 35.3)            # vs 14.41
        self.assertEqual(q["revenue_qoq"], -2.4)
        self.assertEqual((q["pbt_margin"], q["operating_margin"], q["net_margin"]), (17.1, 13.7, 12.8))
        self.assertEqual(q["other_income_share"], 26.8)
        self.assertFalse(self.r["bank"])
        self.assertEqual(self.r["basis"], "standalone")

    def test_india_financial_year_labels(self):
        self.assertEqual([r["quarter"] for r in self.r["rows"]],
                         ["Q1 FY27", "Q4 FY26", "Q3 FY26", "Q2 FY26", "Q1 FY26"])
        self.assertEqual(facts.fy_quarter(datetime.date(2026, 3, 31)), "Q4 FY26")
        self.assertEqual(facts.fy_quarter(datetime.date(2025, 12, 31)), "Q3 FY26")

    def test_only_quarters_with_a_year_before_get_growth(self):
        self.assertEqual([r["revenue_yoy"] for r in self.r["rows"]], [12.0, None, None, None, None])
        self.assertIsNone(self.r["rows"][-1]["revenue_qoq"])

    def test_trailing_twelve_months(self):
        self.assertEqual(self.r["ttm"], {"revenue": 40182.0, "net_profit": 4427.0, "eps_diluted": 66.14})

    def test_quarters_flag(self):
        r = facts.analyse(doc("results-DEMO.json"), quarters=2)
        self.assertEqual(len(r["rows"]), 2)
        self.assertEqual(r["ttm"]["revenue"], 40182.0)      # the TTM still reads all four

    def test_what_changed_in_words(self):
        lines = facts.changes(self.r)
        self.assertEqual(lines[0], "Quarter ending 2026-06-30 (Q1 FY27, unaudited): revenue Rs 10,215.0 crore "
                                   "(+12.0% vs a year earlier), net profit Rs 1,305.0 crore (+35.4%), diluted EPS "
                                   "Rs 19.50 (+35.3%).")
        self.assertIn("Profit before tax margin 17.1% (+3.7 points vs the quarter before).", lines)
        self.assertTrue(any(l.startswith("Other income is 26.8% of profit before tax (Rs 468.0 crore)")
                            for l in lines))
        self.assertTrue(any(l.startswith("Revenue -2.4% vs the quarter before") for l in lines))

    def test_one_offs_are_flagged(self):
        q4 = facts.analyse({"resCmpData": [r for r in doc("results-DEMO.json")["resCmpData"]
                                           if r["re_to_dt"] != "30-JUN-2026"]})
        self.assertEqual(q4["rows"][0]["end"], "2026-03-31")
        self.assertEqual(q4["rows"][0]["exceptional"], -125.0)
        self.assertTrue(q4["rows"][0]["audited"])
        self.assertTrue(any("Exceptional items of Rs -125.0 crore" in l for l in facts.changes(q4)))
        credit = {"rows": [dict(self.r["rows"][0], net_profit=2000.0, other_income_share=5.0, prev_end=None)],
                  "bank": False}
        self.assertTrue(any("above profit before tax" in l for l in facts.changes(credit)))
        quiet = {"rows": [dict(self.r["rows"][0], other_income_share=12.0, prev_end=None)], "bank": False}
        self.assertFalse(any("Other income" in l for l in facts.changes(quiet)))

    def test_a_restated_quarter_keeps_the_latest_filing(self):
        d = doc("results-DEMO.json")
        again = dict(d["resCmpData"][0], re_net_sale="1025000", re_seq_num="6900999")
        older = dict(d["resCmpData"][0], re_net_sale="1", re_seq_num="1")
        r = facts.analyse({"resCmpData": [older, again] + d["resCmpData"][1:]})
        self.assertEqual(r["rows"][0]["revenue"], 10250.0)
        self.assertEqual(len(r["rows"]), 5)


class Bank(unittest.TestCase):
    def test_a_bank_reads_total_income_and_has_no_operating_margin(self):
        r = facts.analyse(doc("results-DEMOBANK.json"))
        self.assertTrue(r["bank"])
        q = r["rows"][0]
        self.assertEqual(q["revenue"], 6652.0)          # interest earned 5,603 + other income 1,049
        self.assertIsNone(q["operating_margin"])
        self.assertIsNone(q["other_income_share"])
        self.assertEqual((q["pbt_margin"], q["net_margin"]), (21.0, 15.8))
        self.assertEqual(q["gross_npa_pct"], 2.12)
        lines = facts.changes(r)
        self.assertTrue(lines[0].startswith("Quarter ending 2026-06-30 (Q1 FY27, unaudited): total income "
                                            "Rs 6,652.0 crore (+9.0% vs a year earlier)"))
        self.assertIn("Gross NPA 2.12% of advances.", lines)
        self.assertFalse(any(l.startswith("Operating margin") for l in lines))

    def test_a_bank_is_known_by_its_rows_too(self):
        d = doc("results-DEMOBANK.json")
        self.assertTrue(facts.is_bank({"bankNonBnking": None}, d["resCmpData"]))
        self.assertFalse(facts.is_bank({"bankNonBnking": "N"}, doc("results-DEMO.json")["resCmpData"]))


class Filings(unittest.TestCase):
    def test_latest_results_filings(self):
        f = facts.latest_filings(doc("financial-results-DEMO.json"))
        self.assertEqual((f["end"], f["relating"], f["name"]), ("2026-06-30", "First Quarter",
                                                                "Demo Industries Limited"))
        self.assertEqual(f["consolidated"]["filed"], "2026-07-23 18:31")
        self.assertEqual(f["standalone"]["filed"], "2026-07-23 18:28")
        self.assertTrue(f["consolidated"]["xbrl"].endswith("INDAS_300101_SYNTHETIC.xml"))
        self.assertEqual(f["standalone"]["audited"], "Un-Audited")
        self.assertIsNone(facts.latest_filings([]))
        self.assertEqual(facts.latest_filings({"data": doc("financial-results-DEMO.json")})["end"], "2026-06-30")

    def test_next_results_board_meeting(self):
        meetings = doc("board-meetings-DEMO.json")
        n = facts.next_results(meetings, datetime.date(2026, 10, 19))
        self.assertEqual((n["date"], n["weekday"]), ("2026-10-22", "Thursday"))
        self.assertIn("Unaudited Financial Results for the quarter ended September 30, 2026", n["purpose"])
        self.assertEqual(n["announced"], "2026-10-16 17:45")
        # a fund-raising meeting is not a results date, and a past meeting is not next
        self.assertIsNone(facts.next_results(meetings, datetime.date(2026, 10, 23)))
        self.assertEqual(facts.next_results(meetings, datetime.date(2026, 8, 1))["date"], "2026-10-22")

    def test_dates_and_amounts_the_way_nse_writes_them(self):
        self.assertEqual(facts.nse_date("31-DEC-2024"), datetime.date(2024, 12, 31))
        self.assertEqual(facts.nse_date("16-Jan-2025 20:20"), datetime.date(2025, 1, 16))
        self.assertIsNone(facts.nse_date("-"))
        self.assertEqual(facts.num("872100"), 872100.0)
        self.assertIsNone(facts.num("-"))
        self.assertIsNone(facts.num(None))
        self.assertEqual(facts.grouped(4018200 / 100.0), "40,182.0")
        self.assertEqual(facts.grouped(1234567.25, 2), "12,34,567.25")
        self.assertEqual(facts.grouped(-125.0), "-125.0")


class LaggingData(unittest.TestCase):
    def test_says_when_nse_results_trail_a_filed_quarter(self):
        code, out, err = run(["LAGCO", "--offline", DIR] + NOW)
        self.assertEqual(code, 0, err)
        self.assertIn("Lagging Company Limited (LAGCO)", out)
        self.assertIn("NOTE: NSE's results comparison stops at 2026-03-31, but results for the quarter ending "
                      "2026-06-30 were filed 2026-07-29 17:15. Read the filing directly: https://nsearchives."
                      "nseindia.com/corporate/xbrl/INDAS_300301_SYNTHETIC.xml", out)


class Cli(unittest.TestCase):
    def test_the_story(self):
        # synthetic test data: DEMO on Monday 2026-10-19, results due Thursday
        code, out, err = run(["DEMO", "--offline", DIR] + NOW)
        self.assertEqual(code, 0, err)
        self.assertIn("Demo Industries Limited (DEMO). Rs crore except EPS. Standalone results from NSE", out)
        self.assertIn("Q1 FY27  2026-06-30    10,215.0   +12.0%    -2.4%   17.1%   13.7%   12.8%    1,305.0", out)
        self.assertIn("Last 4 quarters: revenue Rs 40,182.0 crore, net profit Rs 4,427.0 crore, diluted EPS "
                      "Rs 66.14.", out)
        self.assertIn("The table is standalone. The consolidated (group) numbers are in the consolidated filing: "
                      "https://nsearchives.nseindia.com/corporate/xbrl/INDAS_300101_SYNTHETIC.xml", out)
        self.assertIn("Next results: board meeting on 2026-10-22 (Thursday, in 3 days)", out)
        self.assertNotIn("NOTE:", out)
        self.assertNotIn(chr(0x2014), out)

    def test_metrics_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "trader", "metrics.json")
            with open(os.path.join(tmp, "old.json"), "w") as fh:
                json.dump({"companies": [{"symbol": "OLDCO", "eps_ttm": 1.0}]}, fh)
            os.makedirs(os.path.dirname(path))
            os.replace(os.path.join(tmp, "old.json"), path)
            code, out, err = run(["DEMO", "DEMOBANK", "--offline", DIR, "--metrics", path] + NOW)
            self.assertEqual(code, 0, err)
            with open(path) as fh:
                data = json.load(fh)
        rows = {r["symbol"]: r for r in data["companies"]}
        self.assertEqual(sorted(rows), ["DEMO", "DEMOBANK", "OLDCO"])
        self.assertEqual(data["as_of"], "2026-10-19")
        self.assertEqual(rows["DEMO"]["eps_ttm"], 66.14)
        self.assertEqual(rows["DEMO"]["revenue_growth_yoy"], 12.0)
        self.assertEqual(rows["DEMO"]["next_results"], "2026-10-22")
        self.assertEqual(rows["DEMO"]["basis"], "standalone")
        self.assertIsNone(rows["DEMOBANK"]["operating_margin"])
        self.assertIn("wrote", out)

    def test_the_example_metrics_file_is_this_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "metrics.json")
            run(["DEMO", "DEMOBANK", "--offline", DIR, "--metrics", path] + NOW)
            with open(path) as fh:
                fresh = json.load(fh)
        with open(os.path.join(os.path.dirname(facts.__file__), "metrics.example.json")) as fh:
            example = json.load(fh)
        self.assertIn("Synthetic", example["note"])
        self.assertEqual(example["companies"], fresh["companies"])

    def test_unknown_symbol_bse_symbol_and_no_rows(self):
        code, _, err = run(["ZZZZ", "--offline", DIR] + NOW)
        self.assertEqual(code, 2)
        self.assertIn("ZZZZ: offline: no file", err)
        code, _, err = run(["EMPTY", "--offline", DIR] + NOW)
        self.assertEqual(code, 2)
        self.assertIn("NSE has no quarterly results for this symbol", err)
        code, _, err = run(["500325.BO"] + NOW)
        self.assertEqual(code, 2)
        self.assertIn("BSE listing", err)
        code, out, err = run(["DEMO", "ZZZZ", "--offline", DIR] + NOW)
        self.assertEqual(code, 1)                         # one worked, one did not

    def test_nse_refusal_is_said_plainly_and_ends_nse_calls(self):
        code, out, err = run(["BLOCKED", "DEMO", "--offline", DIR] + NOW)
        self.assertEqual(code, 2)
        self.assertIn("BLOCKED: NSE refused the script (a web page instead of data)", err)
        self.assertIn("https://www.nseindia.com/companies-listing/corporate-filings-financial-results", err)
        self.assertIn("DEMO: NSE refused the script", err)    # not asked again after the refusal

    def test_json_output(self):
        code, out, err = run(["DEMO", "--offline", DIR, "--json", "--quarters", "4"] + NOW)
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(len(data[0]["result"]["rows"]), 4)
        self.assertEqual(data[0]["result"]["basis"], "standalone")
        self.assertEqual(data[0]["next_results"]["date"], "2026-10-22")
        self.assertEqual(data[0]["filings"]["consolidated"]["filed"], "2026-07-23 18:31")
        self.assertIsNone(data[0]["note"])

    def test_no_filings(self):
        code, out, err = run(["DEMO", "--offline", DIR, "--no-filings"] + NOW)
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith("DEMO. Rs crore"))
        self.assertNotIn("Next results", out)


class Network(unittest.TestCase):
    """The live path, with the network swapped out."""

    def test_warm_up_browser_headers_and_refusal(self):
        f = facts.Fetcher(nse_pause=0)
        calls = []
        answers = [(b"<html>home</html>", "text/html"),
                   (json.dumps(doc("results-DEMO.json")).encode("utf-8"), "application/json"),
                   urllib.error.HTTPError("https://www.nseindia.com/api/x", 403, "Forbidden", {}, None)]

        def fake(url, headers):
            calls.append((url, headers))
            a = answers.pop(0)
            if isinstance(a, Exception):
                raise a
            return a

        f._open = fake
        d = f.json("results-comparision", {"symbol": "M&M"}, "unused")
        self.assertEqual(len(d["resCmpData"]), 5)
        self.assertEqual(calls[0][0], "https://www.nseindia.com/")
        self.assertEqual(calls[1][0], "https://www.nseindia.com/api/results-comparision?symbol=M%26M")
        self.assertEqual(calls[1][1]["Referer"], "https://www.nseindia.com/")
        self.assertIn("Chrome", calls[1][1]["User-Agent"])
        with self.assertRaises(facts.NseRefused) as ctx:
            f.json("corporate-board-meetings", {"index": "equities", "symbol": "M&M"}, "unused")
        self.assertIn("HTTP 403", str(ctx.exception))
        with self.assertRaises(facts.NseRefused):
            f.json("results-comparision", {"symbol": "TCS"}, "unused")
        self.assertEqual(len(calls), 3)


if __name__ == "__main__":
    unittest.main()
