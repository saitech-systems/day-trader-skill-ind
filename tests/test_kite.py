"""trader-broker/kite.py tests: your own Kite Connect API, READ ONLY, on SYNTHETIC
answers. tests/fixtures/shared/kite/ holds DEMO's market data (a made-up NSE stock,
see tests/fixtures/make_synthetic.py) and tests/fixtures/trader-broker/kite/ the
account: the login, profile, margins, positions, the order book and trades, all
made up in the shapes Kite Connect answers in. tests/_kite.py fakes Kite and records
every request, so these tests prove the one thing sent to Kite that is not a read is
the daily login exchange, and that no token or secret is printed or written anywhere
but the session file. ticket.py match is tested here on that order book.
"""

import datetime
import gzip
import hashlib
import io
import json
import os
import shutil
import stat
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

from _load import load, ROOT, FIX
from _kite import FakeKite, KDIR

kite = load("trader-broker", "kite")
ticket = load("trader-broker", "ticket")
check = load("trader-risk", "check")
entry = load("trader-entry", "entry")

BDIR = os.path.join(FIX, "trader-broker", "kite")
CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
ORDERS_PATH = os.path.join(FIX, "trader-risk", "orders-demo.json")
ENV = dict(KITE_API_KEY="demo_api_key", KITE_API_SECRET="demo_api_secret")   # made up, like the answers
REQUEST_TOKEN = "RT9x2Kq7Lm"
REDIRECT = "http://127.0.0.1/?action=login&type=login&status=success&request_token=" + REQUEST_TOKEN
# sha256("demo_api_key" + "RT9x2Kq7Lm" + "demo_api_secret"), worked out once by hand
CHECKSUM = "aaaf853c65387a859922150417240afe4883abb06234fae43ba5f9e81d111764"
SECRETS = ("demo_access_token", "demo_api_secret", REQUEST_TOKEN, "demo_public_token", "demo_enctoken", CHECKSUM)
ACCOUNT = {"/session/token": "session-token-synthetic.json", "/user/profile": "profile-synthetic.json",
           "/user/margins/equity": "margins-synthetic.json", "/portfolio/positions": "positions-synthetic.json",
           "/orders": "orders-synthetic.json", "/trades": "trades-synthetic.json"}
UTC = kite.parse_now


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)                                   # trader/state/... lands in the temp folder
        env = dict(os.environ, **ENV)
        env.pop("KITE_ACCESS_TOKEN", None)
        self.env = mock.patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.fake = FakeKite(routes={k: os.path.join(BDIR, v) for k, v in ACCOUNT.items()})
        self.bodies = []

        def opener(req, timeout=20):
            self.bodies.append((req.get_method(), req.data))
            return self.fake.urlopen(req, timeout)
        kite.kite_urlopen = opener
        self.out = []

    def tearDown(self):
        self.env.stop()
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def run_kite(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = kite.main(list(argv))
        self.out.append(out.getvalue() + err.getvalue())
        return code, out.getvalue(), err.getvalue()

    def logged_in(self):
        return FakeKite.session_file(self.d)

    def files_text(self, skip=()):
        """Every file under the temp folder, as text, except the ones in skip."""
        found = {}
        for base, _, files in os.walk(self.d):
            for f in files:
                path = os.path.join(base, f)
                if os.path.relpath(path, self.d) in skip:
                    continue
                opener = gzip.open if f.endswith(".gz") else open
                with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
                    found[os.path.relpath(path, self.d)] = fh.read()
        return found


class Login(Base):
    def test_the_login_link(self):
        code, text, _ = self.run_kite("login", "--now", "2026-10-19 08:55")
        self.assertEqual(code, 0)
        self.assertIn("     https://kite.zerodha.com/connect/login?v=3&api_key=demo_api_key\n", text)
        self.assertIn("?request_token=...&action=login&status=success", text)
        self.assertIn('python3 trader-broker/kite.py session --url "PASTE-THE-ADDRESS-HERE"', text)
        self.assertIn("(in Claude Code, after a !)", text)
        self.assertIn("Never paste the secret anywhere", text)
        self.assertEqual(self.fake.requests, [])

    def test_no_key_no_link(self):
        os.environ.pop("KITE_API_KEY")
        code, _, err = self.run_kite("login")
        self.assertEqual(code, 1)
        self.assertIn("KITE_API_KEY is not set. Put it in your shell profile", err)
        self.assertIn("never in the chat", err)

    def test_the_checksum(self):
        self.assertEqual(kite.checksum("demo_api_key", REQUEST_TOKEN, "demo_api_secret"), CHECKSUM)
        self.assertEqual(CHECKSUM, hashlib.sha256(("demo_api_key" + REQUEST_TOKEN + "demo_api_secret")
                                                  .encode()).hexdigest())

    def test_the_session_from_the_redirect(self):
        code, text, err = self.run_kite("session", "--url", REDIRECT, "--now", "2026-10-19 08:58")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Logged in to Kite as Demo User (AB1234) until 06:00 IST on Tue 2026-10-20.", text)
        self.assertIn("only you can read it", text)
        self.assertIn("Keep trader/ out of git", text)
        path = os.path.join("trader", "state", "kite-session.json")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with open(path) as fh:
            doc = json.load(fh)
        self.assertEqual(doc, {"api_key": "demo_api_key", "access_token": "demo_access_token", "user_id": "AB1234",
                               "user_name": "Demo User", "login_utc": "2026-10-19T03:28:01Z"})
        # one request: the login exchange, a form POST with the checksum, no Authorization header
        self.assertEqual(self.fake.paths(), ["/session/token"])
        self.assertEqual(self.fake.methods(), {"POST"})
        method, url, headers = self.fake.requests[0]
        self.assertEqual(url, "https://api.kite.trade/session/token")
        self.assertEqual(headers.get("X-kite-version"), "3")
        self.assertNotIn("Authorization", headers)
        form = dict(p.split("=") for p in self.bodies[0][1].decode().split("&"))
        self.assertEqual(form, {"api_key": "demo_api_key", "request_token": REQUEST_TOKEN, "checksum": CHECKSUM})
        # the session works: status, and no token or secret in what was printed
        code, text, _ = self.run_kite("status", "--now", "2026-10-19 10:00")
        self.assertEqual(code, 0)
        self.assertIn("Kite session for Demo User (AB1234), logged in 08:58 IST, valid until 06:00 IST on Tue "
                      "2026-10-20", text)
        for out in self.out:
            for s in SECRETS:
                self.assertNotIn(s, out)

    def test_the_request_token_alone(self):
        code, text, _ = self.run_kite("session", "--request-token", REQUEST_TOKEN, "--now", "2026-10-19 08:58")
        self.assertEqual(code, 0)
        self.assertIn("Logged in to Kite as Demo User", text)

    def test_what_the_redirect_must_say(self):
        bad = {"http://127.0.0.1/?action=login&status=cancelled&request_token=RT9x2Kq7Lm": "did not succeed (status=cancelled)",
               "http://127.0.0.1/?action=login&status=success": "has no request_token",
               "http://127.0.0.1/?request_token=RT9x2Kq7Lm": "has no status=success",
               "http://127.0.0.1/?status=success&request_token=RT9x2;rm": "not a Kite request token"}
        for url, why in bad.items():
            code, _, err = self.run_kite("session", "--url", url)
            self.assertEqual(code, 1, url)
            self.assertIn(why, err, url)
        code, _, err = self.run_kite("session")
        self.assertIn("one of them", err)
        code, _, err = self.run_kite("session", "--url", REDIRECT, "--request-token", REQUEST_TOKEN)
        self.assertIn("one of them", err)
        self.assertEqual(self.fake.requests, [])

    def test_no_secret_no_session(self):
        os.environ.pop("KITE_API_SECRET")
        code, _, err = self.run_kite("session", "--url", REDIRECT)
        self.assertEqual(code, 1)
        self.assertIn("KITE_API_SECRET is not set", err)
        self.assertEqual(self.fake.requests, [])

    def test_a_refused_login_writes_nothing(self):
        self.fake.routes["/session/token"] = (403, os.path.join(BDIR, "session-refused-synthetic.json"))
        code, _, err = self.run_kite("session", "--url", REDIRECT)
        self.assertEqual(code, 1)
        self.assertIn("Kite refused the login (TokenException: Token is invalid or has expired.)", err)
        self.assertIn("A request token works once, for a few minutes", err)
        self.assertFalse(os.path.exists(os.path.join("trader", "state", "kite-session.json")))
        self.assertNotIn(REQUEST_TOKEN, err)


class Session(Base):
    def test_it_ends_at_six_in_the_morning(self):
        login = UTC("2026-10-19 08:58:01")
        self.assertEqual(kite.to_ist(kite.kite_expiry(login)), datetime.datetime(2026, 10, 20, 6, 0))
        early = UTC("2026-10-19 05:30")
        self.assertEqual(kite.to_ist(kite.kite_expiry(early)), datetime.datetime(2026, 10, 19, 6, 0))
        self.logged_in()
        self.assertIsNotNone(kite.kite_session(now_utc=UTC("2026-10-20 05:59")))
        self.assertIsNone(kite.kite_session(now_utc=UTC("2026-10-20 06:00")))
        code, text, _ = self.run_kite("status", "--now", "2026-10-20 06:00")
        self.assertEqual(code, 1)
        self.assertIn("No valid Kite session: the one from 2026-10-19 08:58 IST ended at 06:00 IST on Tue "
                      "2026-10-20", text)
        code, _, err = self.run_kite("quote", "DEMO", "--now", "2026-10-20 09:20")
        self.assertEqual(code, 1)
        self.assertIn("ended at 06:00 IST on Tue 2026-10-20: log in again (kite.py login)", err)
        self.assertEqual(self.fake.requests, [])

    def test_status_check_and_logout(self):
        self.logged_in()
        code, text, _ = self.run_kite("status", "--check", "--now", "2026-10-19 10:00")
        self.assertEqual(code, 0)
        self.assertIn("Kite confirms: Demo User (AB1234), broker ZERODHA.", text)
        self.assertEqual(self.fake.paths(), ["/user/profile"])
        _, url, headers = self.fake.requests[0]
        self.assertEqual(headers.get("Authorization"), "token demo_api_key:demo_access_token")
        code, text, _ = self.run_kite("logout")
        self.assertEqual(code, 0)
        self.assertIn("Zerodha ends the session itself at 06:00 IST", text)
        self.assertEqual(self.run_kite("logout")[0], 1)
        code, text, _ = self.run_kite("status")
        self.assertEqual(code, 1)
        self.assertIn("No Kite session. Log in: kite.py login. Until then the data tools use free Yahoo data.", text)
        code, _, err = self.run_kite("orders")
        self.assertIn("no Kite session: log in first", err)

    def test_the_environment_wins(self):
        os.environ["KITE_ACCESS_TOKEN"] = "demo_access_token"
        code, text, _ = self.run_kite("status")
        self.assertEqual(code, 0)
        self.assertIn("from the environment (KITE_API_KEY and KITE_ACCESS_TOKEN)", text)

    def test_a_file_this_tool_did_not_write(self):
        os.makedirs(os.path.join("trader", "state"))
        with open(os.path.join("trader", "state", "kite-session.json"), "w") as fh:
            fh.write("{not json")
        code, text, _ = self.run_kite("status")
        self.assertEqual(code, 1)
        self.assertIn("is not a session this tool wrote", text)


class MarketData(Base):
    def setUp(self):
        Base.setUp(self)
        self.logged_in()

    def test_quote_to_prices(self):
        os.makedirs("trader", exist_ok=True)
        with open(os.path.join("trader", "prices.json"), "w") as fh:
            json.dump({"as_of": "2026-10-19T04:00:00Z", "prices": {"TESTCO": 151.2},
                       "detail": {"TESTCO": {"time": "2026-10-19T04:00:00Z", "source": "yahoo"}}}, fh)
        code, text, _ = self.run_kite("quote", "demo", "--write", "--now", "2026-10-19 10:50")
        self.assertEqual(code, 0)
        self.assertIn("DEMO         1369.85, +1.29% vs the previous close 1352.40, last trade 10:49:45 IST\n", text)
        self.assertIn("Kite, live. Got 1 of 1.", text)
        self.assertIn("Wrote 1 price to trader/prices.json (source kite).", text)
        with open(os.path.join("trader", "prices.json")) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["prices"], {"TESTCO": 151.2, "DEMO": 1369.85})
        self.assertEqual(doc["detail"]["DEMO"], {"time": "2026-10-19T05:19:45Z", "fetched": "2026-10-19T05:20:00Z",
                                                 "source": "kite", "prev_close": 1352.4})
        self.assertEqual(doc["as_of"], "2026-10-19T05:20:00Z")
        self.assertEqual(self.fake.paths(), ["/quote"])
        self.assertIn("i=NSE%3ADEMO", self.fake.requests[0][1])
        code, text, _ = self.run_kite("quote", "SAMPLE", "--now", "2026-10-19 10:50")
        self.assertEqual(code, 1)
        self.assertIn("SAMPLE       no quote from Kite", text)
        code, _, err = self.run_kite("quote", "RELIANCE.BO")
        self.assertIn("BSE listing", err)

    def test_candles_in_yahoos_shape(self):
        out = os.path.join(self.d, "DEMO-kite.json.gz")
        code, text, _ = self.run_kite("candles", "DEMO", "--days", "90", "--to", "2026-10-19", "--out", out,
                                      "--now", "2026-10-19 10:49:45")
        self.assertEqual(code, 0)
        self.assertIn("4444 5m candles of DEMO from 2026-07-24 09:15 to 2026-10-19 10:45 IST (Kite)", text)
        self.assertIn("any tool reads it with --offline", text)
        hist = [u for m, u, h in self.fake.requests if "/historical/" in u][0]
        self.assertIn("/instruments/historical/9000001/5minute?from=2026-07-21+09%3A00%3A00&to=2026-10-19+16%3A00%3A00",
                      hist)
        with gzip.open(out, "rt", encoding="utf-8") as fh:
            text = fh.read()
        chart = entry.parse_chart(text, "DEMO")
        self.assertEqual((chart["symbol"], chart["prev_close"], chart["meta"]["source"], chart["meta"]["tickSize"]),
                         ("DEMO", 1352.4, "kite", 0.1))
        res = entry.evaluate(chart, entry.parse_now("2026-10-19 10:00"))
        self.assertEqual((res["status"], res["trigger"]["time"], res["trigger"]["price"]),
                         ("triggered", "10:00 AM", 1366.4))
        self.assertEqual((res["opening_range"]["high"], res["opening_range"]["low"]), (1363.8, 1351.3))
        code, _, err = self.run_kite("candles", "DEMO", "--days", "101")
        self.assertEqual(code, 1)
        self.assertIn("Kite gives at most 100 days of 5m candles in one request", err)
        code, text, _ = self.run_kite("candles", "DEMO", "--days", "3", "--to", "2026-10-19",
                                      "--now", "2026-10-19 10:49:45")
        self.assertIn("Add --out FILE", text)

    def test_the_instrument_list(self):
        code, text, _ = self.run_kite("instruments", "DEMO", "DEMO-BE", "--now", "2026-10-19 09:00")
        self.assertEqual(code, 1)
        self.assertIn("DEMO         token 9000001, tick 0.1, lot 1, DEMO INDUSTRIES (SYNTHETIC)", text)
        self.assertIn("DEMO-BE      not an NSE equity in Kite's list", text)     # BE series: no intraday
        code, text, _ = self.run_kite("instruments", "--now", "2026-10-19 09:30")
        self.assertIn("3 NSE equities in Kite's instrument list, cached for 2026-10-19", text)
        self.assertEqual(self.fake.paths(), ["/instruments/NSE"])                 # the day's copy is cached
        self.run_kite("instruments", "--refresh", "--now", "2026-10-19 09:30")
        self.run_kite("instruments", "--now", "2026-10-20 05:30")   # a new day, same session
        self.assertEqual(self.fake.paths(), ["/instruments/NSE"] * 3)


class Account(Base):
    def setUp(self):
        Base.setUp(self)
        self.logged_in()

    def test_margins(self):
        code, text, _ = self.run_kite("margins", "--now", "2026-10-19 10:05")
        self.assertEqual(code, 0)
        self.assertIn("  opening balance  Rs 1,00,000.00\n", text)
        self.assertIn("  live balance     Rs 85,234.40\n", text)
        self.assertIn("  used today       Rs 14,765.60\n", text)
        self.assertIn("at its margin, not its full cost", text)
        self.assertEqual(json.loads(self.run_kite("margins", "--json", "--now", "2026-10-19 10:05")[1])["net"],
                         85234.4)

    def test_positions_and_trades(self):
        code, text, _ = self.run_kite("positions", "--now", "2026-10-19 10:05")
        self.assertEqual(code, 0)
        self.assertIn("DEMO         MIS  54 shares, average 1366.40, last 1369.85", text)
        self.assertIn("SAMPLE       CNC  10 shares, average 405.20, last 409.00", text)
        self.assertIn("TESTCO       MIS  closed today (bought 20, sold 20)", text)
        self.assertIn("2 open, 1 closed today", text)
        self.assertNotIn("pnl", text.lower())
        code, text, _ = self.run_kite("trades", "--now", "2026-10-19 10:05")
        self.assertIn("10:01:06 BUY 54 DEMO at 1366.40, MIS, order 261019000123456", text)

    def test_the_order_book(self):
        code, text, _ = self.run_kite("orders", "--write", "--now", "2026-10-19 10:03")
        self.assertEqual(code, 0)
        self.assertIn("10:01:05 COMPLETE         BUY 54 DEMO LIMIT 1366.40 MIS, filled 54 at 1366.40, "
                      "id 261019000123456", text)
        self.assertIn("10:02:10 TRIGGER PENDING  SELL 54 DEMO SL 1354.10 trigger 1357.50 MIS, id 261019000123999",
                      text)
        self.assertIn("(Insufficient funds.", text)
        self.assertIn("Wrote 4 orders to trader/state/kite-orders.json. Next: ticket.py match", text)
        with open(os.path.join("trader", "state", "kite-orders.json")) as fh:
            book = json.load(fh)
        self.assertEqual((book["date"], book["source"], len(book["orders"])), ("2026-10-19", "kite", 4))

    def test_sync_shows_the_change_then_writes(self):
        os.makedirs("trader", exist_ok=True)
        pf = os.path.join("trader", "portfolio.json")
        with open(pf, "w") as fh:
            json.dump({"cash": 100000, "positions": []}, fh)
        code, text, _ = self.run_kite("sync", "--now", "2026-10-19 10:05")
        self.assertEqual(code, 0)
        self.assertIn("  cash: Rs 1,00,000.00 -> Rs 26,224.40\n", text)
        self.assertIn("  DEMO: 0 -> 54 shares\n", text)
        self.assertIn("  SAMPLE: 0 -> 10 shares\n", text)
        self.assertIn("minus what today's buys cost in full", text)
        self.assertIn("Nothing written: add --write", text)
        with open(pf) as fh:
            self.assertEqual(json.load(fh), {"cash": 100000, "positions": []})
        code, text, _ = self.run_kite("sync", "--write", "--now", "2026-10-19 10:05")
        self.assertIn("Wrote trader/portfolio.json (source kite).", text)
        with open(pf) as fh:
            doc = json.load(fh)
        self.assertEqual((doc["cash"], doc["source"], doc["as_of"]), (26224.4, "kite", "2026-10-19T04:35:00Z"))
        self.assertEqual([(p["ticker"], p["shares"], p["product"]) for p in doc["positions"]],
                         [("DEMO", 54, "MIS"), ("SAMPLE", 10, "CNC")])
        self.assertEqual(doc["positions"][0]["lots"], [{"id": "DEMO-kite", "date": "2026-10-19", "shares": 54,
                                                        "cost": 73785.6}])
        code, text, _ = self.run_kite("sync", "--now", "2026-10-19 10:06")
        self.assertIn("  cash: Rs 26,224.40 (unchanged)\n", text)
        self.assertIn("  DEMO: 54 shares (unchanged)\n", text)
        # trader-risk reads it as the account: 54 DEMO held, so a DEMO sell is an exit
        acct, _ = check.account({}, doc, {"DEMO": 1369.85})
        self.assertEqual((acct["cash"], acct["shares"]["DEMO"]), (26224.4, 54))

    def test_sync_falls_back_to_the_live_balance(self):
        with open(os.path.join(BDIR, "positions-synthetic.json")) as fh:
            doc = json.load(fh)
        for r in doc["data"]["net"]:
            r.pop("day_buy_value")
        alt = os.path.join(self.d, "positions-old.json")
        with open(alt, "w") as fh:
            json.dump(doc, fh)
        self.fake.routes["/portfolio/positions"] = alt
        code, text, _ = self.run_kite("sync", "--now", "2026-10-19 10:05")
        self.assertIn("cash: Rs 85,234.40 (new file)", text)
        self.assertIn("Kite's live balance (it counts MIS buys at their margin only, so it can overstate", text)

    def test_refusals_in_one_line(self):
        self.fake.fail["/quote"] = (403, os.path.join(KDIR, "error-token-synthetic.json"))
        code, _, err = self.run_kite("quote", "DEMO", "--now", "2026-10-19 10:05")
        self.assertEqual(code, 1)
        self.assertEqual(err, "kite.py: Kite refused the session (TokenException: Incorrect `api_key` or "
                              "`access_token`.). Log in again: python3 trader-broker/kite.py login\n")
        self.fake.fail["/orders"] = (429, os.path.join(BDIR, "error-429-synthetic.json"))
        code, _, err = self.run_kite("orders", "--now", "2026-10-19 10:05")
        self.assertEqual(code, 1)
        self.assertIn("Kite says too many requests (NetworkException: Too many requests): wait a few seconds", err)
        self.assertNotIn("Traceback", err)


class ReadOnly(Base):
    def test_everything_but_the_login_is_a_read_and_nothing_leaks(self):
        n = "2026-10-19 10:05"
        self.assertEqual(self.run_kite("session", "--url", REDIRECT, "--now", "2026-10-19 08:58")[0], 0)
        for argv in (["status", "--check"], ["quote", "DEMO", "--write"], ["instruments", "DEMO"],
                     ["candles", "DEMO", "--days", "5", "--to", "2026-10-19", "--out", "c.json"], ["margins"],
                     ["positions"], ["orders", "--write"], ["trades"], ["sync", "--write"],
                     ["margins", "--json"], ["orders", "--json"], ["positions", "--json"]):
            code, _, err = self.run_kite(*(argv + ["--now", n]))
            self.assertEqual(err, "", argv)
        posts = [(m, u) for m, u, h in self.fake.requests if m != "GET"]
        self.assertEqual(posts, [("POST", "https://api.kite.trade/session/token")])
        self.assertEqual([b for m, b in self.bodies if b is not None and m == "GET"], [])
        self.assertEqual({p for p in self.fake.paths()},
                         {"/session/token", "/user/profile", "/quote", "/instruments/NSE",
                          "/instruments/historical/9000001/5minute", "/user/margins/equity",
                          "/portfolio/positions", "/orders", "/trades"})
        for out in self.out:
            for s in SECRETS:
                self.assertNotIn(s, out)
        session = os.path.join("trader", "state", "kite-session.json")
        for rel, text in self.files_text(skip=(session,)).items():
            for s in SECRETS:
                self.assertNotIn(s, text, rel)
        with open(session) as fh:
            text = fh.read()
        for s in SECRETS[1:]:
            self.assertNotIn(s, text)

    def test_the_tool_has_no_order_endpoint(self):
        with open(os.path.join(ROOT, "trader-broker", "kite.py"), encoding="utf-8") as fh:
            src = fh.read()
        for bad in ("/orders/", "place_order", "modify_order", "cancel_order", "/gtt", '"PUT"', '"DELETE"'):
            self.assertNotIn(bad, src)
        self.assertEqual(src.count('method="POST"'), 1)


class Match(Base):
    """ticket.py match on a live account with the manual route: you place the order in
    Kite, kite.py orders --write saves the book, match records it."""

    def setUp(self):
        Base.setUp(self)
        self.logged_in()
        self.orders = os.path.join(self.d, "orders.json")
        shutil.copy(ORDERS_PATH, self.orders)
        with open(CFG_PATH) as fh:
            cfg = json.load(fh)
        cfg["paper"] = False
        self.cfg = os.path.join(self.d, "config.json")
        with open(self.cfg, "w") as fh:
            json.dump(cfg, fh)
        self.risk = os.path.join(self.d, "risk.json")
        self.state = os.path.join(self.d, "broker.json")
        self.pf = os.path.join(self.d, "portfolio.json")
        self.book = os.path.join("trader", "state", "kite-orders.json")
        with redirect_stdout(io.StringIO()):
            check.main(["--orders", self.orders, "--config", self.cfg, "--portfolio", self.pf, "--prices",
                        os.path.join(self.d, "none"), "--state", self.state, "--now", "2026-10-19 10:00",
                        "--out", self.risk])

    def ticket(self, *argv, **kw):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = ticket.main(list(argv) + ["--orders", self.orders, "--config", self.cfg, "--risk", self.risk,
                                             "--state", self.state, "--now", kw.get("now", "2026-10-19 10:00")])
        return code, out.getvalue(), err.getvalue()

    def match(self, *extra, **kw):
        return self.ticket("match", "--index", "1", "--kite-orders", self.book, "--portfolio", self.pf,
                           *extra, now=kw.get("now", "2026-10-19 10:03"))

    def saved_book(self, edit=None, now="2026-10-19 10:03"):
        """kite.py orders --write, with the answer edited first (a later state of the book)."""
        if edit:
            with open(os.path.join(BDIR, "orders-synthetic.json")) as fh:
                doc = json.load(fh)
            edit(doc["data"])
            alt = os.path.join(self.d, "orders-edited.json")
            with open(alt, "w") as fh:
                json.dump(doc, fh)
            self.fake.routes["/orders"] = alt
        else:
            self.fake.routes["/orders"] = os.path.join(BDIR, "orders-synthetic.json")
        self.assertEqual(self.run_kite("orders", "--write", "--now", now)[0], 0)

    def row(self, rows, oid):
        return [r for r in rows if r["order_id"] == oid][0]

    def state_doc(self):
        with open(self.state) as fh:
            return json.load(fh)

    def portfolio(self):
        with open(self.pf) as fh:
            return json.load(fh)

    def test_the_buy_its_stop_and_the_stop_filling(self):
        self.assertEqual(self.ticket("approve", "--index", "1", "--reply", "yes")[0], 0)
        self.saved_book()
        code, text, _ = self.match()
        self.assertEqual(code, 0)
        self.assertIn("Matched Kite order 261019000123456: COMPLETE, BUY 54 DEMO, limit 1366.40", text)
        self.assertIn("Recorded order 1 as placed (broker id 261019000123456).", text)
        self.assertIn("Recorded the fill: BUY 54 DEMO at 1366.40, into", text)
        self.assertIn("Now place its stop-loss yourself in Kite: SELL 54 DEMO, SL trigger 1357.50 limit 1354.10", text)
        st = self.state_doc()["orders"]
        self.assertEqual([(r["status"], r.get("broker_order_id")) for r in st],
                         [("approved", None), ("placed", "261019000123456"), ("filled", "261019000123456")])
        self.assertEqual((st[1]["via"], st[1]["max_loss"], st[1]["kind"], st[1]["paper"]),
                         ("kite order book", 553.67, "entry", False))
        self.assertEqual((self.portfolio()["cash"], self.portfolio()["positions"][0]["shares"]), (26214.4, 54))
        # the 09:58 CANCELLED buy was before the yes: never a candidate
        self.assertNotIn("261019000100001", json.dumps(st))

        code, text, _ = self.match()                       # again: nothing doubles
        self.assertIn("Its fill (54 at 1366.40) is already recorded.", text)
        self.assertEqual(len(self.state_doc()["orders"]), 3)
        self.assertEqual(self.portfolio()["cash"], 26214.4)

        code, text, _ = self.match("--leg", "stop")
        self.assertEqual(code, 0)
        self.assertIn("Matched Kite order 261019000123999: TRIGGER PENDING, SELL 54 DEMO, SL trigger 1357.50", text)
        self.assertIn("Recorded order 1's stop-loss as placed (broker id 261019000123999).", text)
        code, text, _ = self.match("--leg", "stop")
        self.assertIn("Still TRIGGER PENDING at Kite: nothing new to record.", text)
        code, text, _ = self.ticket("show", "--index", "1")
        self.assertIn("DONE: placed", text)
        self.assertIn("stop-loss placed", text)

        # trader-risk counts the trade once
        with open(self.orders) as fh:
            order = json.load(fh)["orders"][0]
        res = check.check([order], {"budget": 100000}, state=self.state_doc(), now=check.parse_now("2026-10-19 10:30"))
        self.assertEqual((res["today"]["entries_placed"], res["today"]["orders_placed"],
                          res["today"]["loss_at_risk"]), (1, 1, 553.67))

        def stopped(rows):
            r = self.row(rows, "261019000123999")
            r.update(status="COMPLETE", filled_quantity=54, pending_quantity=0, average_price=1357.4)
        self.saved_book(stopped, now="2026-10-19 11:40")
        code, text, _ = self.match("--leg", "stop", now="2026-10-19 11:41")
        self.assertEqual(code, 0)
        self.assertIn("Recorded the fill: SELL 54 DEMO at 1357.40", text)
        self.assertIn("The stop-loss filled: the trade is closed.", text)
        self.assertEqual((self.portfolio()["cash"], self.portfolio()["positions"]), (99514.0, []))

    def test_an_order_placed_before_the_yes_does_not_count(self):
        self.ticket("approve", "--index", "1", "--reply", "yes", now="2026-10-19 10:05")
        self.saved_book(now="2026-10-19 10:06")
        code, text, _ = self.match(now="2026-10-19 10:06")
        self.assertEqual(code, 1)
        self.assertIn("Not matched: Kite's order 261019000123456 (BUY 54 DEMO, limit 1366.40", text)
        self.assertIn("was placed at 10:01:05, before your yes at 10:05:00. An order only counts after the yes.", text)
        self.assertEqual([r["status"] for r in self.state_doc()["orders"]], ["approved"])

    def test_rejected_open_then_complete_and_partial(self):
        self.ticket("approve", "--index", "1", "--reply", "yes")

        def opened(rows):
            self.row(rows, "261019000123456").update(status="OPEN", filled_quantity=0, average_price=0)
        self.saved_book(opened)
        code, text, _ = self.match()
        self.assertIn("Recorded order 1 as placed (broker id 261019000123456).", text)
        self.assertIn("It has not filled yet.", text)
        self.assertFalse(os.path.exists(self.pf))
        self.saved_book(now="2026-10-19 10:04")             # now COMPLETE
        code, text, _ = self.match(now="2026-10-19 10:04")
        self.assertNotIn("Recorded order 1 as placed", text)
        self.assertIn("Recorded the fill: BUY 54 DEMO at 1366.40", text)
        self.assertEqual([r["status"] for r in self.state_doc()["orders"]], ["approved", "placed", "filled"])

    def test_a_rejected_buy_is_a_failure(self):
        self.ticket("approve", "--index", "1", "--reply", "yes")

        def rejected(rows):
            self.row(rows, "261019000123456").update(status="REJECTED", filled_quantity=0, average_price=0,
                                                     status_message="RMS: Margin exceeds")
        self.saved_book(rejected)
        code, text, _ = self.match()
        self.assertEqual(code, 0)
        self.assertIn("Recorded order 1 as failed (Kite says: RMS: Margin exceeds).", text)
        self.assertIn("not retried without a new check and a new yes", text)
        self.assertEqual(self.state_doc()["orders"][-1]["note"], "RMS: Margin exceeds")

    def test_a_partial_fill_needs_its_own_stop(self):
        self.ticket("approve", "--index", "1", "--reply", "yes")

        def partial(rows):
            self.row(rows, "261019000123456").update(status="CANCELLED", filled_quantity=20, pending_quantity=0,
                                                     cancelled_quantity=34)
        self.saved_book(partial)
        code, text, _ = self.match()
        self.assertIn("Recorded the fill: BUY 20 DEMO at 1366.40", text)
        self.assertIn("Kite cancelled the rest (20 of 54 filled).", text)
        self.assertIn("Only 20 shares filled: the stop-loss on the ticket is for 54", text)
        self.assertEqual(self.portfolio()["positions"][0]["shares"], 20)

    def test_ambiguous_wrong_and_stale_books(self):
        self.ticket("approve", "--index", "1", "--reply", "yes")

        def twice(rows):
            r = dict(self.row(rows, "261019000123456"), order_id="261019000123457", order_timestamp="2026-10-19 10:01:30")
            rows.append(r)
        self.saved_book(twice)
        code, text, _ = self.match()
        self.assertEqual(code, 1)
        self.assertIn("More than one Kite order is BUY 54 DEMO", text)
        self.assertIn("--order-id ID", text)
        code, text, _ = self.match("--order-id", "261019000123457")
        self.assertEqual(code, 0)
        self.assertIn("Matched Kite order 261019000123457", text)

        self.ticket("decline", "--index", "1", "--reply", "no")
        self.assertEqual(self.ticket("approve", "--index", "1", "--reply", "yes")[0], 1)    # done for today

    def test_another_quantity_is_not_this_ticket(self):
        self.ticket("approve", "--index", "1", "--reply", "yes")
        def other(rows):
            self.row(rows, "261019000123456").update(quantity=55, filled_quantity=55)
            rows.remove(self.row(rows, "261019000100001"))
        self.saved_book(other)
        code, text, _ = self.match()
        self.assertEqual(code, 1)
        self.assertIn("No order in Kite's order book is BUY 54 DEMO, limit 1366.40", text)
        self.assertIn("An order with any other quantity or price is not this ticket.", text)
        self.saved_book(now="2026-10-19 10:03")
        with open(self.book) as fh:
            doc = json.load(fh)
        doc["date"] = "2026-10-16"
        with open(self.book, "w") as fh:
            json.dump(doc, fh)
        code, _, err = self.match()
        self.assertEqual(code, 1)
        self.assertIn("is from 2026-10-16, not today: run kite.py orders --write again", err)


if __name__ == "__main__":
    unittest.main()
