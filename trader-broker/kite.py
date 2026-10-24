#!/usr/bin/env python3
"""Your own Kite Connect API, READ ONLY, for trader-broker. Standard library only.

It logs in once a day, then reads market data and your Zerodha account:
prices, candles, the instrument list, margins, positions, the order book and
trades. It never places, changes or cancels an order, and it has no code that
could. The one thing it sends to Kite is the daily login exchange (a request
token for a session). You place orders yourself in Kite; trader-broker's
ticket.py then matches them against the order book this tool saves.

    python3 kite.py login                         # the Kite login link (needs KITE_API_KEY)
    python3 kite.py session --url "<redirect>"    # run it yourself: saves today's session
    python3 kite.py status [--check]              # valid until when? --check asks Kite
    python3 kite.py logout                        # forget the saved session
    python3 kite.py quote DEMO RELIANCE --write   # live prices -> trader/prices.json
    python3 kite.py candles DEMO --interval 5m --days 60 --out DEMO.json.gz
    python3 kite.py instruments DEMO              # token, tick size, lot
    python3 kite.py margins                       # what Kite says you can use
    python3 kite.py positions                     # today's positions
    python3 kite.py orders --write                # the order book -> trader/state/kite-orders.json
    python3 kite.py trades                        # today's fills
    python3 kite.py sync --write                  # positions and cash -> trader/portfolio.json

Credentials. Your Kite Connect app (developers.kite.trade; the Connect plan
carries the market data) has an API key and an API secret. Put them in the
environment, in your shell profile: KITE_API_KEY and KITE_API_SECRET. Never
in the chat, never in a file in your project. The session is saved to
trader/state/kite-session.json, readable by you only, and Zerodha ends it at
6:00 AM IST every day. Nothing here prints the access token, the secret or
the request token. KITE_API_KEY with KITE_ACCESS_TOKEN in the environment
skips the file.

The daily login. `login` prints https://kite.zerodha.com/connect/login?v=3&api_key=...
You log in there (your password and TOTP go to Zerodha, never here). Kite then
sends the browser to your app's redirect URL with
?request_token=...&action=login&status=success (the page itself may not load:
that is fine). Copy the whole address and run `session --url "<it>"` yourself
(in Claude Code, after a !). The request token works once, for a few minutes,
and is useless without your secret: checksum = sha256(api_key + request_token
+ api_secret), POST https://api.kite.trade/session/token.

Every amount is in rupees with Indian digit grouping. --now "2026-10-19 10:00"
pins the clock (India time). Exit code 0 on success, 1 on a refusal or nothing
to show.
"""

import argparse
import csv
import datetime
import gzip
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (compatible; day-trader-skill-ind/1.0; +https://github.com/saitech-systems/day-trader-skill-ind)"
LOGIN_URL = "https://kite.zerodha.com/connect/login?v=3&api_key=%s"
DEFAULT_PRICES = "trader/prices.json"
DEFAULT_ORDERS = "trader/state/kite-orders.json"
DEFAULT_PORTFOLIO = "trader/portfolio.json"
MAX_DAYS = {"1m": 60, "5m": 100, "1d": 2000}      # Kite's most days of candles in one request
DEFAULT_DAYS = {"1m": 7, "5m": 60, "1d": 365}
TOKEN_RE = re.compile(r"^[A-Za-z0-9]{6,64}$")
FILLED, DEAD = "COMPLETE", ("REJECTED", "CANCELLED")


class KiteError(Exception):
    pass


# ---------------------------------------------------------------- NSE calendar (India time, no tz files)

IST = datetime.timedelta(hours=5, minutes=30)   # India Standard Time: UTC+5:30 all year, no daylight saving
EPOCH = datetime.datetime(1970, 1, 1)

# NSE equity segment trading holidays that fall on weekdays, from the NSE holiday
# list (nseindia.com/resources/exchange-communication-holidays, read 2026-10-23).
# NSE publishes next year's list in December: 2027 is not out yet.
NSE_HOLIDAYS = {
    "2026-01-15": "Municipal Corporation Elections in Maharashtra",
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}
# Special sessions on days that are otherwise closed: (open, close, name), India time.
NSE_SPECIAL_SESSIONS = {
    "2026-02-01": ("09:15", "15:30", "Union Budget day (Sunday), normal hours"),
    "2026-11-08": ("14:20", "15:30", "Muhurat trading, Diwali Laxmi Pujan (Sunday)"),
}
CALENDAR_YEARS = (2026,)
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)


def to_ist(utc):
    """naive UTC -> naive India time."""
    return utc.replace(tzinfo=None) + IST


def ist_to_utc(local):
    """naive India time -> naive UTC."""
    return local.replace(tzinfo=None) - IST


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)


def utc_of(ts):
    """Unix seconds -> naive UTC, without the machine's time zone."""
    return EPOCH + datetime.timedelta(seconds=int(ts))


def ts_of(utc):
    return int((utc.replace(tzinfo=None) - EPOCH).total_seconds())


def _hm(text):
    h, m = text.split(":")
    return datetime.time(int(h), int(m))


def trading_day(d):
    """(is_trading_day, reason). Weekends, NSE holidays and special sessions."""
    k = d.isoformat()
    if k in NSE_SPECIAL_SESSIONS:
        return True, "special session: " + NSE_SPECIAL_SESSIONS[k][2]
    if d.weekday() >= 5:
        return False, "weekend"
    name = NSE_HOLIDAYS.get(k)
    if name:
        return False, "NSE holiday: " + name
    return True, ""


def session(d):
    """(open_time, close_time) in India time, or None if NSE is closed all day."""
    k = d.isoformat()
    if k in NSE_SPECIAL_SESSIONS:
        o, c, _ = NSE_SPECIAL_SESSIONS[k]
        return _hm(o), _hm(c)
    if d.weekday() >= 5 or k in NSE_HOLIDAYS:
        return None
    return OPEN, CLOSE


def next_open(d):
    """"2026-10-21 09:15": the next session's open after day d, or None."""
    nd = d + datetime.timedelta(days=1)
    for _ in range(15):
        s = session(nd)
        if s:
            return "%s %02d:%02d" % (nd.isoformat(), s[0].hour, s[0].minute)
        nd += datetime.timedelta(days=1)
    return None


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
        raise KiteError('--now wants an India time like "2026-10-19 10:15" (or ...Z for UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


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
        raise KiteError("%s is a BSE listing: this pack trades NSE symbols (like RELIANCE)" % text)
    if not NSE_SYMBOL.match(s):
        raise KiteError("%r is not an NSE symbol (like RELIANCE, M&M or BAJAJ-AUTO)" % text)
    return s


def yahoo_symbol(sym):
    """RELIANCE -> RELIANCE.NS (Yahoo's NSE suffix)."""
    return nse_symbol(sym) + ".NS"


def yahoo_quote(sym):
    """For a URL path: M&M -> M%26M.NS."""
    from urllib.parse import quote
    return quote(yahoo_symbol(sym), safe=".-")


# ---------------------------------------------------------------- rupees

def inr(x, paise=None):
    """Rs 1,23,456.50 with Indian digit grouping (lakh, crore). Whole rupees
    drop the paise unless paise=True; paise=False always rounds to the rupee."""
    v = float(x)
    s = "%.2f" % abs(v)
    whole, frac = s.split(".")
    if paise is False:
        whole, frac = str(int(round(abs(v)))), "00"
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    out = "Rs " + whole + ("" if (frac == "00" and not paise) else "." + frac)
    return ("-" if v < 0 and float(s) != 0 else "") + out


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
    """GET one Kite path: the raw body text. Plain-words KiteError on failure."""
    query = urllib.parse.urlencode(params or [], doseq=True)
    url = KITE_API + path + ("?" + query if query else "")
    req = urllib.request.Request(url, headers={
        "X-Kite-Version": "3", "User-Agent": UA,
        "Authorization": "token %s:%s" % (sess["api_key"], sess["access_token"])})
    try:
        with kite_urlopen(req, timeout) as r:
            body = r.read()
        if body[:2] == b"\x1f\x8b":         # a gzipped answer (the instrument dump can come that way)
            body = gzip.decompress(body)
        return body.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            doc = json.loads(e.read().decode("utf-8", "replace"))
            why = "%s: %s" % (doc.get("error_type") or "error", doc.get("message") or "")
        except Exception:
            why = "HTTP %d" % e.code
        finally:
            e.close()
        if e.code in (401, 403):
            raise KiteError("Kite refused the session (%s). Log in again with /trader-broker (kite.py login)" % why)
        if e.code == 429:
            raise KiteError("Kite says too many requests (%s): wait a few seconds and try again" % why)
        raise KiteError("Kite answered HTTP %d for %s (%s)" % (e.code, path, why))
    except urllib.error.URLError as e:
        raise KiteError("could not reach Kite (%s): check the connection" % (e.reason,))
    except OSError as e:
        raise KiteError("could not reach Kite: %s" % (e,))


def kite_get(sess, path, params=None):
    """GET a Kite JSON path: its "data"."""
    text = kite_request(sess, path, params)
    try:
        doc = json.loads(text)
    except ValueError:
        raise KiteError("Kite sent something that is not JSON for %s" % path)
    if doc.get("status") != "success":
        raise KiteError("Kite said %s for %s: %s" % (doc.get("error_type") or "error", path, doc.get("message")))
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
        raise KiteError("Kite's NSE instrument list came back empty")
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
        raise KiteError("Kite candles here are 1m, 5m or 1d, not %r" % interval)
    inst = kite_instruments(sess, now_utc=now_utc).get(sym)
    if not inst:
        raise KiteError("Kite has no NSE equity called %s" % sym)
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
        raise KiteError("data source is auto, kite or yahoo, not %r" % want)
    if want == "yahoo":
        return "yahoo", None, ""
    sess = kite_session(path, now_utc)
    if sess:
        return "kite", sess, ""
    if want == "kite":
        raise KiteError("no Kite session for today: log in first with /trader-broker (kite.py login), "
                        "or use --source yahoo")
    return "yahoo", None, "no Kite session today, so free Yahoo data (log in with /trader-broker for live Kite data)"
# END KITE BLOCK


# ---------------------------------------------------------------- the daily login (the one write)

def env_credential(name):
    """KITE_API_KEY / KITE_API_SECRET from the environment, or a plain refusal."""
    v = (os.environ.get(name) or "").strip()
    if not v:
        raise KiteError("%s is not set. Put it in your shell profile (export %s=...) or the Claude environment, "
                        "never in the chat or a project file" % (name, name))
    return v


def request_token_from(url=None, token=None):
    """The request token from Kite's redirect address (status must be success), or as given."""
    if url:
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url.strip()).query)
        status = (q.get("status") or [""])[0]
        if status and status != "success":
            raise KiteError("Kite says the login did not succeed (status=%s): log in again (kite.py login)" % status)
        token = (q.get("request_token") or [""])[0]
        if not token:
            raise KiteError("that address has no request_token: copy the whole address Kite sent the browser to "
                            "after you logged in")
        if status != "success":
            raise KiteError("that address has no status=success: copy the whole address Kite sent the browser to")
    token = (token or "").strip()
    if not TOKEN_RE.match(token):
        raise KiteError("that is not a Kite request token (letters and digits only): copy it again")
    return token


def checksum(api_key, request_token, api_secret):
    """Kite's login checksum: SHA-256 of api_key + request_token + api_secret, in hex."""
    return hashlib.sha256((api_key + request_token + api_secret).encode("utf-8")).hexdigest()


def exchange_token(api_key, request_token, api_secret):
    """POST /session/token: the only request in the pack that is not a GET."""
    body = urllib.parse.urlencode({"api_key": api_key, "request_token": request_token,
                                   "checksum": checksum(api_key, request_token, api_secret)}).encode("utf-8")
    req = urllib.request.Request(KITE_API + "/session/token", data=body, method="POST", headers={
        "X-Kite-Version": "3", "User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with kite_urlopen(req) as r:
            text = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            doc = json.loads(e.read().decode("utf-8", "replace"))
            why = "%s: %s" % (doc.get("error_type") or "error", doc.get("message") or "")
        except Exception:
            why = "HTTP %d" % e.code
        finally:
            e.close()
        if e.code in (400, 403):
            raise KiteError("Kite refused the login (%s). A request token works once, for a few minutes: log in "
                            "again (kite.py login) and run session with the new address" % why)
        raise KiteError("Kite answered HTTP %d to the login (%s)" % (e.code, why))
    except urllib.error.URLError as e:
        raise KiteError("could not reach Kite (%s): check the connection" % (e.reason,))
    except OSError as e:
        raise KiteError("could not reach Kite: %s" % (e,))
    try:
        doc = json.loads(text)
    except ValueError:
        raise KiteError("Kite sent something that is not JSON to the login")
    data = doc.get("data") or {}
    if doc.get("status") != "success" or not data.get("access_token"):
        raise KiteError("Kite did not open a session: %s" % (doc.get("message") or "no access token in the answer"))
    return data


def write_private(path, doc):
    """Write a JSON file only its owner can read (mode 0600)."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def guard_from_git(path):
    """Put a .gitignore that ignores everything next to the session file, so git
    never picks up the token in whatever project the pack runs in. Returns the
    .gitignore's path when it wrote one."""
    d = os.path.dirname(os.path.abspath(path))
    gi = os.path.join(d, ".gitignore")
    if os.path.exists(gi):
        return None
    try:
        with open(gi, "w", encoding="utf-8") as fh:
            fh.write("# written by trader-broker/kite.py: the Kite session in this folder must never be committed\n*\n")
    except OSError:
        return None
    return gi


def saved_session(path):
    """The session file as saved, valid or not (for status and logout), or None."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        login = datetime.datetime.strptime(doc["login_utc"], "%Y-%m-%dT%H:%M:%SZ")
    except (OSError, ValueError, KeyError, TypeError):
        return {"broken": True}
    return dict(doc, login=login)


def until_words(expiry_utc):
    """"06:00 IST on Tue 2026-10-20"."""
    return "06:00 IST on %s" % to_ist(expiry_utc).strftime("%a %Y-%m-%d")


def need_session(a, now):
    sess = kite_session(a.session, now)
    if not sess:
        old = saved_session(a.session)
        if old and not old.get("broken"):
            raise KiteError("the Kite session from %s ended at %s: log in again (kite.py login)"
                            % (to_ist(old["login"]).strftime("%Y-%m-%d %H:%M IST"), until_words(kite_expiry(old["login"]))))
        raise KiteError("no Kite session: log in first (kite.py login, then kite.py session --url ...)")
    return sess


# ---------------------------------------------------------------- small helpers

def iso_z(utc):
    return utc.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _p(v):
    return "%.2f" % float(v)


def _q(v):
    f = float(v)
    return str(int(f)) if f == int(f) else ("%.4f" % f).rstrip("0").rstrip(".")


def read_json(path, what):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise KiteError("%s is not valid JSON: %s" % (path, e))


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def kite_time(text):
    """"2026-10-19 10:01:05" (India time) -> naive UTC, or None."""
    try:
        return ist_to_utc(datetime.datetime.strptime(str(text)[:19], "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        return None


def show_json(a, doc):
    if getattr(a, "json", False):
        print(json.dumps(doc, indent=2, default=str))
        return True
    return False


# ---------------------------------------------------------------- commands: the session

def cmd_login(a):
    now = parse_now(a.now)
    key = env_credential("KITE_API_KEY")
    sess = kite_session(a.session, now)
    if sess and sess.get("source") != "environment":
        print("A Kite session is already saved for %s (%s), valid until %s. Log in again only if Kite refused it."
              % (sess.get("user_name") or "you", sess.get("user_id") or "?", until_words(kite_expiry(
                  datetime.datetime.strptime(sess["login_utc"], "%Y-%m-%dT%H:%M:%SZ")))))
    print("1. Open this link and log in to Kite (your password and TOTP go to Zerodha, never here):")
    print("     " + LOGIN_URL % urllib.parse.quote(key, safe=""))
    print("2. Kite then sends the browser to your app's redirect URL, ending in")
    print("     ?request_token=...&action=login&status=success")
    print("   The page itself may not load. That is fine: copy the whole address.")
    print("3. Run this yourself (in Claude Code, after a !), with that address in the quotes:")
    print('     python3 "%s" session --url "PASTE-THE-ADDRESS-HERE"' % os.path.abspath(__file__))
    print("The request token in it works once, for a few minutes, and is useless without your API secret,")
    print("which stays in KITE_API_SECRET. Never paste the secret anywhere.")
    return 0


def cmd_session(a):
    now = parse_now(a.now)
    if bool(a.url) == bool(a.request_token):
        raise KiteError("give the address Kite sent you to (--url) or the request token (--request-token), one of them")
    token = request_token_from(a.url, a.request_token)
    key, secret = env_credential("KITE_API_KEY"), env_credential("KITE_API_SECRET")
    data = exchange_token(key, token, secret)
    login = kite_time(data.get("login_time")) or now
    doc = {"api_key": key, "access_token": data["access_token"], "user_id": data.get("user_id") or "",
           "user_name": data.get("user_name") or "", "login_utc": iso_z(login)}
    write_private(a.session, doc)
    guard = guard_from_git(a.session)
    print("Logged in to Kite as %s (%s) until %s." % (doc["user_name"] or "you", doc["user_id"] or "?",
                                                     until_words(kite_expiry(login))))
    print("Saved the session to %s (only you can read it)." % a.session)
    if guard:
        print("Wrote %s so git ignores that folder; add trader/ to your project's .gitignore as well." % guard)
    print("The data tools now read live Kite data (\"data_source\": \"auto\"). Nothing here can place an order.")
    return 0


def cmd_status(a):
    now = parse_now(a.now)
    sess = kite_session(a.session, now)
    if not sess:
        old = saved_session(a.session)
        if old and old.get("broken"):
            print("%s is not a session this tool wrote: log in again (kite.py login)." % a.session)
        elif old:
            print("No valid Kite session: the one from %s ended at %s. Log in again: kite.py login."
                  % (to_ist(old["login"]).strftime("%Y-%m-%d %H:%M IST"), until_words(kite_expiry(old["login"]))))
        else:
            print("No Kite session. Log in: kite.py login. Until then the data tools use free Yahoo data.")
        return 1
    if sess.get("source") == "environment":
        print("Kite session from the environment (KITE_API_KEY and KITE_ACCESS_TOKEN); its expiry is not known here.")
    else:
        login = datetime.datetime.strptime(sess["login_utc"], "%Y-%m-%dT%H:%M:%SZ")
        print("Kite session for %s (%s), logged in %s, valid until %s (%s)." % (
            sess.get("user_name") or "you", sess.get("user_id") or "?", to_ist(login).strftime("%H:%M IST"),
            until_words(kite_expiry(login)), a.session))
    if a.check:
        prof = kite_get(sess, "/user/profile") or {}
        print("Kite confirms: %s (%s), broker %s." % (prof.get("user_name") or "?", prof.get("user_id") or "?",
                                                      prof.get("broker") or "?"))
    return 0


def cmd_logout(a):
    if not os.path.exists(a.session):
        print("No saved Kite session at %s." % a.session)
        return 1
    os.remove(a.session)
    print("Deleted %s. Zerodha ends the session itself at 06:00 IST; to end it now, log out of Kite on the web."
          % a.session)
    return 0


# ---------------------------------------------------------------- commands: market data

def cmd_quote(a):
    now = parse_now(a.now)
    syms = [nse_symbol(s) for s in a.symbols]
    sess = need_session(a, now)
    q = kite_quotes(sess, syms)
    if show_json(a, {s: dict(v, time=iso_z(v["time"]) if v.get("time") else None) for s, v in q.items()}):
        return 0 if q else 1
    for s in syms:
        v = q.get(s)
        if not v:
            print("%-12s no quote from Kite (not an NSE equity it knows?)" % s)
            continue
        pc = _f(v.get("prev_close"))
        move = (", %+.2f%% vs the previous close %s" % (100.0 * (v["price"] / pc - 1), _p(pc))) if pc else ""
        when = (", last trade %s IST" % to_ist(v["time"]).strftime("%H:%M:%S")) if v.get("time") else ""
        print("%-12s %s%s%s" % (s, _p(v["price"]), move, when))
    print("Kite, live. Got %d of %d." % (len(q), len(syms)))
    if a.write and q:
        doc = read_json(a.prices, "prices") or {}
        if not isinstance(doc, dict):
            doc = {}
        doc.setdefault("prices", {})
        doc.setdefault("detail", {})
        for s, v in q.items():
            doc["prices"][s] = v["price"]
            doc["detail"][s] = {"time": iso_z(v["time"]) if v.get("time") else None, "fetched": iso_z(now),
                                "source": "kite", "prev_close": _f(v.get("prev_close"))}
        doc["as_of"] = iso_z(now)
        write_json(a.prices, doc)
        print("Wrote %d price%s to %s (source kite)." % (len(q), "" if len(q) == 1 else "s", a.prices))
    return 0 if q else 1


def cmd_candles(a):
    now = parse_now(a.now)
    sym = nse_symbol(a.symbol)
    days = a.days if a.days is not None else DEFAULT_DAYS[a.interval]
    if days < 0 or days > MAX_DAYS[a.interval]:
        raise KiteError("Kite gives at most %d days of %s candles in one request; --days %d is too many"
                        % (MAX_DAYS[a.interval], a.interval, days))
    try:
        end = datetime.date.fromisoformat(a.to) if a.to else to_ist(now).date()
    except ValueError:
        raise KiteError("--to wants a date like 2026-10-19, not %r" % a.to)
    sess = need_session(a, now)
    start = end - datetime.timedelta(days=days)
    text = kite_chart(sess, sym, a.interval, start.isoformat(), end.isoformat(), now_utc=now)
    doc = json.loads(text)
    stamps = doc["chart"]["result"][0]["timestamp"]
    span = ""
    if stamps:
        span = " from %s to %s IST" % (to_ist(utc_of(stamps[0])).strftime("%Y-%m-%d %H:%M"),
                                       to_ist(utc_of(stamps[-1])).strftime("%Y-%m-%d %H:%M"))
    if a.out:
        d = os.path.dirname(a.out)
        if d:
            os.makedirs(d, exist_ok=True)
        if a.out.endswith(".gz"):
            with gzip.open(a.out, "wt", encoding="utf-8") as fh:
                fh.write(text)
        else:
            with open(a.out, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
        print("Wrote %s: %d %s candles of %s%s (Kite), in Yahoo's chart shape: any tool reads it with --offline %s."
              % (a.out, len(stamps), a.interval, sym, span, a.out))
    else:
        print("%s: %d %s candles%s (Kite). Add --out FILE to save them for a tool's --offline." % (
            sym, len(stamps), a.interval, span))
    return 0 if stamps else 1


def cmd_instruments(a):
    now = parse_now(a.now)
    sess = need_session(a, now)
    if a.refresh and os.path.exists(a.instruments):
        os.remove(a.instruments)
    rows = kite_instruments(sess, a.instruments, now)
    if a.symbols:
        code = 0
        for s in a.symbols:
            s = nse_symbol(s)
            r = rows.get(s)
            if not r:
                print("%-12s not an NSE equity in Kite's list" % s)
                code = 1
                continue
            print("%-12s token %d, tick %s, lot %d, %s" % (s, r["token"], _q(r["tick"]), r["lot"], r["name"]))
        return code
    print("%d NSE equities in Kite's instrument list, cached for %s in %s." % (
        len(rows), to_ist(now).date().isoformat(), a.instruments))
    return 0


# ---------------------------------------------------------------- commands: your account (read only)

def cmd_margins(a):
    now = parse_now(a.now)
    sess = need_session(a, now)
    m = kite_get(sess, "/user/margins/equity") or {}
    if show_json(a, m):
        return 0
    av, used = m.get("available") or {}, m.get("utilised") or {}
    print("Equity margins (Kite, live):")
    for label, v in (("opening balance", av.get("opening_balance")), ("pay-ins today", av.get("intraday_payin")),
                     ("live balance", av.get("live_balance")), ("used today", used.get("debits")),
                     ("net available", m.get("net"))):
        if _f(v) is not None:
            print("  %-16s %s" % (label, inr(v, paise=True)))
    print("Kite counts an intraday (MIS) buy at its margin, not its full cost; this pack sizes without that leverage.")
    return 0


def position_rows(data):
    """Kite's net positions with shares still open, plus the ones closed today."""
    rows = (data or {}).get("net") or []
    open_, closed = [], []
    for r in rows:
        if str(r.get("exchange") or "NSE").upper() != "NSE":
            continue
        q = _f(r.get("quantity")) or 0
        (open_ if abs(q) > 1e-9 else closed).append(r)
    return open_, closed


def cmd_positions(a):
    now = parse_now(a.now)
    sess = need_session(a, now)
    data = kite_get(sess, "/portfolio/positions") or {}
    if show_json(a, data):
        return 0
    open_, closed = position_rows(data)
    if not open_ and not closed:
        print("No NSE positions in Kite today.")
        return 1
    for r in open_:
        q = _f(r["quantity"])
        print("%-12s %-4s %s shares%s, average %s, last %s" % (
            r.get("tradingsymbol"), r.get("product") or "", _q(q), " (short)" if q < 0 else "",
            _p(r.get("average_price") or 0), _p(r.get("last_price") or 0)))
    for r in closed:
        print("%-12s %-4s closed today (bought %s, sold %s)" % (
            r.get("tradingsymbol"), r.get("product") or "", _q(r.get("day_buy_quantity") or r.get("buy_quantity") or 0),
            _q(r.get("day_sell_quantity") or r.get("sell_quantity") or 0)))
    print("Kite, live. %d open, %d closed today." % (len(open_), len(closed)))
    return 0


def order_line(o):
    """10:01:05 COMPLETE  BUY 54 DEMO LIMIT 1366.40 MIS, filled 54 at 1366.40, id 261019000123456"""
    t = kite_time(o.get("order_timestamp"))
    typ = str(o.get("order_type") or "").upper()
    how = typ
    if typ in ("LIMIT", "SL") and _f(o.get("price")):
        how += " " + _p(o["price"])
    if typ in ("SL", "SL-M") and _f(o.get("trigger_price")):
        how += " trigger " + _p(o["trigger_price"])
    fill = ""
    if _f(o.get("filled_quantity")):
        fill = ", filled %s at %s" % (_q(o["filled_quantity"]), _p(o.get("average_price") or 0))
    why = (" (%s)" % o["status_message"]) if o.get("status_message") and o.get("status") in DEAD else ""
    return "%s %-16s %s %s %s %s %s%s, id %s%s" % (
        to_ist(t).strftime("%H:%M:%S") if t else "--:--:--", o.get("status"), o.get("transaction_type"),
        _q(o.get("quantity") or 0), o.get("tradingsymbol"), how, o.get("product"), fill, o.get("order_id"), why)


def cmd_orders(a):
    now = parse_now(a.now)
    sess = need_session(a, now)
    rows = kite_get(sess, "/orders") or []
    if a.write:
        write_json(a.out, {"fetched": iso_z(now), "date": to_ist(now).date().isoformat(), "source": "kite",
                           "orders": rows})
    if show_json(a, rows):
        return 0 if rows else 1
    if not rows:
        print("No orders in Kite's order book today.")
    for o in rows:
        print(order_line(o))
    if a.write:
        print("Wrote %d order%s to %s. Next: ticket.py match --index N (and --leg stop)." % (
            len(rows), "" if len(rows) == 1 else "s", a.out))
    return 0 if rows else 1


def cmd_trades(a):
    now = parse_now(a.now)
    sess = need_session(a, now)
    rows = kite_get(sess, "/trades") or []
    if show_json(a, rows):
        return 0 if rows else 1
    if not rows:
        print("No trades (fills) in Kite today.")
        return 1
    for t in rows:
        when = kite_time(t.get("fill_timestamp") or t.get("exchange_timestamp"))
        print("%s %s %s %s at %s, %s, order %s" % (
            to_ist(when).strftime("%H:%M:%S") if when else "--:--:--", t.get("transaction_type"),
            _q(t.get("quantity") or 0), t.get("tradingsymbol"), _p(t.get("average_price") or 0),
            t.get("product"), t.get("order_id")))
    return 0


def kite_portfolio(positions, margins, now):
    """trader/portfolio.json from Kite: open NSE positions, and cash as if every trade
    today were paid in full (this pack's view: no MIS leverage). Returns (doc, cash_note)."""
    av = (margins or {}).get("available") or {}
    open_, closed = position_rows(positions)
    today = to_ist(now).date().isoformat()
    opening = _f(av.get("opening_balance"))
    rows = open_ + closed
    if opening is not None and all(r.get("day_buy_value") is not None for r in rows):
        cash = opening + (_f(av.get("intraday_payin")) or 0)
        cash -= sum(_f(r.get("day_buy_value")) or 0 for r in rows)
        cash += sum(_f(r.get("day_sell_value")) or 0 for r in rows)
        note = ("opening balance plus today's pay-ins, minus what today's buys cost in full, plus what today's sells "
                "brought (Kite's own live balance counts MIS buys at their margin only)")
    else:
        cash = _f(av.get("live_balance"))
        if cash is None:
            cash = _f(av.get("cash")) or 0.0
        note = "Kite's live balance (it counts MIS buys at their margin only, so it can overstate the cash)"
    positions_out = []
    for r in open_:
        q = _f(r["quantity"])
        avg = _f(r.get("average_price")) or 0.0
        positions_out.append({"ticker": nse_symbol(r["tradingsymbol"]), "shares": q,
                              "product": r.get("product") or "", "average_price": avg,
                              "lots": [{"id": "%s-kite" % nse_symbol(r["tradingsymbol"]), "date": today,
                                        "shares": q, "cost": round(q * avg, 2)}]})
    return ({"cash": round(cash, 2), "positions": positions_out, "source": "kite", "as_of": iso_z(now)}, note)


def portfolio_diff(old, new):
    lines = []
    oc = _f((old or {}).get("cash"))
    if oc is None:
        lines.append("cash: %s (new file)" % inr(new["cash"], paise=True))
    elif abs(oc - new["cash"]) > 0.005:
        lines.append("cash: %s -> %s" % (inr(oc, paise=True), inr(new["cash"], paise=True)))
    else:
        lines.append("cash: %s (unchanged)" % inr(oc, paise=True))
    held = {}
    for p in (old or {}).get("positions", []):
        t = str(p.get("ticker") or p.get("symbol") or "").upper()
        if t:
            held[t] = held.get(t, 0.0) + (_f(p.get("shares", p.get("qty"))) or 0)
    now_ = {p["ticker"]: p["shares"] for p in new["positions"]}
    for t in sorted(set(held) | set(now_)):
        a, b = held.get(t, 0.0), now_.get(t, 0.0)
        if abs(a - b) > 1e-9:
            lines.append("%s: %s -> %s shares" % (t, _q(a), _q(b)))
        else:
            lines.append("%s: %s shares (unchanged)" % (t, _q(a)))
    return lines


def cmd_sync(a):
    now = parse_now(a.now)
    sess = need_session(a, now)
    positions = kite_get(sess, "/portfolio/positions") or {}
    time.sleep(KITE_PAUSE)
    margins = kite_get(sess, "/user/margins/equity") or {}
    new, note = kite_portfolio(positions, margins, now)
    old = read_json(a.portfolio, "portfolio")
    print("Kite -> %s:" % a.portfolio)
    for line in portfolio_diff(old, new):
        print("  " + line)
    print("Cash here is %s." % note)
    if not a.write:
        print("Nothing written: add --write to save it.")
        return 0
    write_json(a.portfolio, new)
    print("Wrote %s (source kite)." % a.portfolio)
    return 0


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description="Your Kite Connect API, read only (trader-broker). Never places orders.")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--session", default=KITE_SESSION, help="the saved session (default %s)" % KITE_SESSION)
        p.add_argument("--now", help='pretend it is this India time, e.g. "2026-10-19 10:00"')

    p = sub.add_parser("login", help="print the Kite login link")
    common(p)
    p = sub.add_parser("session", help="exchange the login for today's session (run it yourself)")
    common(p)
    p.add_argument("--url", help="the whole address Kite sent the browser to after the login")
    p.add_argument("--request-token", help="or just the request_token from it")
    p = sub.add_parser("status", help="is the session valid, and until when")
    common(p)
    p.add_argument("--check", action="store_true", help="also ask Kite (GET /user/profile)")
    p = sub.add_parser("logout", help="delete the saved session")
    common(p)
    p = sub.add_parser("quote", help="live prices")
    common(p)
    p.add_argument("symbols", nargs="+")
    p.add_argument("--write", action="store_true", help="merge them into trader/prices.json")
    p.add_argument("--prices", default=DEFAULT_PRICES)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("candles", help="candles for one stock, in Yahoo's chart shape")
    common(p)
    p.add_argument("symbol")
    p.add_argument("--interval", choices=["1m", "5m", "1d"], default="5m")
    p.add_argument("--days", type=int, help="calendar days back from --to (default 60 for 5m)")
    p.add_argument("--to", help="the last day, YYYY-MM-DD (default today)")
    p.add_argument("--out", help="write the candles here (.json or .json.gz)")
    p = sub.add_parser("instruments", help="Kite's NSE equity list: token, tick size, lot")
    common(p)
    p.add_argument("symbols", nargs="*")
    p.add_argument("--refresh", action="store_true", help="fetch it again even if today's copy is cached")
    p.add_argument("--instruments", default=KITE_INSTRUMENTS)
    p = sub.add_parser("margins", help="equity margins")
    common(p)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("positions", help="today's positions")
    common(p)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("orders", help="today's order book")
    common(p)
    p.add_argument("--write", action="store_true", help="save it for ticket.py match")
    p.add_argument("--out", default=DEFAULT_ORDERS)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("trades", help="today's fills")
    common(p)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("sync", help="positions and cash -> trader/portfolio.json")
    common(p)
    p.add_argument("--portfolio", default=DEFAULT_PORTFOLIO)
    p.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"login": cmd_login, "session": cmd_session, "status": cmd_status, "logout": cmd_logout,
                "quote": cmd_quote, "candles": cmd_candles, "instruments": cmd_instruments,
                "margins": cmd_margins, "positions": cmd_positions, "orders": cmd_orders, "trades": cmd_trades,
                "sync": cmd_sync}[a.cmd](a)
    except KiteError as e:
        print("kite.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
