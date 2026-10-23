#!/usr/bin/env python3
"""Time Machine for trader-timemachine. Standard library only.

Replays a planned day trade on an NSE stock before you risk a rupee: 10,000
simulated runs of the rest of the session from your entry, plus the same rule
on every past session in the data.

    python3 timemachine.py run                          # trader/plan.json, 10,000 replays, seed 19
    python3 timemachine.py run --write                  # also save trader/timemachine.json
    python3 timemachine.py run --until 15:30            # replay to the close instead of your flat-by time
    python3 timemachine.py run --seed 7 --replays 20000 --json
    python3 timemachine.py run --symbol RELIANCE --date 2026-10-19 --entry 1366.40 \\
        --stop 1357.50 --target 1375.30 --entry-time "10:00 AM"
    python3 timemachine.py chart                        # ASCII fan chart of the replays
    python3 timemachine.py chart --from trader/timemachine.json

How the replays work:
  1. Fetch 60 days of 5-minute bars (Yahoo v8 chart, SYMBOL.NS, range=60d
     interval=5m; one request). NSE's session is 9:15 to 15:30 India time, 75
     bars. The replay ends at --until: by default the plan's flat_by (15:05 in
     the example config), else the 15:30 close. The pool keeps every session
     before the plan's date that has EVERY 5-minute bar from 9:15 through that
     end; sessions with a gap before it, and today, are left out.
  2. The pool: for every bar slot 1..end of the day, each past session's bar as
     ratios to the previous bar's close: (open, high, low, close) / prev close.
  3. One replay starts at the entry price and walks the rest of the session.
     For each bar i it picks a slot j = i + randint(-window, window), kept in
     1..end, then one past bar from pool[j] (random.choice), and scales it to
     the current price. The stop is checked before the target in every bar,
     so a bar that touches both counts as the stop (the conservative call).
  4. Outcomes: "target" or "stop" (whichever was touched first), or "neither"
     when the trade is still open at --until.
     "green" = target first, or above the entry at --until (below for a sell).
     R = the target's R at the target, -1 at the stop, otherwise
     (price at --until - entry) / risk. A gap through the stop still counts
     -1R here; a real fill can be worse.
  5. The price at --until: median and the 5th to 95th percentile of all
     replays (pct(a, q) = sorted a at index int(q * n)). The fan chart uses a
     second run of 2,000 paths with the same seed. The histogram has 24 bins
     between the 1st and 99th percentile.
  6. History: the plan's own rule (an opening-range breakout with the same
     opening range, stop rule and target R) re-run on every one of those past
     sessions, first close beyond the range = entry, same stop-first rule.
     Its stops and targets round onto the NSE tick, like trader-exit's.

The verdict line looks at the replays that hit one side (target or stop) and
compares the target's share of them, target / (target + stop), with the share
that breaks even at the plan's target R: 1 / (1 + R), so 50 % at 1R and 33 % at
2R. The gap, in percentage points:
  less than 5 points    "Close to a coin flip" (1R) / "Close to break-even" (other R)
  5 to 15 points        "Leans toward the target" / "Leans toward the stop"
  15 points or more     "Strongly toward the target" / "Strongly toward the stop"
Below it: a note when more than 40 % of the replays hit neither (the price at
--until decides those), and the average R when its sign goes against the lean.

5-minute bars only: Yahoo keeps 60 days of 5-min bars but only about 7 days of
1-min bars, too few sessions for a fair pool. The run refuses with a plain
message when there are fewer than 20 usable sessions, the entry bar is not in
the data, or the plan's day is not a normal NSE session (a holiday, a weekend,
or a special session like Muhurat trading that does not open at 9:15). Same
plan, same data and same --seed give the same numbers on any machine. A
simulation built from the stock's own recent bars is not a prediction:
results, news and circuit limits can do what the last 60 days never did.

Data: --source auto (the default, or "data_source" in trader/config.json), kite or yahoo.
  kite   your own Kite Connect app (Connect plan), read only: the same 60 sessions
         of 5-minute candles from Kite's historical API (one request), live and
         official. trader-broker/kite.py logs in once a day; Zerodha ends the
         session at 6:00 AM.
  yahoo  https://query2.finance.yahoo.com/v8/finance/chart/<SYMBOL>.NS?range=60d&interval=5m
         (unofficial and free, can lag; falls back to query1 on 429 or 5xx).
  auto   Kite when a session from today is saved, else Yahoo (it says so once).
--offline FILE reads a saved chart JSON (.json or .json.gz) instead.
Times are India time (IST, UTC+5:30, no daylight saving); --now "2026-10-19 10:15" pins the clock.
"""

import argparse
import csv
import datetime
import gzip
import io
import json
import math
import os
import random
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING, ROUND_HALF_UP

UA = "Mozilla/5.0 (compatible; day-trader-skill-ind/1.0; +https://github.com/saitech-systems/day-trader-skill-ind)"
PAUSE = 0.5          # seconds between requests: be polite to free endpoints
BACKOFF = 5.0        # seconds to wait once after a 429
HOSTS = ("query2", "query1")
INTERVAL = 300       # 5-minute bars, in seconds
SESSION_BARS = 75    # 9:15 to 15:30 in 5-minute bars
SESSIONS = 60        # the plan's day and the 59 sessions before it, like Yahoo's range=60d
SOURCE_WORDS = {"kite": "Kite, live", "yahoo": "Yahoo, free, can lag", "offline": "a saved file"}
OPEN_MIN = 9 * 60 + 15
MIN_SESSIONS = 20
SAMPLE_PATHS = 160
BAND_PATHS = 2000
BAND_Q = (0.05, 0.25, 0.5, 0.75, 0.95)
HIST_BINS = 24
COIN_FLIP = 0.05     # verdict thresholds, see the docstring
STRONG = 0.15
MANY_NEITHER = 0.40
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


class TMError(Exception):
    pass


# ---------------------------------------------------------------- BLOCK: NSE calendar (India time, no tz files)

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
        raise TMError('--now wants an India time like "2026-10-19 10:15" (or ...Z for UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)

# ---------------------------------------------------------------- BLOCK: NSE symbols on Yahoo

NSE_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")   # RELIANCE, M&M, BAJAJ-AUTO, 3MINDIA


def nse_symbol(text):
    """"reliance", "RELIANCE.NS", "NSE:RELIANCE" -> "RELIANCE". BSE (.BO) is refused."""
    s = str(text or "").strip().upper()
    if s.startswith("NSE:"):
        s = s[4:]
    if s.endswith(".NS"):
        s = s[:-3]
    if s.endswith(".BO") or s.startswith("BSE:"):
        raise TMError("%s is a BSE listing: this pack trades NSE symbols (like RELIANCE)" % text)
    if not NSE_SYMBOL.match(s):
        raise TMError("%r is not an NSE symbol (like RELIANCE, M&M or BAJAJ-AUTO)" % text)
    return s


def yahoo_symbol(sym):
    """RELIANCE -> RELIANCE.NS (Yahoo's NSE suffix)."""
    return nse_symbol(sym) + ".NS"


def yahoo_quote(sym):
    """For a URL path: M&M -> M%26M.NS."""
    from urllib.parse import quote
    return quote(yahoo_symbol(sym), safe=".-")

# ---------------------------------------------------------------- BLOCK: rupees and ticks

TICK_BANDS = (  # NSE cash-segment tick size by price, circular NSE/CMTR/66133 (from 2025-04-15)
    (Decimal("250"), Decimal("0.01")),
    (Decimal("1000"), Decimal("0.05")),
    (Decimal("5000"), Decimal("0.10")),
    (Decimal("10000"), Decimal("0.50")),
    (Decimal("20000"), Decimal("1.00")),
)
TOP_TICK = Decimal("5.00")


def D(x):
    if isinstance(x, Decimal):
        return x
    if isinstance(x, float):
        return Decimal(repr(x))
    return Decimal(str(x).strip().replace(",", ""))


def tick_size(price):
    """The NSE tick for a price: 0.01 under Rs 250, 0.05 to 1,000, 0.10 to 5,000,
    0.50 to 10,000, 1.00 to 20,000, 5.00 above. NSE reviews each stock's tick
    monthly from the last month's close, so a stock near a band edge can differ."""
    p = D(price)
    for upto, tick in TICK_BANDS:
        if p < upto:
            return tick
    return TOP_TICK


def to_tick(price, rounding=ROUND_HALF_UP, tick=None):
    """Round a price onto the tick grid. ROUND_FLOOR for a long's stop (down,
    away from the entry), ROUND_CEILING for a buy limit, ROUND_HALF_UP for a target."""
    p = D(price)
    t = D(tick) if tick is not None else tick_size(p)
    return ((p / t).quantize(Decimal("1"), rounding=rounding) * t).quantize(Decimal("0.01"))


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

# ---------------------------------------------------------------- clock words

def parse_clock(text, what):
    """'10:15 AM', '10:15am', '15:05' or '3:05' (a bare 1 to 8 o'clock means p.m.) -> minutes after midnight."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*(?:([AaPp])\.?\s*[Mm]\.?)?\s*$", str(text or ""))
    if not m:
        raise TMError("%s wants an India time like 10:15 AM or 15:05, not %r" % (what, text))
    h, mi, ap = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
    if mi > 59 or h > 23 or (ap and not 1 <= h <= 12):
        raise TMError("%s %r is not a clock time" % (what, text))
    if ap == "p" and h != 12:
        h += 12
    elif ap == "a" and h == 12:
        h = 0
    elif not ap and 1 <= h <= 8:
        h += 12
    return h * 60 + mi


def clock12(minutes):
    h, m = divmod(minutes, 60)
    return "%d:%02d %s" % (h % 12 or 12, m, "AM" if h < 12 else "PM")


def clock24(minutes):
    return "%02d:%02d" % divmod(minutes, 60)


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
    """GET one Kite path: the raw body text. Plain-words TMError on failure."""
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
            raise TMError("Kite refused the session (%s). Log in again: python3 trader-broker/kite.py login" % why)
        if e.code == 429:
            raise TMError("Kite says too many requests (%s): wait a few seconds and try again" % why)
        raise TMError("Kite answered HTTP %d for %s (%s)" % (e.code, path, why))
    except urllib.error.URLError as e:
        raise TMError("could not reach Kite (%s): check the connection" % (e.reason,))
    except OSError as e:
        raise TMError("could not reach Kite: %s" % (e,))


def kite_get(sess, path, params=None):
    """GET a Kite JSON path: its "data"."""
    text = kite_request(sess, path, params)
    try:
        doc = json.loads(text)
    except ValueError:
        raise TMError("Kite sent something that is not JSON for %s" % path)
    if doc.get("status") != "success":
        raise TMError("Kite said %s for %s: %s" % (doc.get("error_type") or "error", path, doc.get("message")))
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
        raise TMError("Kite's NSE instrument list came back empty")
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
        raise TMError("Kite candles here are 1m, 5m or 1d, not %r" % interval)
    inst = kite_instruments(sess, now_utc=now_utc).get(sym)
    if not inst:
        raise TMError("Kite has no NSE equity called %s" % sym)
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
        raise TMError("data source is auto, kite or yahoo, not %r" % want)
    if want == "yahoo":
        return "yahoo", None, ""
    sess = kite_session(path, now_utc)
    if sess:
        return "kite", sess, ""
    if want == "kite":
        raise TMError("no Kite session for today: log in first (python3 trader-broker/kite.py login), "
                        "or use --source yahoo")
    return "yahoo", None, "no Kite session today, so free Yahoo data (log in with trader-broker/kite.py for live Kite data)"
# END KITE BLOCK


# ---------------------------------------------------------------- data

def chart_url(sym, host="query2"):
    return "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=60d&interval=5m" % (host, yahoo_quote(sym))


def _sleep(seconds):
    time.sleep(seconds)


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def fetch_chart(sym):
    """One request for 60 days of 5-min bars. query2 first, one retry after a 429,
    then query1 on 429 or 5xx."""
    last = None
    for n, host in enumerate(HOSTS):
        if n:
            _sleep(PAUSE)
        for attempt in (0, 1):
            url = chart_url(sym, host)
            try:
                return _get(url)
            except urllib.error.HTTPError as e:
                last = e.code
                try:
                    e.close()
                except Exception:
                    pass
                if e.code == 429 and attempt == 0:
                    _sleep(BACKOFF)
                    continue
                if e.code == 429 or e.code >= 500:
                    break
                if e.code == 404:
                    raise TMError("Yahoo has no chart for %s (HTTP 404): check the NSE symbol" % yahoo_symbol(sym))
                raise TMError("Yahoo answered HTTP %d for %s" % (e.code, yahoo_symbol(sym)))
            except urllib.error.URLError as e:
                raise TMError("could not reach Yahoo: %s" % e.reason)
            except OSError as e:
                raise TMError("could not reach Yahoo: %s" % e)
    if last == 429:
        raise TMError("Yahoo is rate limiting (HTTP 429). Wait a minute and run it again.")
    raise TMError("Yahoo answered HTTP %s for %s on both hosts. Try again later." % (last, yahoo_symbol(sym)))


def first_session(day, n=SESSIONS):
    """The NSE trading day n sessions back, counting `day` as the first."""
    d, seen = day, 0
    for _ in range(n * 3 + 30):
        if trading_day(d)[0]:
            seen += 1
            if seen == n:
                return d
        d -= datetime.timedelta(days=1)
    return d


def fetch_kite(sess, sym, day, now_utc):
    """The same 60 sessions from Kite: 5-minute candles from 59 sessions before the
    plan's day through it (Kite allows 100 days of them in one request)."""
    return kite_chart(sess, sym, "5m", first_session(day), day, now_utc=now_utc, with_quote=False)


def read_config(path):
    """Only "data_source" matters here; a missing or broken config is fine."""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def read_offline(path, sym):
    """A saved Yahoo chart file (.json or .json.gz), or a folder holding <SYM>-60d5m.json(.gz)."""
    if os.path.isdir(path):
        for name in ("%s-60d5m.json" % sym.upper(), "%s-60d5m.json.gz" % sym.upper()):
            if os.path.exists(os.path.join(path, name)):
                path = os.path.join(path, name)
                break
        else:
            raise TMError("no %s-60d5m.json(.gz) in %s" % (sym.upper(), path))
    if not os.path.exists(path):
        raise TMError("no offline chart file %s" % path)
    opener = gzip.open if path.endswith(".gz") else open
    try:
        with opener(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    except OSError as e:
        raise TMError("could not read %s: %s" % (path, e))


def chart_result(text, sym):
    try:
        data = json.loads(text)
    except ValueError:
        raise TMError("Yahoo returned something that is not JSON for %s" % sym.upper())
    chart = data.get("chart") if isinstance(data, dict) else None
    if not isinstance(chart, dict):
        raise TMError("Yahoo returned something unexpected for %s" % sym.upper())
    res = chart.get("result")
    if not res:
        err = chart.get("error") or {}
        raise TMError("Yahoo has no data for %s: %s" % (sym.upper(), err.get("description") or "unknown symbol"))
    return res[0]


def parse_bars(result, now_utc):
    """Yahoo chart result -> (meta, bars, dropped). Regular-session 5-min bars only,
    oldest first. Dropped: bars with a missing price, bars off the 5-minute grid
    (the live tick, e.g. 10:49:45), bars not complete yet (now < start + 5 min,
    where now is the earlier of --now and the data's own regularMarketTime), and
    bars outside 9:15 to 15:30 India time."""
    meta = result.get("meta") or {}
    stamps = result.get("timestamp") or []
    q = ((result.get("indicators") or {}).get("quote") or [{}])[0] or {}
    cols = [q.get(k) or [] for k in ("open", "high", "low", "close", "volume")]
    as_of = now_utc
    rmt = meta.get("regularMarketTime")
    if isinstance(rmt, (int, float)) and utc_of(rmt) < as_of:
        as_of = utc_of(rmt)
    dropped = {"no_price": 0, "off_grid": 0, "not_complete": 0, "outside_hours": 0}
    bars = {}
    for i, ts in enumerate(stamps):
        o, h, l, c, v = [col[i] if i < len(col) else None for col in cols]
        if ts is None or None in (o, h, l, c):
            dropped["no_price"] += 1
            continue
        ts = int(ts)
        if ts % INTERVAL:
            dropped["off_grid"] += 1
            continue
        start = utc_of(ts)
        local = to_ist(start)
        mins = local.hour * 60 + local.minute - OPEN_MIN
        if mins < 0 or mins >= SESSION_BARS * 5:
            dropped["outside_hours"] += 1
            continue
        if start + datetime.timedelta(seconds=INTERVAL) > as_of:
            dropped["not_complete"] += 1
            continue
        bars[ts] = {"date": local.date().isoformat(), "slot": mins // 5, "start": local.strftime("%H:%M"),
                    "o": o, "h": h, "l": l, "c": c, "v": v or 0}
    return meta, [bars[t] for t in sorted(bars)], dropped


def split_sessions(bars):
    """{date: {slot: bar}}."""
    days = {}
    for b in bars:
        days.setdefault(b["date"], {})[b["slot"]] = b
    return days


def full_sessions(days, before, last=SESSION_BARS - 1):
    """Sessions strictly before the plan's date with every bar from 9:15 through
    bar `last` (the replay's end), oldest first, as lists of bars 0..last; and the
    ones left out for a gap before then."""
    full, short = [], []
    for d in sorted(days):
        if d >= before:
            continue
        s = days[d]
        if all(i in s for i in range(last + 1)):
            full.append((d, [s[i] for i in range(last + 1)]))
        else:
            short.append({"date": d, "bars": len(s)})
    return full, short


# ---------------------------------------------------------------- the simulation

def build_pool(sessions):
    """pool[i] = every past session's bar i as (o, h, l, c) / bar i-1's close, in date order."""
    top = len(sessions[0][1]) if sessions else 0
    pool = [[] for _ in range(top)]
    for _, D in sessions:
        for i in range(1, top):
            pc = D[i - 1]["c"]
            pool[i].append((D[i]["o"] / pc, D[i]["h"] / pc, D[i]["l"] / pc, D[i]["c"] / pc))
    return pool


def pct(a, q):
    return a[min(len(a) - 1, int(q * len(a)))]


def simulate(pool, entry, stop, target, k, last, replays=10000, seed=19, window=2, side="buy"):
    """Monte Carlo replays of bars k+1..last, drawing slots from 1..the pool's last slot."""
    long = side == "buy"
    risk = (entry - stop) if long else (stop - entry)
    r_target = round(((target - entry) if long else (entry - target)) / risk, 4)
    top = len(pool) - 1

    def slot(rng, i):
        return i if window == 0 else min(top, max(1, i + rng.randint(-window, window)))

    rng = random.Random(seed)
    choice = rng.choice
    outcomes, closes, paths = [], [], []
    for n in range(replays):
        p = entry
        outcome = None
        keep = n < SAMPLE_PATHS
        path = [entry]
        for i in range(k + 1, last + 1):
            o, h, l, c = choice(pool[slot(rng, i)])
            hi, lo, cl = p * h, p * l, p * c
            if outcome is None:
                if long:
                    if lo <= stop:
                        outcome = "stop"        # both inside one bar: count the loss (conservative)
                    elif hi >= target:
                        outcome = "target"
                else:
                    if hi >= stop:
                        outcome = "stop"
                    elif lo <= target:
                        outcome = "target"
            p = cl
            if keep:
                path.append(round(p, 2))
        outcomes.append(outcome or "close")
        closes.append(p)
        if keep:
            paths.append(path)
    cnt = {x: outcomes.count(x) for x in ("target", "stop", "close")}
    green = sum(1 for o, p in zip(outcomes, closes)
                if o == "target" or (o == "close" and (p > entry if long else p < entry)))
    Rs = [r_target if o == "target" else -1.0 if o == "stop" else ((p - entry) if long else (entry - p)) / risk
          for o, p in zip(outcomes, closes)]
    srt = sorted(closes)
    # percentile bands per bar for the fan chart: a second run of 2,000 paths, same seed
    rng = random.Random(seed)
    choice = rng.choice
    allp = []
    for n in range(BAND_PATHS):
        p = entry
        path = [entry]
        for i in range(k + 1, last + 1):
            o, h, l, c = choice(pool[slot(rng, i)])
            p = p * c
            path.append(p)
        allp.append(path)
    bands = []
    for j in range(last - k + 1):
        col = sorted(pp[j] for pp in allp)
        bands.append([round(pct(col, q), 2) for q in BAND_Q])
    lo_, hi_ = pct(srt, 0.01), pct(srt, 0.99)
    W = (hi_ - lo_) / HIST_BINS
    bins = [0] * HIST_BINS
    for c in srt:
        b = int((c - lo_) / W) if W > 0 else 0
        if 0 <= b < HIST_BINS:
            bins[b] += 1
    return {
        "replays": replays, "seed": seed, "window": window, "bars_left": last - k,
        "p_target": round(cnt["target"] / replays, 4), "p_stop": round(cnt["stop"] / replays, 4),
        "p_neither": round(cnt["close"] / replays, 4), "p_green": round(green / replays, 4),
        "median_close": round(pct(srt, 0.5), 2), "p5_close": round(pct(srt, 0.05), 2),
        "p95_close": round(pct(srt, 0.95), 2), "avg_R": round(statistics.mean(Rs), 3),
        "r_at_target": r_target, "band_paths": BAND_PATHS, "bands": bands, "sample_paths": paths,
        "hist": {"lo": round(lo_, 2), "hi": round(hi_, 2), "bins": bins},
    }


# ---------------------------------------------------------------- stop rules and the history replay

def tick_away(x, long):
    """Round a stop onto the NSE tick, away from the entry: down for a buy, up for a sell."""
    return float(to_tick(x, ROUND_FLOOR if long else ROUND_CEILING))


def tick_near(x):
    """Round a target onto the NSE tick, half up."""
    return float(to_tick(x, ROUND_HALF_UP))


def opening_range(D, n):
    """(high, low) of the first n bars of a session given as {slot: bar} or a list."""
    rows = [D[i] for i in range(n)]
    return max(b["h"] for b in rows), min(b["l"] for b in rows)


def rule_stop(rule, oh, ol, entry, long):
    r = rule["stop_rule"]
    if r == "or_mid":
        return tick_away((oh + ol) / 2, long)
    if r in ("or_low", "or_high"):
        return tick_away(ol if long else oh, long)
    frac = rule["stop_pct"] / 100.0
    return tick_away(entry * (1 - frac) if long else entry * (1 + frac), long)


def history_replay(sessions, rule, last, side="buy"):
    """The plan's rule on every past session: entry = the first bar after the
    opening range to close beyond it (up to the --until bar), then stop first, then
    target, bar by bar, else the close at --until."""
    long = side == "buy"
    n = rule["or_minutes"] // 5
    trades = []
    for d, D in sessions:
        oh, ol = opening_range(D, n)
        kk = next((i for i in range(n, last + 1) if (D[i]["c"] > oh if long else D[i]["c"] < ol)), None)
        if kk is None:
            continue
        e = round(D[kk]["c"], 2)
        s = rule_stop(rule, oh, ol, e, long)
        risk = (e - s) if long else (s - e)
        if risk <= 0:
            continue
        t = tick_near(e + rule["target_r"] * risk) if long else tick_near(e - rule["target_r"] * risk)
        oc = None
        for b in D[kk + 1:last + 1]:
            if (b["l"] <= s) if long else (b["h"] >= s):
                oc = "stop"
                break
            if (b["h"] >= t) if long else (b["l"] <= t):
                oc = "target"
                break
        cl = D[last]["c"]
        R = ((t - e) if long else (e - t)) / risk if oc == "target" else -1.0 if oc == "stop" else \
            ((cl - e) if long else (e - cl)) / risk
        trades.append({"date": d, "time": clock12(OPEN_MIN + 5 * (kk + 1)), "entry": e, "stop": s, "target": t,
                       "outcome": oc or "close", "R": round(R, 2)})
    return {
        "sessions": len(sessions), "triggered": len(trades),
        "target": sum(1 for x in trades if x["outcome"] == "target"),
        "stop": sum(1 for x in trades if x["outcome"] == "stop"),
        "close": sum(1 for x in trades if x["outcome"] == "close"),
        "green": sum(1 for x in trades if x["R"] > 0),
        "avg_R": round(statistics.mean(x["R"] for x in trades), 3) if trades else None,
        "rule": rule, "trades": trades,
    }


# ---------------------------------------------------------------- the plan

def _num(v, what):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise TMError("the plan needs a number for %s, got %r" % (what, v))
    if not x > 0 or math.isinf(x):
        raise TMError("the plan's %s must be a positive price, got %r" % (what, v))
    return x


def load_plan(a, now_utc):
    """The plan from --plan (trader/plan.json) or from the flags, checked and normalized."""
    if a.symbol:
        missing = [f for f in ("entry", "stop", "target", "entry_time") if getattr(a, f) is None]
        if missing:
            raise TMError("with --symbol also give %s" % ", ".join("--" + m.replace("_", "-") for m in missing))
        raw = {"symbol": a.symbol, "side": a.side, "date": a.date or to_ist(now_utc).date().isoformat(),
               "entry": a.entry, "stop": a.stop, "target": a.target, "entry_time": a.entry_time,
               "setup": a.setup, "opening_range": {"minutes": a.or_minutes}, "stop_rule": a.stop_rule,
               "interval": "5m"}
        src = "flags"
    else:
        if not os.path.exists(a.plan):
            raise TMError("no plan at %s: run /trader-exit first, or pass --symbol --date --entry --stop "
                          "--target --entry-time" % a.plan)
        try:
            with open(a.plan, encoding="utf-8") as fh:
                raw = json.load(fh)
        except ValueError as e:
            raise TMError("%s is not valid JSON: %s" % (a.plan, e))
        if not isinstance(raw, dict):
            raise TMError("%s is not a plan" % a.plan)
        src = a.plan
    return normalize_plan(raw, src)


def normalize_plan(raw, src="flags"):
    """A plan dict (trader/plan.json shape) -> the checked plan the time machine runs."""
    if not str(raw.get("symbol") or "").strip():
        raise TMError("the plan needs an NSE symbol (like RELIANCE), got %r" % raw.get("symbol"))
    sym = nse_symbol(raw.get("symbol"))
    side = str(raw.get("side") or "buy").lower()
    if side not in ("buy", "sell"):
        raise TMError("side must be buy or sell, got %r" % raw.get("side"))
    interval = str(raw.get("interval") or "5m")
    if interval != "5m":
        raise TMError("5-minute bars only (the plan says %s): Yahoo keeps 60 days of 5-min bars but only about "
                      "7 days of 1-min bars, too few sessions for a fair replay pool" % interval)
    try:
        date = datetime.date.fromisoformat(str(raw.get("date") or "")).isoformat()
    except ValueError:
        raise TMError("the plan needs a date like 2026-10-19, got %r" % raw.get("date"))
    entry, stop, target = (_num(raw.get(k), k) for k in ("entry", "stop", "target"))
    long = side == "buy"
    if long and not stop < entry < target:
        raise TMError("a buy needs stop < entry < target (got stop %s, entry %s, target %s)" % (stop, entry, target))
    if not long and not target < entry < stop:
        raise TMError("a sell needs target < entry < stop (got target %s, entry %s, stop %s)" % (target, entry, stop))
    k = None
    if raw.get("entry_time"):
        close_min = parse_clock(raw["entry_time"], "entry_time")
        if (close_min - OPEN_MIN) % 5:
            raise TMError("entry_time %s is not on the 5-minute grid (use the bar's close, e.g. 10:00 AM)"
                          % raw["entry_time"])
        k = (close_min - OPEN_MIN) // 5 - 1
    if raw.get("entry_bar") is not None:
        try:
            kb = int(raw["entry_bar"])
        except (TypeError, ValueError):
            raise TMError("entry_bar must be a bar number, got %r" % raw["entry_bar"])
        if k is not None and kb != k:
            raise TMError("the plan disagrees with itself: entry_bar %d but entry_time %s is bar %d"
                          % (kb, raw["entry_time"], k))
        k = kb
    if k is None:
        raise TMError("the plan needs entry_time (the entry bar's close, e.g. 10:00 AM) or entry_bar")
    if not 0 <= k <= SESSION_BARS - 2:
        raise TMError("the entry must close between 9:20 AM and 3:25 PM to leave bars to replay "
                      "(got %s)" % clock12(OPEN_MIN + 5 * (k + 1)))
    orng = raw.get("opening_range") or {}
    or_min = orng.get("minutes", 15) if isinstance(orng, dict) else 15
    try:
        or_min = int(or_min)
    except (TypeError, ValueError):
        raise TMError("opening_range.minutes must be a number")
    if or_min < 5 or or_min % 5 or or_min >= SESSION_BARS * 5:
        raise TMError("opening_range.minutes must be a multiple of 5 (got %s)" % or_min)
    risk = (entry - stop) if long else (stop - entry)
    return {
        "symbol": sym, "side": side, "date": date, "setup": str(raw.get("setup") or "orb"),
        "entry": entry, "entry_time": clock12(OPEN_MIN + 5 * (k + 1)), "entry_bar": k,
        "stop": stop, "target": target, "risk_per_share": round(risk, 2),
        "target_r": round(((target - entry) if long else (entry - target)) / risk, 2),
        "opening_range": dict(orng, minutes=or_min) if isinstance(orng, dict) else {"minutes": or_min},
        "stop_rule": str(raw.get("stop_rule") or "auto"), "interval": "5m",
        "flat_by": raw.get("flat_by"), "from": src,
    }


def resolve_rule(plan, entry_day):
    """The rule the history replay uses: orb, the plan's opening range, its stop rule
    (or_mid, or_low / or_high, or 'pct' = the plan's stop distance as a percent of
    the entry), and the plan's target R. 'auto' works the stop rule out from the
    plan day's own opening range. None when the setup is not orb."""
    if plan["setup"].lower() != "orb":
        return None
    long = plan["side"] == "buy"
    n = plan["opening_range"]["minutes"] // 5
    rule = {"setup": "orb", "or_minutes": plan["opening_range"]["minutes"], "stop_rule": plan["stop_rule"],
            "target_r": plan["target_r"],
            "stop_pct": round(100.0 * plan["risk_per_share"] / plan["entry"], 4)}
    far = "or_low" if long else "or_high"
    if rule["stop_rule"] == "auto":
        rule["stop_rule"] = "pct"
        if all(i in entry_day for i in range(n)):
            oh, ol = opening_range(entry_day, n)
            if abs(tick_away((oh + ol) / 2, long) - plan["stop"]) < 0.005:
                rule["stop_rule"] = "or_mid"
            elif abs(tick_away(ol if long else oh, long) - plan["stop"]) < 0.005:
                rule["stop_rule"] = far
    elif rule["stop_rule"] in ("or_low", "or_high") and rule["stop_rule"] != far:
        rule["stop_rule"] = "pct"
    elif rule["stop_rule"] not in ("or_mid", far):
        rule["stop_rule"] = "pct"
    if rule["stop_rule"] != "pct":
        del rule["stop_pct"]
    return rule


# ---------------------------------------------------------------- the run

def _until12(mc):
    t = mc.get("until") or "15:30"
    return clock12(int(t[:2]) * 60 + int(t[3:]))


def lean(mc):
    """(gap, share, break_even): the target's share of the replays that hit one side,
    minus the share that breaks even at the target's R. None when nothing hit a side."""
    pt, ps = mc["p_target"], mc["p_stop"]
    if pt + ps <= 0:
        return None
    r = mc.get("r_at_target") or 1.0
    share, even = pt / (pt + ps), 1.0 / (1.0 + r)
    return share - even, share, even


def verdict(mc):
    """One line from the numbers, see the docstring for the thresholds."""
    pt, ps = mc["p_target"], mc["p_stop"]
    lv = lean(mc)
    if lv is None:
        return "No replay reached the target or the stop by %s: the price at %s decides every one." % (
            _until12(mc), _until12(mc))
    d, share, even = lv
    r = mc.get("r_at_target") or 1.0
    one_r = abs(r - 1.0) < 0.005
    if abs(d) < COIN_FLIP:
        head = "Close to a coin flip" if one_r else "Close to break-even"
    elif abs(d) < STRONG:
        head = "Leans toward the target" if d > 0 else "Leans toward the stop"
    else:
        head = "Strongly toward the target" if d > 0 else "Strongly toward the stop"
    odds = "target first %.1f %% vs stop first %.1f %%" % (100 * pt, 100 * ps)
    if one_r:
        return "%s: %s." % (head, odds)
    return ("%s: at %gR the target has to come first in %.0f %% of the replays that hit one side; "
            "it did in %.0f %% (%s)." % (head, round(r, 2), 100 * even, 100 * share, odds))


def verdict_notes(mc):
    """The lines printed right below the verdict, so nothing reads as one-sided."""
    notes = []
    pn = mc.get("p_neither", 0)
    if pn > MANY_NEITHER and lean(mc) is not None:
        notes.append("%.1f %% of the replays hit neither by %s; the price at %s decides those."
                     % (100 * pn, _until12(mc), _until12(mc)))
    lv = lean(mc)
    avg = mc.get("avg_R")
    if lv and abs(lv[0]) >= COIN_FLIP and avg is not None and avg != 0 and (avg > 0) != (lv[0] > 0):
        notes.append("Average R is %+.3f all the same: the replays that hit neither pull it the other way." % avg)
    return notes


def replay_end(plan, until, end_min, warn):
    """(minutes, where it came from): --until, else the plan's flat_by, else the close."""
    if until is not None:
        m = parse_clock(until, "--until")
        if (m - OPEN_MIN) % 5 or m > end_min:
            raise TMError("--until must be on the 5-minute grid and no later than %s" % clock12(end_min))
        return m, "until"
    flat = plan.get("flat_by")
    if flat:
        try:
            m = parse_clock(flat, "flat_by")
        except TMError:
            warn.append("the plan's flat_by %r is not a time: the replays run to the %s close"
                        % (flat, clock12(end_min)))
            return end_min, "close"
        if m >= end_min:
            return end_min, "close"
        grid = OPEN_MIN + (m - OPEN_MIN) // 5 * 5
        if grid != m:
            warn.append("the plan's flat_by %s is between bars: the replays end at %s" % (flat, clock12(grid)))
        return grid, "flat_by"
    return end_min, "close"


def time_machine(text, plan, now_utc, replays=10000, seed=19, window=2, until=None, source="yahoo"):
    """Chart JSON text + a normalized plan -> the timemachine.json document."""
    if not 1 <= replays <= 1000000:
        raise TMError("--replays must be between 1 and 1,000,000")
    if not 0 <= window <= 10:
        raise TMError("--window must be between 0 and 10 bars")
    sym = plan["symbol"]
    day = datetime.date.fromisoformat(plan["date"])
    ok, why = trading_day(day)
    if not ok:
        raise TMError("%s is not an NSE session (%s): plan a trading day" % (plan["date"], why))
    s = session(day)
    if s[0] != OPEN:
        raise TMError("%s is a special session (%s, %s to %s): the time machine replays normal 9:15 sessions only"
                      % (plan["date"], why.replace("special session: ", ""), s[0].strftime("%H:%M"),
                         s[1].strftime("%H:%M")))
    result = chart_result(text, sym)
    meta, bars, dropped = parse_bars(result, now_utc)
    msym = str(meta.get("symbol") or "").upper()
    if msym and msym != yahoo_symbol(sym):
        raise TMError("the data is for %s but the plan is for %s (%s)" % (msym, sym, yahoo_symbol(sym)))
    gran = meta.get("dataGranularity")
    if gran and gran != "5m":
        raise TMError("the data has %s bars; the time machine needs 5-minute bars" % gran)
    if not bars:
        raise TMError("no complete 5-minute bars for %s in the data" % sym)
    k = plan["entry_bar"]
    end_min = s[1].hour * 60 + s[1].minute
    warn = []
    until_min, until_from = replay_end(plan, until, end_min, warn)
    last = (until_min - OPEN_MIN) // 5 - 1
    if last <= k:
        raise TMError("--until %s leaves nothing to replay after the %s entry" % (clock12(until_min),
                                                                             plan["entry_time"]))
    days = split_sessions(bars)
    entry_day = days.get(plan["date"], {})
    if k not in entry_day:
        reason = ("no bars for that day" if not entry_day else
                  "the data stops at the %s bar" % entry_day[max(entry_day)]["start"])
        raise TMError("the entry is not in the data: no complete %s bar (closing %s) on %s for %s (%s)"
                      % (clock24(OPEN_MIN + 5 * k), plan["entry_time"], plan["date"], sym, reason))
    eb = entry_day[k]
    if not eb["l"] - 0.01 <= plan["entry"] <= eb["h"] + 0.01:
        warn.append("the plan's entry %.2f is outside that bar's range %.2f to %.2f"
                    % (plan["entry"], eb["l"], eb["h"]))
    sessions, short = full_sessions(days, plan["date"], last)
    if len(sessions) < MIN_SESSIONS:
        raise TMError("only %d session(s) of %s with every bar to %s before %s in the data; the time machine "
                      "needs at least %d" % (len(sessions), sym, clock12(until_min), plan["date"], MIN_SESSIONS))
    pool = build_pool(sessions)
    mc = simulate(pool, plan["entry"], plan["stop"], plan["target"], k, last, replays, seed, window, plan["side"])
    mc = dict(mc, until=clock24(until_min), until_from=until_from, pool_sessions=len(sessions),
              pool_from=sessions[0][0], pool_to=sessions[-1][0],
              band_times=[clock24(OPEN_MIN + 5 * (j + 1)) for j in range(k, last + 1)])
    rule = resolve_rule(plan, entry_day)
    plan_out = {kk: v for kk, v in plan.items() if v is not None}
    if rule:
        plan_out["stop_rule"] = rule["stop_rule"]
        history = history_replay(sessions, rule, last, plan["side"])
    else:
        history = {"sessions": len(sessions), "triggered": 0, "rule": None, "trades": [],
                   "note": "skipped: the history replay knows the opening-range breakout (orb) only, "
                           "this plan's setup is %r" % plan["setup"]}
    last_bar = bars[-1]
    return {
        "created_by": "trader-timemachine",
        "created_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
        "data": {"symbol": sym, "yahoo_symbol": msym or yahoo_symbol(sym), "interval": "5m", "range": "60d",
                 "last_bar": "%s %s IST" % (last_bar["date"], last_bar["start"]),
                 "dropped": dropped, "short_sessions_left_out": short},
        "plan": plan_out,
        "mc": mc,
        "history": history,
        "verdict": verdict(mc),
        "verdict_notes": verdict_notes(mc),
        "warnings": warn,
        "note": ("A simulation built from %s's own last %d sessions. It is not a prediction: results, news and "
                 "circuit limits can do things those sessions never did. Same seed, same numbers."
                 % (sym, len(sessions))),
    }


# ---------------------------------------------------------------- output

def pc(x):
    return "%.1f %%" % (100.0 * x)


def report(doc):
    p, m, h = doc["plan"], doc["mc"], doc["history"]
    long = p["side"] == "buy"
    until = clock12(int(m["until"][:2]) * 60 + int(m["until"][3:]))
    wd = WEEKDAYS[datetime.date.fromisoformat(p["date"]).weekday()]
    r = p["target_r"]
    end = {"flat_by": "%s, your flat-by time" % until, "close": "%s, the close" % until}.get(m.get("until_from"), until)
    lines = [
        "%s %s, %s %s: entry %.2f at %s (bar %d), stop %.2f, target %.2f (%sR, risk %.2f a share)."
        % (p["symbol"], p["side"], wd, p["date"], p["entry"], p["entry_time"], p["entry_bar"], p["stop"],
           p["target"], ("%g" % r), p["risk_per_share"]),
        "%s replays of the rest of the session, %s to %s (%d five-minute bars), seed %d."
        % ("{:,}".format(m["replays"]), p["entry_time"], end, m["bars_left"], m["seed"]),
        "Each bar is drawn whole from %s's own 5-min bars at the same time of day (+-%d bars) over its last "
        "%d sessions with every bar from 9:15 AM to %s (%s to %s)."
        % (p["symbol"], m["window"], m["pool_sessions"], until, m["pool_from"], m["pool_to"]),
        "",
        "  target first   %s" % pc(m["p_target"]),
        "  stop first     %s" % pc(m["p_stop"]),
        "  neither        %-8s still open at %s" % (pc(m["p_neither"]), until),
        "  green          %-8s target first, or %s the entry at %s" % (pc(m["p_green"]),
                                                                       "above" if long else "below", until),
        "",
        "  Price at %s: median %.2f, 90 %% of replays between %.2f and %.2f."
        % (until, m["median_close"], m["p5_close"], m["p95_close"]),
        "  Average R: %+.3f (R = the %.2f risk: %sR at the target, -1R at the stop)."
        % (m["avg_R"], p["risk_per_share"], ("+%g" % m["r_at_target"]), ),
        "",
    ]
    if h.get("rule"):
        ru = h["rule"]
        stop_words = {"or_mid": "opening-range midpoint", "or_low": "opening-range low",
                      "or_high": "opening-range high"}.get(ru["stop_rule"],
                                                           "%.2f %% from the entry" % ru.get("stop_pct", 0))
        if h["triggered"]:
            lines.append("History: the same rule (%d-min opening range, stop at the %s, %gR target) on each of "
                         "those %d real sessions fired %d times: %d target, %d stop, %d neither. Average R %+.3f."
                         % (ru["or_minutes"], stop_words, ru["target_r"], h["sessions"], h["triggered"],
                            h["target"], h["stop"], h["close"], h["avg_R"]))
        else:
            lines.append("History: the same rule never fired in those %d sessions." % h["sessions"])
    else:
        lines.append("History: " + h.get("note", "skipped."))
    lines += ["", doc["verdict"]] + list(doc.get("verdict_notes") or []) + [""]
    for w in doc.get("warnings") or []:
        lines.append("Note: " + w + ".")
    if doc["data"]["short_sessions_left_out"]:
        lines.append("Left out %d session(s) with a gap before %s: %s."
                     % (len(doc["data"]["short_sessions_left_out"]), until,
                        ", ".join(x["date"] for x in doc["data"]["short_sessions_left_out"])))
    src = doc.get("source")
    if src in SOURCE_WORDS:
        lines.append("Bars: %s." % SOURCE_WORDS[src])
    lines.append(doc["note"] + " Repeat this run with --seed %d." % m["seed"])
    return lines


def render_chart(doc, height=17):
    """ASCII fan chart of the bands: one column per 5-min bar."""
    p, m = doc["plan"], doc["mc"]
    bands, times = m["bands"], m["band_times"]
    lo = min([b[0] for b in bands] + [p["stop"], p["target"]])
    hi = max([b[4] for b in bands] + [p["stop"], p["target"]])
    pad = (hi - lo) * 0.04 or 0.01
    lo, hi = lo - pad, hi + pad
    step = (hi - lo) / (height - 1)

    def row(price):
        return max(0, min(height - 1, int(round((hi - price) / step))))
    grid = [[" "] * len(bands) for _ in range(height)]
    for j, b in enumerate(bands):
        r5, r25, r50, r75, r95 = [row(x) for x in b]
        for r in range(r95, r5 + 1):
            grid[r][j] = "."
        for r in range(r75, r25 + 1):
            grid[r][j] = ":"
        grid[r50][j] = "*"
    marks = {row(p["target"]): "target", row(p["stop"]): "stop"}
    if row(p["entry"]) not in marks:
        marks[row(p["entry"])] = "entry"
    for r in (row(p["target"]), row(p["stop"])):
        grid[r] = ["-" if ch == " " else ch for ch in grid[r]]
    out = ["%s %s, %s replay paths from the %s entry (seed %d)" % (
        p["symbol"], p["date"], "{:,}".format(m.get("band_paths", BAND_PATHS)), p["entry_time"], m["seed"])]
    for r in range(height):
        label = marks.get(r)
        price = {"target": p["target"], "stop": p["stop"], "entry": p["entry"]}.get(label, hi - r * step)
        left = ("%9.2f |" % price) if (label or r in (0, height - 1)) else "          |"
        out.append(left + "".join(grid[r]) + ("  " + label if label else ""))
    axis = [" "] * len(bands)
    for j in sorted({0, len(bands) // 2, len(bands) - 1}):
        t = times[j]
        hh, mm = int(t[:2]), t[3:]
        lab = "%d:%s" % (hh % 12 or 12, mm)
        s = max(0, min(j, len(bands) - len(lab)))
        axis[s:s + len(lab)] = list(lab)
    out.append("          +" + "-" * len(bands))
    out.append("           " + "".join(axis))
    out.append("* median   : middle half of the paths   . 90 % of the paths   - stop and target")
    return out


# ---------------------------------------------------------------- commands

def _write(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
        fh.write("\n")


def compute(a):
    now = parse_now(a.now)
    plan = load_plan(a, now)
    note = ""
    if a.offline:
        text, source = read_offline(a.offline, plan["symbol"]), "offline"
    else:
        source, sess, note = pick_source(a.source, read_config(a.config))   # the real clock: today's login
        if sess:
            text = fetch_kite(sess, plan["symbol"], datetime.date.fromisoformat(plan["date"]), now)
        else:
            text = fetch_chart(plan["symbol"])
    doc = time_machine(text, plan, now, a.replays, a.seed, a.window, a.until, source)
    if note:
        doc["source_note"] = note
    return doc


def cmd_run(a):
    doc = compute(a)
    if a.write:
        _write(a.write, doc)
    if a.json:
        slim = dict(doc, mc={k: v for k, v in doc["mc"].items() if k != "sample_paths"})
        print(json.dumps(slim, indent=2))
    else:
        if doc.get("source_note"):
            print("Note: %s." % doc["source_note"])
        print("\n".join(report(doc)))
        if a.write:
            print("Wrote %s." % a.write)
        print("Next: /trader-budget sizes it to your budget and your max loss per trade.")
    return 0


def cmd_chart(a):
    if a.from_file:
        if not os.path.exists(a.from_file):
            raise TMError("no %s: run timemachine.py run --write first" % a.from_file)
        try:
            with open(a.from_file, encoding="utf-8") as fh:
                doc = json.load(fh)
            doc["mc"]["bands"]
        except (ValueError, KeyError, TypeError):
            raise TMError("%s is not a time machine result" % a.from_file)
    else:
        doc = compute(a)
        if doc.get("source_note"):
            print("Note: %s." % doc["source_note"])
    print("\n".join(render_chart(doc)))
    print("\n".join([doc.get("verdict", "")] + list(doc.get("verdict_notes") or [])))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Time Machine: replay a planned day trade (trader-timemachine).")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--plan", default="trader/plan.json", help="the plan from trader-exit")
        p.add_argument("--symbol", help="or give the plan as flags: NSE symbol, like RELIANCE")
        p.add_argument("--side", default="buy", choices=["buy", "sell"])
        p.add_argument("--date", help="the plan's day, YYYY-MM-DD (default today, India time)")
        p.add_argument("--entry", type=float)
        p.add_argument("--stop", type=float)
        p.add_argument("--target", type=float)
        p.add_argument("--entry-time", help='the entry bar\'s close, e.g. "10:00 AM"')
        p.add_argument("--setup", default="orb")
        p.add_argument("--or-minutes", type=int, default=15)
        p.add_argument("--stop-rule", default="auto", choices=["auto", "or_mid", "or_low", "or_high", "pct"])
        p.add_argument("--replays", type=int, default=10000)
        p.add_argument("--seed", type=int, default=19)
        p.add_argument("--window", type=int, default=2, help="draw from the same time of day +- this many bars")
        p.add_argument("--until", help="end each replay at this India time (default: the plan's flat_by, "
                                       "else 15:30, the close)")
        p.add_argument("--offline", metavar="FILE", help="a saved Yahoo chart JSON (.json or .json.gz), no network")
        p.add_argument("--source", choices=KITE_SOURCES,
                       help="auto (default: Kite with a session from today, else Yahoo), kite or yahoo")
        p.add_argument("--config", default="trader/config.json", help="for its data_source")
        p.add_argument("--now", help='pretend it is this time ("2026-10-19 10:15" India time, or ISO with Z)')

    p = sub.add_parser("run", help="replay the plan: 10,000 simulated sessions plus the history")
    common(p)
    p.add_argument("--json", action="store_true")
    p.add_argument("--write", nargs="?", const="trader/timemachine.json", metavar="PATH",
                   help="save the result (default trader/timemachine.json)")
    p = sub.add_parser("chart", help="ASCII fan chart of the replays")
    common(p)
    p.add_argument("--from", dest="from_file", nargs="?", const="trader/timemachine.json", metavar="PATH",
                   help="draw a saved result instead of running again")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"run": cmd_run, "chart": cmd_chart}[a.cmd](a)
    except TMError as e:
        print("timemachine.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
