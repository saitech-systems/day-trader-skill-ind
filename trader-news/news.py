#!/usr/bin/env python3
"""News monitor for trader-news. Standard library only.

Watches headlines and NSE corporate announcements for the NSE stocks you
watch or hold and prints only what is new since the last check, newest first,
with how far each stock has moved today and a flag on anything that touches
your symbols.

    python3 news.py                                  # watchlist + plan + portfolio symbols
    python3 news.py --tickers RELIANCE TCS INFY
    python3 news.py --tickers RELIANCE --all --hours 72  # everything in the last 3 days, seen or not
    python3 news.py --no-nse                         # headlines only
    python3 news.py --no-moves                       # skip the price line per symbol

Your symbols, when --tickers is not given: every hit in trader/watchlist.json
(trader-scanner), the symbol in trader/plan.json (trader-exit) and the
positions in trader/portfolio.json. Symbols are NSE symbols (RELIANCE, M&M);
RELIANCE.NS and NSE:RELIANCE work too.

Sources, one request each per symbol:
  - Yahoo Finance headline RSS (India edition):
    https://feeds.finance.yahoo.com/rss/2.0/headline?s=<SYMBOL>.NS&region=IN&lang=en-IN
  - NSE corporate announcements (what the company told the exchange:
    results, board meetings, director and auditor changes, credit ratings,
    defaults, pledges, deals), the JSON behind nseindia.com's announcements page:
    https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=<SYMBOL>&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY
    nseindia.com sits behind bot protection: the tool visits the home page
    first for its cookies, sends browser headers and waits a second between
    NSE calls. When NSE refuses anyway (HTTP 401/403 or a web page instead of
    JSON) it says so once, names the page to open in a browser, and carries on
    with the headlines. It never retries in a loop.
  - The move: Yahoo Finance's chart endpoint (unofficial, can lag), the last
    price against the previous close:
    https://query2.finance.yahoo.com/v8/finance/chart/<SYMBOL>.NS?range=1d&interval=5m

Times print in India time (IST). State: trader/state/news.json remembers what
you have seen (30 days), so an item shows once. Offline: --offline DIR reads
yahoo-<SYMBOL>.xml, nse-announcements-<SYMBOL>.json and chart-<SYMBOL>.json
from DIR. The tests use it.

Headlines are leads, not facts: open the story before acting, and treat
nothing here as advice.
"""

import argparse
import csv
import datetime
import email.utils
import gzip
import hashlib
import http.cookiejar
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

YAHOO = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%s&region=IN&lang=en-IN"
CHART = "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=5m"
NSE_HOME = "https://www.nseindia.com/"
NSE_API = "https://www.nseindia.com/api/"
NSE_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
UA = "Mozilla/5.0 (compatible; day-trader-skill-ind/1.0; +https://github.com/saitech-systems/day-trader-skill-ind)"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")
NSE_PAUSE = 1.0  # seconds between calls to nseindia.com
KEEP_DAYS = 30
MOVE_PCT = 3.0  # a stock this far from the previous close is flagged as moving

# What an NSE announcement is about, from its category (desc) or, failing that,
# its text. First match wins: (pattern, label, important, tag).
NSE_CATEGORIES = [
    (r"financial results?|results? updates?|quarterly results|audited results", "financial results", True, "earnings"),
    (r"outcome of (?:the )?board meeting", "board meeting outcome", True, "earnings"),
    (r"board meeting intimation|prior intimation", "board meeting date", False, "earnings"),
    (r"resign|cessation|change in (?:directors|key managerial|management|auditor)|appointment",
     "director, officer or auditor change", True, "people"),
    (r"credit rating", "credit rating", True, "risk"),
    (r"default|insolvency|bankruptcy|\bcirp\b|\bnclt\b", "default or insolvency", True, "risk"),
    (r"fraud|arrest", "fraud or arrest", True, "legal"),
    (r"pledge|encumbrance", "pledge of shares", True, "risk"),
    (r"clarification|spurt in (?:price|volume)|price movement", "clarification on price or volume", True, "regulatory"),
    (r"acquisition|amalgamation|merger|scheme of arrangement|takeover|open offer|demerger",
     "acquisition or merger", True, "deal"),
    (r"litigation|dispute|regulatory action|penalt|orders? passed|show cause", "litigation or regulatory action",
     False, "legal"),
    (r"dividend|buy ?back|bonus|split|rights issue|record date", "dividend, buyback or bonus", False, "capital"),
    (r"allotment|raising of funds|qualified institutional|preferential issue|\bqip\b", "fund raising or allotment",
     False, "capital"),
    (r"analysts?/institutional investor|investor meet|con\.? ?call|investor presentation|earnings call",
     "analyst or investor meet", False, "analyst"),
    (r"insider trading|\bsast\b|trading window", "insider trading or SAST", False, None),
    (r"press release", "press release", False, None),
]
KEYWORDS = [
    ("earnings", r"\bearnings\b|\bresults\b|\bEPS\b|\bguidance\b|\bforecast\b|\bnet profit\b|\bPAT\b|\bEBITDA\b"
                 r"|\bQ[1-4] ?FY ?\d{2}\b|\bQ[1-4]\b"),
    ("analyst", r"\bupgrade[sd]?\b|\bdowngrade[sd]?\b|price target|target price|\boutperform\b|\bunderperform\b"
                r"|\bbrokerages?\b|\bupside\b"),
    ("legal", r"\blawsuit\b|\bsued\b|\bprobe\b|\binvestigation\b|\bcourt\b|\bjudge\b|\bshow[- ]cause\b"
              r"|\bEnforcement Directorate\b|\bED raid|\bCBI\b|\bantitrust\b"),
    ("deal", r"\bacqui(?:re|res|red|sition)\b|\bmerger\b|\bbuyout\b|\btakeover\b|\bdeal\b|\bstake sale\b"
             r"|\bopen offer\b|\bdemerger\b"),
    ("capital", r"\bdividend\b|\bbuyback\b|\bbuy-back\b|\brepurchase\b|\bstock split\b|\bbonus issue\b"
                r"|\brights issue\b|\bQIP\b|\boffer for sale\b|\bOFS\b|\boffering\b|\bblock deal\b"),
    ("people", r"\bCEO\b|\bCFO\b|\bMD\b|\bmanaging director\b|\bchairman\b|\bresign|\bsteps down\b|\bappoint"),
    ("risk", r"\brecall\b|\bbankrupt|\binsolvency\b|\bdefault\b|\bdelist|\bhack|\bbreach\b|\boutage\b|\bpledge"
             r"|\bcredit rating\b|\brating (?:cut|downgrade)"),
    ("halt", r"\bhalt(?:s|ed)?\b|\btrading (?:pause|suspen)|\b(?:upper|lower) circuit\b|\bsurveillance\b"
             r"|\bASM\b|\bGSM\b"),
    ("regulatory", r"\bSEBI\b|\bRBI\b|\bCCI\b|\bUSFDA\b|\bFDA\b|\bDCGI\b|\bTRAI\b|\bapproval\b|\bapproves\b"
                   r"|\bclinical\b|\bwarning letter\b"),
    ("macro", r"\brepo rate\b|\bMPC\b|\bCPI\b|\binflation\b|\bGDP\b|\bIIP\b|\bGST collections?\b|\bUnion Budget\b"
              r"|\bFIIs?\b|\bFPIs?\b|\bSensex\b|\bNifty\b|\bGIFT Nifty\b|\bcrude\b|\brupee\b|\btariffs?\b"
              r"|\bFed\b|\bfutures\b"),
]


class NewsError(Exception):
    pass


class NseRefused(NewsError):
    pass


# ---------------------------------------------------------------- time (India, no tz files)

IST = datetime.timedelta(hours=5, minutes=30)   # India Standard Time: UTC+5:30 all year, no daylight saving
EPOCH = datetime.datetime(1970, 1, 1)


def to_ist(utc):
    """naive UTC -> naive India time."""
    return utc.replace(tzinfo=None) + IST


def ist_to_utc(local):
    """naive India time -> naive UTC."""
    return local.replace(tzinfo=None) - IST


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)


def ts_of(utc):
    return int((utc.replace(tzinfo=None) - EPOCH).total_seconds())


def parse_now(text):
    """--now -> naive UTC. "2026-10-19 10:15" (or ...T10:15, or just 10:15 for
    today) is India time; an offset or Z makes it absolute."""
    if not text:
        return utc_now()
    t = text.strip()
    if t.upper().endswith(" IST"):
        t = t[:-4].strip()
    if re.match(r"^\d{1,2}:\d{2}$", t):
        t = "%s %s" % (to_ist(utc_now()).date().isoformat(), t.zfill(5))
    t = t.replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise NewsError('--now wants an India time like "2026-10-19 10:15" (or ...Z for UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def aware(utc):
    """naive UTC -> aware UTC (items carry aware times)."""
    return utc.replace(tzinfo=datetime.timezone.utc)


def ist_text(utc, fmt="%Y-%m-%d %H:%M"):
    """Aware or naive UTC -> "2026-10-19 08:04" India time."""
    if utc.tzinfo is not None:
        utc = utc.astimezone(datetime.timezone.utc)
    return to_ist(utc).strftime(fmt)


# ---------------------------------------------------------------- NSE symbols on Yahoo

NSE_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")   # RELIANCE, M&M, BAJAJ-AUTO, 3MINDIA


def nse_symbol(text):
    """"reliance", "RELIANCE.NS", "NSE:RELIANCE" -> "RELIANCE". BSE (.BO) is refused."""
    s = str(text or "").strip().upper()
    if s.startswith("NSE:"):
        s = s[4:]
    if s.endswith(".NS"):
        s = s[:-3]
    if s.endswith(".BO") or s.startswith("BSE:"):
        raise NewsError("%s is a BSE listing: this pack trades NSE symbols (like RELIANCE)" % text)
    if not NSE_SYMBOL.match(s):
        raise NewsError("%r is not an NSE symbol (like RELIANCE, M&M or BAJAJ-AUTO)" % text)
    return s


def yahoo_symbol(sym):
    """RELIANCE -> RELIANCE.NS (Yahoo's NSE suffix)."""
    return nse_symbol(sym) + ".NS"


def yahoo_quote(sym):
    """For a URL path: M&M -> M%26M.NS."""
    from urllib.parse import quote
    return quote(yahoo_symbol(sym), safe=".-")


# BEGIN KITE BLOCK
# ---------------------------------------------------------------- Kite Connect, read only (market data, account)
#
# Your own Kite Connect app (developers.kite.trade; the Connect plan carries the
# market data). trader-broker/kite.py logs in once a day and saves the session to
# trader/state/kite-session.json; every tool reads it from there. Zerodha ends
# every session at 6:00 AM IST. KITE_API_KEY + KITE_ACCESS_TOKEN in the
# environment win over the file. This block only ever GETs: no orders.

KITE_API = "https://api.kite.trade"
KITE_SESSION = "trader/state/kite-session.json"
KITE_INSTRUMENTS = "trader/state/kite-instruments-NSE.json"
KITE_INTERVALS = {"1m": "minute", "5m": "5minute", "1d": "day"}
KITE_PAUSE = 0.35           # historical candles: Kite allows 3 requests a second
KITE_QUOTE_PAUSE = 1.0      # quotes: 1 request a second, up to 500 symbols each
KITE_SOURCES = ("auto", "kite", "yahoo")


def kite_urlopen(req, timeout=20):
    """The one network call of the block (tests replace it)."""
    return urllib.request.urlopen(req, timeout=timeout)


def kite_expiry(login_utc):
    """Kite sessions end at 6:00 AM IST after the login: naive UTC."""
    local = to_ist(login_utc)
    six = datetime.datetime.combine(local.date(), datetime.time(6, 0))
    if local >= six:
        six += datetime.timedelta(days=1)
    return ist_to_utc(six)


def kite_session(path=KITE_SESSION, now_utc=None):
    """The saved session while it is valid, else None. Never prints the token."""
    key, token = os.environ.get("KITE_API_KEY"), os.environ.get("KITE_ACCESS_TOKEN")
    if key and token:
        return {"api_key": key, "access_token": token, "source": "environment"}
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        login = datetime.datetime.strptime(doc["login_utc"], "%Y-%m-%dT%H:%M:%SZ")
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not doc.get("api_key") or not doc.get("access_token"):
        return None
    if (now_utc or utc_now()) >= kite_expiry(login):
        return None
    return dict(doc, source=path)


def kite_request(sess, path, params=None, timeout=20):
    """GET one Kite path: the raw body text. Plain-words NewsError on failure."""
    query = urllib.parse.urlencode(params or [], doseq=True)
    url = KITE_API + path + ("?" + query if query else "")
    req = urllib.request.Request(url, headers={
        "X-Kite-Version": "3", "User-Agent": UA,
        "Authorization": "token %s:%s" % (sess["api_key"], sess["access_token"])})
    try:
        with kite_urlopen(req, timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            doc = json.loads(e.read().decode("utf-8", "replace"))
            why = "%s: %s" % (doc.get("error_type") or "error", doc.get("message") or "")
        except Exception:
            why = "HTTP %d" % e.code
        finally:
            e.close()
        if e.code in (401, 403):
            raise NewsError("Kite refused the session (%s). Log in again: python3 trader-broker/kite.py login" % why)
        if e.code == 429:
            raise NewsError("Kite says too many requests (%s): wait a few seconds and try again" % why)
        raise NewsError("Kite answered HTTP %d for %s (%s)" % (e.code, path, why))
    except urllib.error.URLError as e:
        raise NewsError("could not reach Kite (%s): check the connection" % (e.reason,))
    except OSError as e:
        raise NewsError("could not reach Kite: %s" % (e,))


def kite_get(sess, path, params=None):
    """GET a Kite JSON path: its "data"."""
    text = kite_request(sess, path, params)
    try:
        doc = json.loads(text)
    except ValueError:
        raise NewsError("Kite sent something that is not JSON for %s" % path)
    if doc.get("status") != "success":
        raise NewsError("Kite said %s for %s: %s" % (doc.get("error_type") or "error", path, doc.get("message")))
    return doc.get("data")


def kite_instruments(sess, path=KITE_INSTRUMENTS, now_utc=None):
    """{SYMBOL: {"token", "tick", "name", "lot"}} for NSE equities, from Kite's
    instrument dump, cached for the day (Kite rebuilds it every morning)."""
    today = to_ist(now_utc or utc_now()).date().isoformat()
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            if doc.get("date") == today and doc.get("rows"):
                return doc["rows"]
        except (OSError, ValueError, AttributeError):
            pass
    text = kite_request(sess, "/instruments/NSE")
    rows = {}
    for r in csv.DictReader(io.StringIO(text)):
        if r.get("segment") != "NSE" or r.get("instrument_type") != "EQ":
            continue
        try:
            rows[r["tradingsymbol"].strip().upper()] = {
                "token": int(r["instrument_token"]), "tick": float(r.get("tick_size") or 0.05),
                "name": (r.get("name") or "").strip(), "lot": int(float(r.get("lot_size") or 1))}
        except (KeyError, ValueError):
            continue
    if not rows:
        raise NewsError("Kite's NSE instrument list came back empty")
    if path:
        try:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"date": today, "rows": rows}, fh)
        except OSError:
            pass
    return rows


def kite_ts(text):
    """"2026-10-19T09:15:00+0530" -> Unix seconds."""
    dt = datetime.datetime.strptime(str(text).strip(), "%Y-%m-%dT%H:%M:%S%z")
    return int((dt - datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)).total_seconds())


def kite_quotes(sess, symbols, pause=KITE_QUOTE_PAUSE):
    """{SYM: {"price", "prev_close", "time" (naive UTC or None), "volume"}}, 500 a call."""
    syms = [nse_symbol(s) for s in symbols]
    out = {}
    for i in range(0, len(syms), 500):
        if i:
            time.sleep(pause)
        batch = syms[i:i + 500]
        data = kite_get(sess, "/quote", [("i", "NSE:" + s) for s in batch]) or {}
        for s in batch:
            d = data.get("NSE:" + s)
            if not d or d.get("last_price") is None:
                continue
            stamp = d.get("last_trade_time") or d.get("timestamp")
            when = None
            if stamp:
                try:
                    when = ist_to_utc(datetime.datetime.strptime(str(stamp)[:19], "%Y-%m-%d %H:%M:%S"))
                except ValueError:
                    when = None
            out[s] = {"price": float(d["last_price"]), "prev_close": (d.get("ohlc") or {}).get("close"),
                      "time": when, "volume": d.get("volume")}
    return out


def kite_chart(sess, sym, interval, start_day, end_day, now_utc=None, with_quote=True):
    """Kite candles for one NSE stock from start_day to end_day (dates, IST),
    returned as Yahoo chart JSON v8 text: the tool then reads it exactly like a
    Yahoo answer. A forming candle comes back too; the tool's own closed-bar
    rule decides when it counts. with_quote adds the previous close and the
    last price from Kite's quote."""
    sym = nse_symbol(sym)
    if interval not in KITE_INTERVALS:
        raise NewsError("Kite candles here are 1m, 5m or 1d, not %r" % interval)
    inst = kite_instruments(sess, now_utc=now_utc).get(sym)
    if not inst:
        raise NewsError("Kite has no NSE equity called %s" % sym)
    data = kite_get(sess, "/instruments/historical/%d/%s" % (inst["token"], KITE_INTERVALS[interval]),
                    [("from", "%s 09:00:00" % start_day), ("to", "%s 16:00:00" % end_day)]) or {}
    q = {"open": [], "high": [], "low": [], "close": [], "volume": []}
    stamps = []
    for row in data.get("candles") or []:
        try:
            t = kite_ts(row[0])
            o, h, lo, c = (float(x) for x in row[1:5])
        except (ValueError, TypeError, IndexError):
            continue
        stamps.append(t)
        for k, v in zip(("open", "high", "low", "close"), (o, h, lo, c)):
            q[k].append(v)
        q["volume"].append(int(row[5]) if len(row) > 5 and row[5] is not None else 0)
    now_ts = ts_of(now_utc or utc_now())
    step = {"1m": 60, "5m": 300, "1d": 86400}[interval]
    last_end = (stamps[-1] + step) if stamps else now_ts
    meta = {"symbol": yahoo_symbol(sym), "currency": "INR", "exchangeName": "NSI", "fullExchangeName": "NSE",
            "instrumentType": "EQUITY", "dataGranularity": interval, "gmtoffset": 19800, "timezone": "IST",
            "exchangeTimezoneName": "Asia/Kolkata", "regularMarketTime": min(now_ts, last_end),
            "longName": inst["name"], "tickSize": inst["tick"], "source": "kite"}
    if stamps:
        meta["regularMarketPrice"] = q["close"][-1]
    if with_quote:
        time.sleep(KITE_PAUSE)
        quote = kite_quotes(sess, [sym]).get(sym) or {}
        if quote.get("prev_close"):
            meta["previousClose"] = meta["chartPreviousClose"] = float(quote["prev_close"])
        if quote.get("price") is not None:
            meta["regularMarketPrice"] = quote["price"]
    return json.dumps({"chart": {"result": [{"meta": meta, "timestamp": stamps, "indicators": {"quote": [q]}}],
                                 "error": None}})


def pick_source(choice=None, cfg=None, now_utc=None, path=KITE_SESSION):
    """(source, session, note). --source wins, then "data_source" in the config.
    "auto" (the default) is Kite when a session from today is saved, else Yahoo."""
    want = str(choice or (cfg or {}).get("data_source") or "auto").strip().lower()
    if want not in KITE_SOURCES:
        raise NewsError("data source is auto, kite or yahoo, not %r" % want)
    if want == "yahoo":
        return "yahoo", None, ""
    sess = kite_session(path, now_utc)
    if sess:
        return "kite", sess, ""
    if want == "kite":
        raise NewsError("no Kite session for today: log in first (python3 trader-broker/kite.py login), "
                        "or use --source yahoo")
    return "yahoo", None, "no Kite session today, so free Yahoo data (log in with trader-broker/kite.py for live Kite data)"
# END KITE BLOCK


# ---------------------------------------------------------------- inputs

def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def _add(out, sym, name):
    try:
        s = nse_symbol(sym)
    except NewsError:
        return
    out[s] = name or out.get(s)


def watch_from_files(portfolio="trader/portfolio.json", watchlist="trader/watchlist.json",
                     plan="trader/plan.json"):
    """{symbol: name or None}: the scanner's hits, the planned trade and your holdings."""
    out = {}
    wl = load_json(watchlist) or {}
    for h in wl.get("hits", []):
        sym = h.get("symbol") or h.get("ticker")
        if sym:
            _add(out, sym, h.get("name"))
    pl = load_json(plan) or {}
    if pl.get("symbol"):
        _add(out, pl["symbol"], pl.get("name"))
    pf = load_json(portfolio) or {}
    for p in pf.get("positions", []):
        sym = p.get("ticker") or p.get("symbol")
        if sym:
            _add(out, sym, p.get("name"))
    return out


# ---------------------------------------------------------------- fetching

class Fetcher(object):
    """Yahoo with a plain GET; nseindia.com with a cookie jar, browser headers,
    a home-page visit first and a pause between calls. Offline: files in a folder."""

    def __init__(self, offline=None, pause=0.15, nse_pause=NSE_PAUSE):
        self.offline = offline
        self.pause = pause
        self.nse_pause = nse_pause
        self._last = 0.0
        self._nse_last = 0.0
        self._nse_ready = False
        self.nse_refused = None          # the first refusal, so NSE is asked only once after it
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def _offline(self, name):
        path = os.path.join(self.offline, name)
        if not os.path.isfile(path):
            raise NewsError("offline: no file %s" % path)
        with open(path, "rb") as fh:
            return fh.read()

    def _open(self, url, headers):
        """One GET -> (body bytes, content type). Raises urllib errors."""
        req = urllib.request.Request(url, headers=headers)
        with self._opener.open(req, timeout=30) as resp:
            data = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
            return data, resp.headers.get("Content-Type") or ""

    def get(self, url, name):
        if self.offline:
            return self._offline(name)
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()
        try:
            return self._open(url, {"User-Agent": UA, "Accept-Encoding": "gzip"})[0]
        except urllib.error.HTTPError as exc:
            raise NewsError("HTTP %s for %s" % (exc.code, url.split("?")[0]))
        except urllib.error.URLError as exc:
            raise NewsError("could not reach %s: %s" % (url.split("?")[0], exc.reason))

    def _nse_wait(self):
        gap = time.time() - self._nse_last
        if gap < self.nse_pause:
            time.sleep(self.nse_pause - gap)
        self._nse_last = time.time()

    def _refused(self, why):
        self.nse_refused = NseRefused(
            "NSE refused the script (%s): nseindia.com's bot protection blocks many scripts. Read the "
            "announcements in your browser instead: %s" % (why, NSE_PAGE))
        return self.nse_refused

    def nse_json(self, endpoint, params, name):
        """GET www.nseindia.com/api/<endpoint>?params -> parsed JSON, or NseRefused."""
        if self.nse_refused is not None:
            raise self.nse_refused
        if self.offline:
            data = self._offline(name)
        else:
            headers = {"User-Agent": BROWSER_UA, "Accept-Language": "en-IN,en;q=0.9",
                       "Accept-Encoding": "gzip"}
            try:
                if not self._nse_ready:
                    self._nse_wait()
                    self._open(NSE_HOME, dict(headers, Accept="text/html,application/xhtml+xml,*/*;q=0.8"))
                    self._nse_ready = True
                self._nse_wait()
                data, ctype = self._open(NSE_API + endpoint + "?" + urllib.parse.urlencode(params),
                                         dict(headers, Accept="application/json, text/plain, */*",
                                              Referer=NSE_HOME))
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise self._refused("HTTP %d" % exc.code)
                raise NewsError("NSE answered HTTP %d for %s" % (exc.code, endpoint))
            except urllib.error.URLError as exc:
                raise NewsError("could not reach nseindia.com: %s" % (exc.reason,))
            if "html" in ctype.lower():
                raise self._refused("a web page instead of data")
        if data.lstrip()[:1] == b"<":
            raise self._refused("a web page instead of data")
        try:
            return json.loads(data.decode("utf-8"))
        except ValueError:
            raise NewsError("NSE sent something that is not JSON for %s" % endpoint)


# ---------------------------------------------------------------- parsing

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _text(el, name):
    for c in el.iter():
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def parse_time(value):
    """RFC 822 (RSS) or ISO 8601 -> aware UTC datetime, or None."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        d = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        d = None
    if d is None:
        try:
            d = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return d.astimezone(datetime.timezone.utc)


def parse_nse_time(value):
    """NSE's India-time stamps ("2026-10-19 09:02:11", "19-Oct-2026 09:02:11") -> aware UTC, or None."""
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d%m%Y%H%M%S"):
        try:
            return aware(ist_to_utc(datetime.datetime.strptime(value, fmt)))
        except ValueError:
            continue
    return None


def parse_rss(data, ticker):
    """Yahoo headline RSS -> items."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NewsError("RSS for %s is not valid XML: %s" % (ticker, exc))
    out = []
    for it in root.iter("item"):
        title = _text(it, "title")
        link = _text(it, "link")
        guid = _text(it, "guid") or link
        out.append({"id": "rss:" + guid, "source": "Yahoo Finance", "kind": "headline",
                    "ticker": ticker, "title": title, "summary": _text(it, "description"),
                    "link": link, "time": parse_time(_text(it, "pubDate"))})
    return out


def nse_category(desc, text=""):
    """(label, important, tag) for an NSE announcement: its category first, then its text."""
    for hay in (desc or "", text or ""):
        for rx, label, important, tag_ in NSE_CATEGORIES:
            if re.search(rx, hay, re.I):
                return label, important, tag_
    return None, False, None


def parse_nse_announcements(doc, ticker):
    """NSE corporate-announcements JSON (a list, or {"data": [...]}) -> items."""
    rows = doc.get("data") if isinstance(doc, dict) else doc
    if not isinstance(rows, list):
        raise NewsError("NSE announcements for %s are not a list" % ticker)
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        sym = str(r.get("symbol") or ticker).upper()
        desc = (r.get("desc") or "").strip()
        text = (r.get("attchmntText") or "").strip()
        label, important, tag_ = nse_category(desc, text)
        link = r.get("attchmntFile") or ""
        key = r.get("seq_id") or link or "%s|%s|%s" % (sym, r.get("an_dt"), desc)
        title = "%s filed with NSE: %s" % (r.get("sm_name") or sym, desc or "announcement")
        if label and label.lower() != desc.lower():
            title += " (%s)" % label
        out.append({"id": "nse:%s" % key, "source": "NSE", "kind": "filing", "ticker": sym,
                    "title": title, "summary": text, "category": label, "category_tag": tag_,
                    "important": important, "link": link,
                    "time": parse_nse_time(r.get("sort_date")) or parse_nse_time(r.get("an_dt"))})
    return out


def parse_chart(data, ticker):
    """Yahoo chart JSON -> {"ticker", "price", "prev_close", "pct", "time"} or raise."""
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        meta = doc["chart"]["result"][0]["meta"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise NewsError("chart for %s is not usable" % ticker)
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if not price or not prev:
        raise NewsError("chart for %s has no price" % ticker)
    when = meta.get("regularMarketTime")
    return {"ticker": ticker, "price": round(float(price), 2), "prev_close": round(float(prev), 2),
            "pct": round((float(price) / float(prev) - 1) * 100, 2),
            "time": datetime.datetime.fromtimestamp(when, datetime.timezone.utc) if when else None}


def collect_moves(fetcher, watch, errors=None):
    errors = [] if errors is None else errors
    out = []
    for t in sorted(watch):
        name = "chart-%s.json" % t
        try:
            try:
                data = fetcher.get(CHART % ("query2", yahoo_quote(t)), name)
            except NewsError as exc:
                if fetcher.offline or not re.search(r"HTTP (429|5\d\d)", str(exc)):
                    raise
                data = fetcher.get(CHART % ("query1", yahoo_quote(t)), name)
            out.append(parse_chart(data, t))
        except NewsError as exc:
            if not (fetcher.offline and "no file" in str(exc)):
                errors.append("price for %s: %s" % (t, exc))
    return out


def mention_patterns(watch):
    pats = {}
    for t, name in watch.items():
        e = re.escape(t)
        alts = [r"\(%s\)" % e, r"\bNSE\s*:\s*%s(?![\w&-])" % e, r"(?<![\w&-])%s\.NS\b" % e]
        if name and len(name) >= 4:
            alts.append(r"\b%s\b" % re.escape(name).replace(r"\ ", r"[\s-]"))
        pats[t] = re.compile("|".join(alts))
    return pats


def tag(items, watch):
    """Which of your symbols each item touches, and why it matters."""
    pats = mention_patterns(watch)
    for it in items:
        hay = "%s %s" % (it["title"], it.get("summary") or "")
        touches = {it["ticker"]} if it["ticker"] in watch else set()
        for t, rx in pats.items():
            if rx.search(hay):
                touches.add(t)
        it["touches"] = sorted(touches)
        it["tags"] = [label for label, rx in KEYWORDS if re.search(rx, hay, re.I)]
        ct = it.get("category_tag")
        if ct and ct not in it["tags"]:
            it["tags"].insert(0, ct)
    return items


def dedupe(items):
    """One item per story: same id, or same link, or same title the same day."""
    seen, out = {}, []
    for it in items:
        keys = [it["id"], it["link"],
                "%s|%s" % (re.sub(r"\W+", " ", it["title"].lower()).strip(),
                           it["time"].date() if it["time"] else "")]
        hit = next((seen[k] for k in keys if k and k in seen), None)
        if hit is not None:
            for t in it.get("touches", []):
                if t not in hit["touches"]:
                    hit["touches"].append(t)
            hit["touches"].sort()
            continue
        for k in keys:
            if k:
                seen[k] = it
        out.append(it)
    return out


def fingerprint(it):
    return hashlib.sha1(it["id"].encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- the check

def collect(fetcher, watch, now, hours=48, nse=True, errors=None):
    """Headlines and NSE announcements for every symbol. now: aware UTC."""
    errors = [] if errors is None else errors
    items = []
    for t in sorted(watch):
        try:
            items += parse_rss(fetcher.get(YAHOO % yahoo_quote(t), "yahoo-%s.xml" % t), t)
        except NewsError as exc:
            errors.append("headlines for %s: %s" % (t, exc))
    if nse:
        start = ist_text(now - datetime.timedelta(hours=hours), "%d-%m-%Y")
        end = ist_text(now, "%d-%m-%Y")
        for t in sorted(watch):
            try:
                doc = fetcher.nse_json("corporate-announcements",
                                       {"index": "equities", "symbol": t, "from_date": start, "to_date": end},
                                       "nse-announcements-%s.json" % t)
                items += parse_nse_announcements(doc, t)
            except NseRefused as exc:
                errors.append("NSE announcements skipped: %s" % exc)
                break
            except NewsError as exc:
                errors.append("NSE announcements for %s: %s" % (t, exc))
    return items


def select(items, state, now, hours=48, show_all=False):
    """Items within the window; only unseen ones unless show_all."""
    seen = state.setdefault("seen", {})
    cutoff = now - datetime.timedelta(hours=hours)
    ahead = now + datetime.timedelta(minutes=10)  # a pinned --now: nothing from after it
    out = []
    for it in items:
        if it["time"] is not None and (it["time"] < cutoff or it["time"] > ahead):
            continue
        fp = fingerprint(it)
        it["new"] = fp not in seen
        if it["new"] or show_all:
            out.append(it)
    out.sort(key=lambda i: (i["time"] or now), reverse=True)
    return out


def remember(items, state, now):
    seen = state.setdefault("seen", {})
    stamp = now.isoformat()
    for it in items:
        seen.setdefault(fingerprint(it), stamp)
    cutoff = (now - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    state["last_check"] = stamp


def render_moves(moves, move_pct=MOVE_PCT):
    out = []
    for m in moves:
        flag = "MOVING" if abs(m["pct"]) >= move_pct else ""
        when = (" at %s IST" % ist_text(m["time"], "%H:%M")) if m.get("time") else ""
        out.append("%-10s %.2f %+.2f%% vs the previous close %.2f%s  %s" % (
            m["ticker"], m["price"], m["pct"], m["prev_close"], when, flag))
    return out


def render(items, errors, now, moves=None, move_pct=MOVE_PCT):
    out = render_moves(moves or [], move_pct)
    if out:
        out.append("")
    if not items:
        out.append("Nothing new.")
    for it in items:
        when = (ist_text(it["time"]) + " IST") if it["time"] else "time unknown"
        mark = "!" if it.get("important") else " "
        who = ",".join(it["touches"]) or it["ticker"]
        out.append("%s %s  %-11s %-13s %s" % (mark, when, who, it["source"], it["title"]))
        extra = []
        if it["tags"]:
            extra.append("tags: " + ", ".join(it["tags"]))
        extra.append(it["link"])
        out.append("      " + "  ".join(extra))
    for e in errors:
        out.append("note: %s" % e)
    out.append("Checked %s IST. Headlines are leads, not facts; open the story or the filing before acting. "
               "Not financial advice." % ist_text(now))
    return "\n".join(out) + "\n"


def _json_ready(items):
    out = []
    for it in items:
        d = dict(it)
        d["time"] = it["time"].isoformat() if it["time"] else None
        out.append(d)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tickers", nargs="*", help="NSE symbols; default: watchlist + plan + portfolio")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--watchlist", default="trader/watchlist.json")
    ap.add_argument("--plan", default="trader/plan.json")
    ap.add_argument("--state", default="trader/state/news.json")
    ap.add_argument("--hours", type=float, default=48, help="ignore items older than this")
    ap.add_argument("--all", action="store_true", help="show seen items too")
    ap.add_argument("--no-nse", action="store_true", help="headlines only, no NSE announcements")
    ap.add_argument("--no-moves", action="store_true", help="skip the price line per symbol")
    ap.add_argument("--move-pct", type=float, default=MOVE_PCT, help="flag a move this big (percent)")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--now", help='pretend it is this India time, e.g. "2026-10-19 10:15"')
    ap.add_argument("--dry-run", action="store_true", help="do not update the state file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        if args.tickers:
            watch = {nse_symbol(t): None for t in args.tickers}
            known = watch_from_files(args.portfolio, args.watchlist, args.plan)
            watch.update({t: known[t] for t in watch if known.get(t)})
        else:
            watch = watch_from_files(args.portfolio, args.watchlist, args.plan)
        now = aware(parse_now(args.now))
    except NewsError as exc:
        print("news: %s" % exc, file=sys.stderr)
        return 2
    if not watch:
        print("news: no tickers. Pass --tickers, or run /trader-scanner first (it writes "
              "trader/watchlist.json).", file=sys.stderr)
        return 2
    state = load_json(args.state) or {}
    errors = []
    fetcher = Fetcher(offline=args.offline)
    items = collect(fetcher, watch, now, hours=args.hours, nse=not args.no_nse, errors=errors)
    items = dedupe(tag(items, watch))
    shown = select(items, state, now, hours=args.hours, show_all=args.all)
    moves = [] if args.no_moves else collect_moves(fetcher, watch, errors)
    if args.json:
        mv = [dict(m, time=m["time"].isoformat() if m.get("time") else None,
                   moving=abs(m["pct"]) >= args.move_pct) for m in moves]
        print(json.dumps({"checked": now.isoformat(), "moves": mv, "items": _json_ready(shown),
                          "notes": errors}, indent=2))
    else:
        sys.stdout.write(render(shown, errors, now, moves, args.move_pct))
    if not args.dry_run:
        remember(items, state, now)
        folder = os.path.dirname(args.state)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.state, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
    return 0 if any(i["new"] for i in shown) else 1


if __name__ == "__main__":
    sys.exit(main())
