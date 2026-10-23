"""trader-news tests. Fixtures in tests/fixtures/trader-news/ are SYNTHETIC (not real
news), made by make_synthetic.py there in the exact shapes the live sources send:

yahoo-DEMO.xml, yahoo-SAMPLE.xml     Yahoo Finance headline RSS for two made-up NSE
                                     companies, Monday 2026-10-19 morning (India time)
nse-announcements-DEMO.json          NSE corporate-announcements JSON (a list)
nse-announcements-SAMPLE.json        an empty list
nse-announcements-BLOCKED.json       the "Access Denied" page NSE's bot protection sends
chart-DEMO.json                      tests/fixtures/shared/DEMO-2026-10-19-5m-synthetic.json
chart-SAMPLE.json                    a one-bar Yahoo chart, 3.45 % under the previous close
"""

import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load

news = load("trader-news", "news")
DIR = os.path.join(FIX, "trader-news")
AT_1015 = "2026-10-19 10:15"            # India time
UTC = datetime.timezone.utc


def raw(name):
    with open(os.path.join(DIR, name), "rb") as fh:
        return fh.read()


def announcements():
    return news.parse_nse_announcements(json.loads(raw("nse-announcements-DEMO.json").decode("utf-8")), "DEMO")


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = news.main(args)
    return code, out.getvalue(), err.getvalue()


class Feeds(unittest.TestCase):
    def test_yahoo_rss(self):
        items = news.parse_rss(raw("yahoo-DEMO.xml"), "DEMO")
        self.assertEqual(len(items), 9)
        first = items[0]
        self.assertEqual(first["title"], "Demo Industries hits the day's high as the broader market extends gains")
        # 10:32 India time is 05:02 UTC
        self.assertEqual(first["time"], datetime.datetime(2026, 10, 19, 5, 2, tzinfo=UTC))
        self.assertTrue(first["link"].startswith("https://finance.yahoo.com/"))
        self.assertTrue(first["id"].startswith("rss:"))

    def test_nse_announcements(self):
        items = announcements()
        self.assertEqual(len(items), 6)
        by = {i["id"]: i for i in items}
        rating = by["nse:700000402"]
        self.assertEqual(rating["title"], "Demo Industries Limited filed with NSE: Credit Rating")
        self.assertEqual((rating["source"], rating["kind"], rating["category"]), ("NSE", "filing", "credit rating"))
        self.assertTrue(rating["important"])
        # 09:02:11 India time is 03:32:11 UTC
        self.assertEqual(rating["time"], datetime.datetime(2026, 10, 19, 3, 32, 11, tzinfo=UTC))
        self.assertTrue(rating["link"].startswith("https://nsearchives.nseindia.com/corporate/DEMO_"))
        cfo = by["nse:700000415"]
        self.assertEqual(cfo["category"], "director, officer or auditor change")
        self.assertTrue(cfo["important"])
        self.assertIn("(director, officer or auditor change)", cfo["title"])
        meeting = by["nse:700000377"]
        self.assertEqual(meeting["category"], "board meeting date")
        self.assertFalse(meeting["important"])
        # a generic "Updates" filing is read by its text: a clarification
        self.assertEqual(by["nse:700000431"]["category"], "clarification on price or volume")
        self.assertTrue(by["nse:700000431"]["important"])
        self.assertEqual(by["nse:700000105"]["category"], "financial results")
        self.assertEqual(by["nse:700000351"]["category"], "analyst or investor meet")
        self.assertFalse(by["nse:700000351"]["important"])

    def test_nse_categories(self):
        cat = news.nse_category
        self.assertEqual(cat("Outcome of Board Meeting")[:2], ("board meeting outcome", True))
        self.assertEqual(cat("Disclosure of Pledged Shares")[:2], ("pledge of shares", True))
        self.assertEqual(cat("Insolvency and Bankruptcy")[:2], ("default or insolvency", True))
        self.assertEqual(cat("Spurt in Volume")[:2], ("clarification on price or volume", True))
        self.assertEqual(cat("Acquisition")[:2], ("acquisition or merger", True))
        self.assertEqual(cat("Dividend")[:2], ("dividend, buyback or bonus", False))
        self.assertEqual(cat("Copy of Newspaper Publication"), (None, False, None))
        self.assertEqual(cat("Updates", "Resignation of the statutory auditor")[:2],
                         ("director, officer or auditor change", True))

    def test_nse_answers_wrapped_in_data(self):
        doc = {"data": json.loads(raw("nse-announcements-DEMO.json").decode("utf-8"))}
        self.assertEqual(len(news.parse_nse_announcements(doc, "DEMO")), 6)
        with self.assertRaises(news.NewsError):
            news.parse_nse_announcements({"error": "x"}, "DEMO")

    def test_times(self):
        self.assertEqual(news.parse_time("Mon, 19 Oct 2026 02:34:00 +0000").hour, 2)
        self.assertEqual(news.parse_time("2026-10-19T08:04:00+05:30").hour, 2)
        self.assertIsNone(news.parse_time("soon"))
        self.assertEqual(news.parse_nse_time("2026-10-19 09:02:11"),
                         datetime.datetime(2026, 10, 19, 3, 32, 11, tzinfo=UTC))
        self.assertEqual(news.parse_nse_time("19-Oct-2026 09:02:11"), news.parse_nse_time("2026-10-19 09:02:11"))
        self.assertIsNone(news.parse_nse_time("-"))
        self.assertEqual(news.parse_now(AT_1015), datetime.datetime(2026, 10, 19, 4, 45))

    def test_nse_symbols_on_yahoo(self):
        self.assertEqual(news.yahoo_symbol("reliance"), "RELIANCE.NS")
        self.assertEqual(news.yahoo_quote("M&M"), "M%26M.NS")
        self.assertEqual(news.nse_symbol("NSE:bajaj-auto"), "BAJAJ-AUTO")
        self.assertEqual(news.nse_symbol("TCS.NS"), "TCS")
        for bad in ("RELIANCE.BO", "BSE:500325", "TATA MOTORS", ""):
            with self.assertRaises(news.NewsError):
                news.nse_symbol(bad)


class Tagging(unittest.TestCase):
    WATCH = {"DEMO": "Demo Industries", "SAMPLE": "Sample Motors"}

    def test_touches_and_tags(self):
        items = news.tag(news.parse_rss(raw("yahoo-SAMPLE.xml"), "SAMPLE"), self.WATCH)
        by = {i["title"][:24]: i for i in items}
        market = by["Stock market today: Nift"]
        self.assertEqual(market["touches"], ["DEMO", "SAMPLE"])
        self.assertIn("macro", market["tags"])
        sebi = by["SEBI issues a show-cause"]
        self.assertEqual(sebi["touches"], ["SAMPLE"])
        self.assertEqual(sebi["tags"], ["legal", "regulatory"])
        self.assertIn("risk", by["Sample Motors recalls 12"]["tags"])
        self.assertIn("analyst", by["Sample Motors October sa"]["tags"])

    def test_nse_forms_of_a_symbol_count_as_a_mention(self):
        items = news.tag(news.parse_rss(raw("yahoo-DEMO.xml"), "DEMO"), {"DEMO": None, "SAMPLE": None})
        hit = [i for i in items if "DEMO" in i["touches"]]
        self.assertEqual(len(hit), 9)            # the DEMO feed itself
        lone = news.tag([{"id": "x", "source": "Yahoo Finance", "kind": "headline", "ticker": "OTHER",
                          "title": "Brokers like NSE: DEMO and DEMO.NS; (SAMPLE) too", "summary": "",
                          "link": "", "time": None}], {"DEMO": None, "SAMPLE": None})[0]
        self.assertEqual(lone["touches"], ["DEMO", "SAMPLE"])
        not_a_mention = news.tag([{"id": "y", "source": "Yahoo Finance", "kind": "headline", "ticker": "OTHER",
                                   "title": "A DEMOCRAT and NSE: DEMOX", "summary": "", "link": "",
                                   "time": None}], {"DEMO": None})[0]
        self.assertEqual(not_a_mention["touches"], [])

    def test_filings_carry_their_category_tag(self):
        items = {i["id"]: i for i in news.tag(announcements(), {"DEMO": None})}
        self.assertEqual(items["nse:700000402"]["tags"][0], "risk")        # credit rating
        self.assertEqual(items["nse:700000377"]["tags"][0], "earnings")    # results board meeting
        self.assertEqual(items["nse:700000415"]["tags"][0], "people")

    def test_dedupe_merges_the_same_story(self):
        a = news.parse_rss(raw("yahoo-DEMO.xml"), "DEMO")
        b = news.parse_rss(raw("yahoo-SAMPLE.xml"), "SAMPLE")
        items = news.dedupe(news.tag(a + b, {"DEMO": None, "SAMPLE": None}))
        self.assertEqual(len(items), 12)         # 9 + 4, one story in both feeds
        market = [i for i in items if i["title"].startswith("Stock market today")]
        self.assertEqual(len(market), 1)
        self.assertEqual(market[0]["touches"], ["DEMO", "SAMPLE"])

    def test_day_trading_tags(self):
        items = [{"id": "x", "source": "Yahoo Finance", "kind": "headline", "ticker": "DEMO",
                  "title": "DEMO hits lower circuit after SEBI order; GIFT Nifty slips before the RBI repo rate call",
                  "summary": "", "link": "", "time": None}]
        tags = news.tag(items, {"DEMO": None})[0]["tags"]
        for t in ("halt", "regulatory", "macro"):
            self.assertIn(t, tags)


class Check(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state", "news.json")
        self.files = ["--portfolio", os.path.join(self.tmp.name, "none.json"),
                      "--watchlist", os.path.join(self.tmp.name, "none.json"),
                      "--plan", os.path.join(self.tmp.name, "none.json")]
        self.base = ["--offline", DIR, "--state", self.state, "--now", AT_1015] + self.files

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_items_once_newest_first(self):
        code, out, err = run(["--tickers", "DEMO", "SAMPLE", "--hours", "6", "--json"] + self.base)
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        times = [i["time"] for i in data["items"]]
        self.assertEqual(times, sorted(times, reverse=True))
        # 7 DEMO headlines + 3 SAMPLE (one shared story) + 2 NSE filings, all before 10:15
        self.assertEqual(len(data["items"]), 12)
        self.assertEqual(sum(1 for i in data["items"] if i["source"] == "NSE"), 2)
        self.assertEqual(data["notes"], [])
        code, out, _ = run(["--tickers", "DEMO", "SAMPLE", "--hours", "6"] + self.base)
        self.assertEqual(code, 1)
        self.assertIn("Nothing new.", out)

    def test_the_story_at_1015(self):
        # synthetic test data: DEMO's morning, checked at 10:15 India time
        code, out, err = run(["--tickers", "DEMO", "--hours", "6", "--no-moves", "--dry-run"] + self.base)
        self.assertEqual(code, 0, err)
        self.assertIn("2026-10-19 08:04 IST  DEMO", out)
        self.assertNotIn("10:32 IST", out)       # published after the pinned 10:15 clock
        self.assertNotIn("10:40 IST", out)
        self.assertIn("! 2026-10-19 09:02 IST  DEMO        NSE           Demo Industries Limited filed with NSE: "
                      "Credit Rating", out)
        acquire = [l for l in out.splitlines() if "acquire a stake" in l]
        tags = out.splitlines()[out.splitlines().index(acquire[0]) + 1]
        self.assertIn("deal", tags)
        self.assertIn("Checked 2026-10-19 10:15 IST", out)
        lines = out.splitlines()
        self.assertEqual(len([l for l in lines if l.startswith("  2026-10-19")]), 7)
        self.assertEqual(len([l for l in lines if l.startswith("! 2026-10-19")]), 2)
        self.assertNotIn(chr(0x2014), out)

    def test_the_price_line(self):
        code, out, err = run(["--tickers", "DEMO", "SAMPLE", "--hours", "6", "--dry-run",
                              "--now", "2026-10-19 10:52"] + self.base[:4] + self.files)
        self.assertEqual(code, 0, err)
        self.assertIn("DEMO       1369.85 +1.29% vs the previous close 1352.40 at 10:49 IST  \n", out)
        self.assertIn("SAMPLE     412.35 -3.45% vs the previous close 427.10 at 10:49 IST  MOVING", out)
        # the 10:40 clarification is in by 10:52, read by its text
        self.assertIn("! 2026-10-19 10:40 IST  DEMO", out)
        self.assertIn("(clarification on price or volume)", out)

    def test_filings_show_in_a_wider_window(self):
        code, out, err = run(["--tickers", "DEMO", "--hours", "2400"] + self.base)
        self.assertEqual(code, 0, err)
        self.assertIn("! 2026-07-23 18:31 IST  DEMO", out)
        self.assertIn("Financial Result Updates (financial results)", out)
        self.assertIn("Not financial advice", out)
        self.assertNotIn(chr(0x2014), out)

    def test_tickers_from_watchlist_and_plan(self):
        wl = os.path.join(self.tmp.name, "watchlist.json")
        with open(wl, "w") as fh:
            json.dump({"created_by": "trader-scanner", "hits": [{"symbol": "SAMPLE", "setups": ["orb"]},
                                                               {"symbol": "not a symbol!"}]}, fh)
        plan = os.path.join(self.tmp.name, "plan.json")
        with open(plan, "w") as fh:
            json.dump({"created_by": "trader-exit", "symbol": "demo.ns"}, fh)
        self.assertEqual(sorted(news.watch_from_files("/nonexistent.json", wl, plan)), ["DEMO", "SAMPLE"])
        args = list(self.base)
        args[args.index("--watchlist") + 1] = wl
        code, out, err = run(["--json", "--dry-run", "--hours", "6"] + args)
        self.assertEqual(code, 0, err)
        tickers = {t for i in json.loads(out)["items"] for t in i["touches"]}
        self.assertEqual(tickers, {"SAMPLE"})
        self.assertFalse(os.path.exists(self.state))

    def test_no_tickers_or_a_bse_symbol_is_an_error(self):
        code, _, err = run(self.base)
        self.assertEqual(code, 2)
        self.assertIn("no tickers", err)
        code, _, err = run(["--tickers", "RELIANCE.BO"] + self.base)
        self.assertEqual(code, 2)
        self.assertIn("BSE listing", err)

    def test_nse_refusal_is_said_once_and_headlines_carry_on(self):
        code, out, err = run(["--tickers", "BLOCKED", "DEMO", "--hours", "6", "--no-moves", "--dry-run"] + self.base)
        self.assertEqual(code, 0, err)
        notes = [l for l in out.splitlines() if l.startswith("note: NSE")]
        self.assertEqual(len(notes), 1)
        self.assertIn("NSE refused the script (a web page instead of data)", notes[0])
        self.assertIn("https://www.nseindia.com/companies-listing/corporate-filings-announcements", notes[0])
        self.assertIn("note: headlines for BLOCKED: offline: no file", out)
        self.assertNotIn("filed with NSE", out)        # DEMO's filings were not asked for after the refusal
        self.assertIn("2026-10-19 08:04 IST  DEMO", out)

    def test_headlines_only(self):
        code, out, err = run(["--tickers", "DEMO", "--hours", "6", "--no-nse", "--json"] + self.base)
        self.assertEqual(code, 0, err)
        self.assertEqual({i["source"] for i in json.loads(out)["items"]}, {"Yahoo Finance"})

    def test_moves(self):
        m = news.parse_chart(raw("chart-DEMO.json"), "DEMO")
        self.assertEqual((m["price"], m["prev_close"], m["pct"]), (1369.85, 1352.4, 1.29))
        self.assertEqual(news.ist_text(m["time"], "%H:%M:%S"), "10:49:45")
        self.assertNotIn("MOVING", news.render_moves([m])[0])
        self.assertIn("MOVING", news.render_moves([m], move_pct=1.0)[0])
        with self.assertRaises(news.NewsError):
            news.parse_chart(b'{"chart": {"result": null}}', "ZZZZ")
        # no chart file offline: no move line, no error note
        only = os.path.join(self.tmp.name, "offline")
        os.makedirs(only)
        for f in ("yahoo-DEMO.xml", "nse-announcements-DEMO.json"):
            shutil.copy(os.path.join(DIR, f), only)
        code, out, _ = run(["--tickers", "DEMO", "--hours", "6", "--dry-run", "--offline", only,
                            "--state", self.state, "--now", AT_1015] + self.files)
        self.assertNotIn("vs the previous close", out)
        self.assertNotIn("note:", out)

    def test_india_time_has_no_daylight_saving(self):
        jan = datetime.datetime(2026, 1, 15, 3, 45, tzinfo=UTC)
        jul = datetime.datetime(2026, 7, 15, 3, 45, tzinfo=UTC)
        self.assertEqual(news.ist_text(jan, "%H:%M"), "09:15")
        self.assertEqual(news.ist_text(jul, "%H:%M"), "09:15")

    def test_state_forgets_after_30_days(self):
        state = {"seen": {"old": "2026-08-01T00:00:00+00:00"}}
        now = news.aware(news.parse_now(AT_1015))
        news.remember([], state, now)
        self.assertNotIn("old", state["seen"])
        self.assertEqual(state["last_check"], now.isoformat())


class Network(unittest.TestCase):
    """The live paths, with the network swapped out."""

    def fetcher(self, answers):
        f = news.Fetcher(pause=0, nse_pause=0)
        calls = []

        def fake(url, headers):
            calls.append((url, headers))
            a = answers.pop(0)
            if isinstance(a, Exception):
                raise a
            return a

        f._open = fake
        return f, calls

    def test_nse_warms_up_then_asks_like_a_browser(self):
        body = raw("nse-announcements-DEMO.json")
        f, calls = self.fetcher([(b"<html>home</html>", "text/html"), (body, "application/json")])
        doc = f.nse_json("corporate-announcements",
                         {"index": "equities", "symbol": "DEMO", "from_date": "19-10-2026", "to_date": "19-10-2026"},
                         "unused.json")
        self.assertEqual(len(doc), 6)
        self.assertEqual(calls[0][0], "https://www.nseindia.com/")
        url, headers = calls[1]
        self.assertEqual(url, "https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=DEMO"
                              "&from_date=19-10-2026&to_date=19-10-2026")
        self.assertEqual(headers["Referer"], "https://www.nseindia.com/")
        self.assertIn("Chrome", headers["User-Agent"])
        self.assertIn("application/json", headers["Accept"])
        # the home page is visited once per run
        f2, calls2 = self.fetcher([(b"<html/>", "text/html"), (b"[]", "application/json"), (b"[]", "application/json")])
        f2.nse_json("corporate-announcements", {"symbol": "A"}, "x")
        f2.nse_json("corporate-announcements", {"symbol": "B"}, "x")
        self.assertEqual(len(calls2), 3)

    def test_a_refusal_is_final_for_the_run(self):
        forbidden = urllib.error.HTTPError("https://www.nseindia.com/api/x", 403, "Forbidden", {}, None)
        f, calls = self.fetcher([(b"<html/>", "text/html"), forbidden])
        with self.assertRaises(news.NseRefused) as ctx:
            f.nse_json("corporate-announcements", {"symbol": "DEMO"}, "x")
        self.assertIn("HTTP 403", str(ctx.exception))
        with self.assertRaises(news.NseRefused):
            f.nse_json("corporate-announcements", {"symbol": "SAMPLE"}, "x")
        self.assertEqual(len(calls), 2)         # nothing more was sent after the refusal
        f, _ = self.fetcher([(b"<html/>", "text/html"), (b"<!DOCTYPE html><p>Please wait</p>", "text/html")])
        with self.assertRaises(news.NseRefused):
            f.nse_json("corporate-announcements", {"symbol": "DEMO"}, "x")
        broken = urllib.error.HTTPError("https://www.nseindia.com/api/x", 500, "Server Error", {}, None)
        f, _ = self.fetcher([(b"<html/>", "text/html"), broken])
        with self.assertRaises(news.NewsError) as ctx:
            f.nse_json("corporate-announcements", {"symbol": "DEMO"}, "x")
        self.assertNotIsInstance(ctx.exception, news.NseRefused)

    def test_yahoo_asks_for_the_india_feed(self):
        f, calls = self.fetcher([(raw("yahoo-DEMO.xml"), "application/rss+xml")])
        items = news.collect(f, {"M&M": None}, news.aware(news.parse_now(AT_1015)), nse=False)
        self.assertEqual(calls[0][0], "https://feeds.finance.yahoo.com/rss/2.0/headline?s=M%26M.NS"
                                      "&region=IN&lang=en-IN")
        self.assertIn("day-trader-skill-ind", calls[0][1]["User-Agent"])
        self.assertEqual(len(items), 9)

    def test_collect_asks_nse_for_the_window_in_india_dates(self):
        f, calls = self.fetcher([(b"<rss/>", "application/rss+xml"), (b"<html/>", "text/html"),
                                 (raw("nse-announcements-DEMO.json"), "application/json")])
        now = news.aware(news.parse_now("2026-10-19 02:00"))        # 02:00 IST is still 18 Oct in UTC
        items = news.collect(f, {"DEMO": None}, now, hours=48)
        self.assertIn("from_date=17-10-2026&to_date=19-10-2026", calls[2][0])
        self.assertEqual(len(items), 6)


if __name__ == "__main__":
    unittest.main()
