"""A fake Kite Connect for the tests: answers from the synthetic files in
tests/fixtures/shared/kite/ (or files a test adds) and records every request,
so a test can prove a tool only ever READ from Kite.

    fake = FakeKite()                      # DEMO's candles, quote and instruments
    fake.install(tool_module)              # replaces tool_module.kite_urlopen
    ...
    fake.methods()                         # {"GET"}: nothing but reads
    fake.paths()                           # ["/instruments/NSE", "/instruments/historical/9000001/5minute", ...]

A session file for the tools: fake.session_file(folder, login_ist) writes
trader/state/kite-session.json the way trader-broker/kite.py does.
"""

import datetime
import gzip
import io
import json
import os
import urllib.error
import urllib.parse

from _load import FIX

KDIR = os.path.join(FIX, "shared", "kite")
IST = datetime.timedelta(hours=5, minutes=30)


def _read(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class _Resp(io.BytesIO):
    def __init__(self, body, code=200):
        io.BytesIO.__init__(self, body.encode("utf-8"))
        self.status = code

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class FakeKite(object):
    def __init__(self, candles=None, quotes=None, instruments=None, routes=None):
        """candles: {(token, interval): file of a Kite historical answer}
        quotes: {SYMBOL: file of a Kite /quote answer}
        routes: {path: file or (http_code, file)} for anything else (positions, orders, ...)"""
        self.candles = {(9000001, "5minute"): os.path.join(KDIR, "historical-DEMO-5minute-synthetic.json.gz")}
        self.candles.update(candles or {})
        self.quotes = {"DEMO": os.path.join(KDIR, "quote-DEMO-synthetic.json")}
        self.quotes.update(quotes or {})
        self.instruments = instruments or os.path.join(KDIR, "instruments-NSE-synthetic.csv")
        self.routes = dict(routes or {})
        self.requests = []
        self.fail = {}          # path prefix -> (http_code, body file); for refusals

    # -------------------------------------------------------------- plumbing

    def install(self, mod):
        mod.kite_urlopen = self.urlopen
        return self

    def methods(self):
        return {m for m, _, _ in self.requests}

    def paths(self):
        return [urllib.parse.urlsplit(u).path for _, u, _ in self.requests]

    def urlopen(self, req, timeout=20):
        url = req.full_url
        self.requests.append((req.get_method(), url, dict(req.header_items())))
        parts = urllib.parse.urlsplit(url)
        path, query = parts.path, urllib.parse.parse_qs(parts.query)
        for prefix, (code, body) in self.fail.items():
            if path.startswith(prefix):
                raise urllib.error.HTTPError(url, code, "refused", {}, io.BytesIO(_read(body).encode("utf-8")))
        if path == "/instruments/NSE":
            return _Resp(_read(self.instruments))
        if path.startswith("/instruments/historical/"):
            _, _, _, token, interval = path.split("/")
            f = self.candles.get((int(token), interval))
            if not f:
                return _Resp(json.dumps({"status": "success", "data": {"candles": []}}))
            doc = json.loads(_read(f))
            lo, hi = query.get("from", [""])[0], query.get("to", [""])[0]
            keep = [r for r in doc["data"]["candles"]
                    if (not lo or r[0][:10] + " " + r[0][11:19] >= lo) and (not hi or r[0][:10] + " " + r[0][11:19] <= hi)]
            return _Resp(json.dumps({"status": "success", "data": {"candles": keep}}))
        if path in ("/quote", "/quote/ltp", "/quote/ohlc"):
            data = {}
            for inst in query.get("i", []):
                sym = inst.split(":", 1)[-1]
                if sym in self.quotes:
                    data.update(json.loads(_read(self.quotes[sym]))["data"])
            return _Resp(json.dumps({"status": "success", "data": data}))
        if path in self.routes:
            r = self.routes[path]
            if isinstance(r, tuple):
                code, body = r
                raise urllib.error.HTTPError(url, code, "refused", {}, io.BytesIO(_read(body).encode("utf-8")))
            return _Resp(_read(r))
        raise urllib.error.HTTPError(url, 404, "not found", {}, io.BytesIO(
            json.dumps({"status": "error", "message": "Route not found", "error_type": "GeneralException"}).encode()))

    # -------------------------------------------------------------- sessions

    @staticmethod
    def session_file(folder, login_ist=datetime.datetime(2026, 10, 19, 8, 58, 1), api_key="demo_api_key",
                     token="demo_access_token"):
        """Write trader/state/kite-session.json under folder, as kite.py session does."""
        path = os.path.join(folder, "trader", "state", "kite-session.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"api_key": api_key, "access_token": token, "user_id": "AB1234", "user_name": "Demo User",
                       "login_utc": (login_ist - IST).strftime("%Y-%m-%dT%H:%M:%SZ")}, fh)
        return path
