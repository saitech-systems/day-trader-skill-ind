#!/usr/bin/env python3
"""Insider and deals tracker for trader-insider. Standard library only.

Reads two kinds of public NSE records: the insider trades that promoters,
directors, key managerial personnel and employees must disclose under SEBI's
insider trading rules (PIT), and the bulk and block deals NSE publishes every
trading day.

    python3 insider.py pit                                # the last 7 days, every company
    python3 insider.py pit --symbol DEMO --days 30        # one company
    python3 insider.py pit --category insiders --market-only   # promoters, directors, KMP: market trades
    python3 insider.py deals                              # today's bulk and block deals
    python3 insider.py deals --from 2026-10-13 --to 2026-10-19 --kind block
    python3 insider.py new                                # only what is new since the last check

Where the data comes from:
  - Insider trades (SEBI PIT disclosures):
    https://www.nseindia.com/api/corporates-pit?index=equities&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY
    The page: https://www.nseindia.com/companies-listing/corporate-filings-insider-trading
    Its field names are the best known ones, NOT verified against a live
    answer when this was written: every field is read defensively, and a
    missing one is left blank, never guessed.
  - Today's deals: https://nsearchives.nseindia.com/content/equities/bulk.csv
    and block.csv (static files, no cookies needed).
  - Older deals: https://www.nseindia.com/api/historicalOR/bulk-block-short-deals?
    optionType=bulk_deals|block_deals&from=DD-MM-YYYY&to=DD-MM-YYYY
    The page: https://www.nseindia.com/report-detail/display-bulk-and-block-deals
  - www.nseindia.com sits behind bot protection that blocks many scripts. The
    tool visits the home page first for cookies, sends browser headers and
    waits a second between calls. If NSE still refuses, it says so in one
    line, names the page to open in a browser, and never retries in a loop.

What these records are, and are not:
  - PUBLIC disclosures. An insider tells the company within 2 trading days of
    a trade, and the company tells the exchange within 2 trading days after
    that, so a row can be up to 4 trading days old when it appears, often more.
  - Reading them fast is not inside information. There is none here, and a
    promoter's buy is not a buy signal.
  - A pledge is not a trade: shares put up (or released) as loan collateral.

State: `new` remembers what it has shown in trader/state/insider.json, so each
row alerts once. Offline: --offline PATH reads saved answers instead of the
network: a folder with pit.json, bulk.csv, block.csv, bulk_deals.json and
block_deals.json, or one of those files. The tests use it.
"""

import argparse
import csv
import datetime
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

NSE_HOME = "https://www.nseindia.com/"
PIT_API = "https://www.nseindia.com/api/corporates-pit"
DEALS_API = "https://www.nseindia.com/api/historicalOR/bulk-block-short-deals"
ARCHIVE_CSV = "https://nsearchives.nseindia.com/content/equities/%s.csv"
PIT_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-insider-trading"
DEALS_PAGE = "https://www.nseindia.com/report-detail/display-bulk-and-block-deals"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")
TIMEOUT = 30
PAUSE = 1.0           # seconds between calls to NSE
SLEEP = time.sleep    # tests swap this out
SEBI_DAYS = 4         # 2 trading days insider -> company, 2 more company -> exchange
KEY_PEOPLE = ("promoter", "promoter group", "director", "KMP")
CATEGORY_ALIASES = {"insiders": KEY_PEOPLE, "promoters": ("promoter", "promoter group"),
                    "promoter-group": ("promoter group",), "kmp": ("KMP",),
                    "employee": ("designated employee",), "employees": ("designated employee",),
                    "relative": ("relative",), "relatives": ("relative",)}
NOTE_PIT = ("Public disclosures under SEBI's insider trading rules, read fast: up to 4 trading days "
            "after the trade, often more. Not inside information, and not a buy or sell signal.")
NOTE_DEALS = ("Bulk and block deals are NSE's public record of big trades by named clients: who "
              "traded, not why, and not what comes next. Not a buy or sell signal.")
KEEP_DAYS = 90        # state entries older than this are forgotten


class InsiderError(Exception):
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
        raise InsiderError('--now wants an India time like "2026-10-19 10:15" (or ...Z for UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


# ---------------------------------------------------------------- NSE symbols and rupees

NSE_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")   # RELIANCE, M&M, BAJAJ-AUTO, 3MINDIA


def nse_symbol(text):
    """"reliance", "RELIANCE.NS", "NSE:RELIANCE" -> "RELIANCE". BSE (.BO) is refused."""
    s = str(text or "").strip().upper()
    if s.startswith("NSE:"):
        s = s[4:]
    if s.endswith(".NS"):
        s = s[:-3]
    if s.endswith(".BO") or s.startswith("BSE:"):
        raise InsiderError("%s is a BSE listing: this pack trades NSE symbols (like RELIANCE)" % text)
    if not NSE_SYMBOL.match(s):
        raise InsiderError("%r is not an NSE symbol (like RELIANCE, M&M or BAJAJ-AUTO)" % text)
    return s


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


def count(n):
    """6,50,000: a share count with Indian grouping."""
    return inr(int(n), paise=False)[3:]


def amount(v):
    """Rs 3,40,12,500 (3.40 crore): rupees, with the crore or lakh figure people say."""
    out = inr(v, paise=False)
    if v >= 1e7:
        out += " (%.2f crore)" % (v / 1e7)
    elif v >= 1e5:
        out += " (%.2f lakh)" % (v / 1e5)
    return out


# ---------------------------------------------------------------- small parsers

DATE_FORMATS = ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M",
                "%d-%m-%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y", "%d %b %Y")


def when(text):
    """'16-Oct-2026 18:20', '01-DEC-2025', '2026-10-19', '19-10-2026' -> datetime, or None."""
    t = " ".join(str(text or "").split())
    if not t or t in ("-", "NA", "N.A.", "Nil", "null", "None"):
        return None
    if "T" in t and re.match(r"^\d{4}-\d{2}-\d{2}T", t):
        t = t[:19].replace("T", " ")
    for fmt in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(t, fmt)
        except ValueError:
            continue
    return None


def day_of(text):
    w = when(text)
    return w.date().isoformat() if w else None


def number(x):
    """'25,000', 25000, '41.2', '-', None -> float or None."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    t = str(x).strip().replace(",", "").replace("Rs.", "").replace("Rs", "").strip()
    if t in ("", "-", "NA", "N.A.", "Nil", "nil", "null"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def pick(row, *keys):
    """The first non-empty value among several possible field names."""
    for k in keys:
        v = row.get(k)
        if v not in (None, "", "-"):
            return v
    return None


def parse_date_arg(text, what):
    """--from / --to: 2026-10-19 or 19-10-2026."""
    d = when(text)
    if d is None:
        raise InsiderError("%s wants a date like 2026-10-19, not %r" % (what, text))
    return d.date()


def trading_days_after(start, end):
    """Trading days after `start`, up to and including `end` (0 when end <= start).
    NSE's 2026 holidays count; other years count weekdays only."""
    n, d = 0, start
    while d < end:
        d += datetime.timedelta(days=1)
        if trading_day(d)[0]:
            n += 1
    return n


# ---------------------------------------------------------------- insider trades (SEBI PIT)

def category_of(raw):
    t = str(raw or "").strip().lower()
    if "promoter group" in t or "promoters group" in t:
        return "promoter group"
    if "promoter" in t:
        return "promoter"
    if "director" in t:
        return "director"
    if "key managerial" in t or re.search(r"\bkmp\b", t):
        return "KMP"
    if "designated" in t or "employee" in t:
        return "designated employee"
    if "relative" in t:
        return "relative"
    return t or "other"


def action_of(raw):
    t = str(raw or "").strip().lower()
    if "invoc" in t:
        return "pledge invoked"
    if "revo" in t or "release" in t:
        return "pledge revoked"
    if "pledge" in t:
        return "pledge"
    if t.startswith(("buy", "purchase", "acqui")):
        return "buy"
    if t.startswith(("sell", "sale", "dispos")):
        return "sell"
    return t or "other"


def mode_of(raw):
    t = " ".join(str(raw or "").replace("-", " ").split()).lower()
    if not t:
        return None
    if t.startswith("market purchase") or t == "market buy":
        return "market purchase"
    if t.startswith("market sale") or t == "market sell":
        return "market sale"
    if "off market" in t:
        return "off market"
    if "esop" in t or "employee stock" in t:
        return "ESOP"
    if "inter se" in t:
        return "inter-se transfer"
    return t


def pit_rows(doc):
    """NSE's corporates-pit answer ({"data": [...]} or a bare list) -> clean rows, newest first."""
    data = doc.get("data", []) if isinstance(doc, dict) else doc
    if not isinstance(data, list):
        raise InsiderError("the insider answer has no list of disclosures")
    out = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        sym = str(pick(raw, "symbol", "SYMBOL") or "").strip().upper()
        person = " ".join(str(pick(raw, "acqName", "acquirerName", "name") or "").split())
        cat_raw = pick(raw, "personCategory", "category")
        act_raw = pick(raw, "tdpTransactionType", "transactionType", "acqMode")
        mode_raw = pick(raw, "acqMode", "modeOfAcquisition")
        shares = number(pick(raw, "secAcq", "noOfSecurities", "buyQuantity", "sellquantity"))
        value = number(pick(raw, "secVal", "value", "buyValue", "sellValue"))
        frm, to = day_of(pick(raw, "acqfromDt", "fromDate")), day_of(pick(raw, "acqtoDt", "toDate"))
        trade_day = to or frm
        intim = day_of(pick(raw, "intimDt", "intimationDate"))
        shown = when(pick(raw, "date", "broadcastDate", "disseminationDate"))
        row = {
            "symbol": sym, "company": " ".join(str(pick(raw, "company", "companyName") or "").split()),
            "person": person, "category": category_of(cat_raw), "category_raw": cat_raw,
            "action": action_of(act_raw), "action_raw": act_raw,
            "mode": mode_of(mode_raw), "mode_raw": mode_raw,
            "security": pick(raw, "secType", "securityType"),
            "shares": int(shares) if shares is not None else None, "value_inr": value,
            "price": round(value / shares, 2) if value and shares else None,
            "before_shares": number(pick(raw, "befAcqSharesNo")),
            "before_pct": number(pick(raw, "befAcqSharesPer")),
            "after_shares": number(pick(raw, "afterAcqSharesNo")),
            "after_pct": number(pick(raw, "afterAcqSharesPer")),
            "from": frm, "to": to, "intimated": intim,
            "disclosed": shown.strftime("%Y-%m-%d %H:%M") if shown else None,
            "xbrl": pick(raw, "xbrl"), "remarks": pick(raw, "remarks"),
        }
        row["pledge"] = row["action"].startswith("pledge")
        row["open_market"] = row["mode"] in ("market purchase", "market sale")
        row["key_person"] = row["category"] in KEY_PEOPLE
        row["highlight"] = (row["key_person"] and row["open_market"] and row["action"] in ("buy", "sell"))
        row["days_to_disclose"] = row["company_days"] = row["insider_days"] = None
        if trade_day and shown:
            t0 = datetime.date.fromisoformat(trade_day)
            row["days_to_disclose"] = trading_days_after(t0, shown.date())
            if intim:
                row["insider_days"] = trading_days_after(t0, datetime.date.fromisoformat(intim))
                row["company_days"] = trading_days_after(datetime.date.fromisoformat(intim), shown.date())
        row["late"] = bool(row["days_to_disclose"] is not None and row["days_to_disclose"] > SEBI_DAYS)
        row["url"] = row["xbrl"] or PIT_PAGE
        did = pick(raw, "did", "pid", "seqNo", "id")
        key = did or "|".join(str(row[k]) for k in ("symbol", "person", "action", "from", "to", "shares",
                                                      "disclosed"))
        row["id"] = "pit:" + (str(did) if did else hashlib.sha256(key.encode("utf-8")).hexdigest()[:16])
        out.append(row)
    out.sort(key=lambda r: (r["disclosed"] or "", r["symbol"], r["person"]), reverse=True)
    return out


def in_window(rows, start, end):
    """Rows disclosed from start to end (NSE filters online; a saved answer may hold more)."""
    lo, hi = start.isoformat(), end.isoformat()
    return [r for r in rows if not r["disclosed"] or lo <= r["disclosed"][:10] <= hi]


def categories_wanted(names):
    """--category values -> the set of clean categories, or None for all."""
    if not names:
        return None
    out = set()
    for n in names:
        k = n.strip().lower()
        if k == "all":
            return None
        if k in CATEGORY_ALIASES:
            out.update(CATEGORY_ALIASES[k])
        else:
            c = category_of(k)
            if c not in ("promoter", "promoter group", "director", "KMP", "designated employee", "relative"):
                raise InsiderError("--category is promoter, promoter-group, promoters, director, kmp, "
                                   "employee, relative, insiders or all (got %r)" % n)
            out.add(c)
    return out


def select_pit(rows, symbols=None, categories=None, market_only=False, action=None):
    out = []
    for r in rows:
        if symbols and r["symbol"] not in symbols:
            continue
        if categories is not None and r["category"] not in categories:
            continue
        if market_only and not r["open_market"]:
            continue
        if action and not (r["action"] == action or (action == "pledge" and r["pledge"])):
            continue
        out.append(r)
    return out


# ---------------------------------------------------------------- bulk and block deals

def deal_row(kind, date, sym, name, client, side, qty, price, remarks):
    q, p = number(qty), number(price)
    s = str(side or "").strip().lower()
    side = "buy" if s.startswith("b") else ("sell" if s.startswith("s") else s or None)
    d = day_of(date)
    sym = str(sym or "").strip().upper()
    client = " ".join(str(client or "").split())
    row = {"kind": kind, "date": d, "symbol": sym, "name": " ".join(str(name or "").split()),
           "client": client, "side": side, "qty": int(q) if q is not None else None, "price": p,
           "value_inr": round(q * p, 2) if q is not None and p is not None else None,
           "remarks": None if str(remarks or "").strip() in ("", "-", "None") else str(remarks).strip()}
    key = "|".join(str(row[k]) for k in ("date", "symbol", "client", "side", "qty", "price"))
    row["id"] = "%s:%s" % (kind, hashlib.sha256(key.encode("utf-8")).hexdigest()[:16])
    return row


def deals_from_csv(text, kind):
    """NSE's archive bulk.csv / block.csv -> rows. Headers: Date, Symbol, Security Name,
    Client Name, Buy/Sell, Quantity Traded, Trade Price / Wght. Avg. Price, Remarks."""
    text = text.lstrip("﻿")
    reader = csv.reader(io.StringIO(text))
    head = None
    out = []
    for cells in reader:
        cells = [c.strip() for c in cells]
        if not any(cells):
            continue
        if head is None:
            head = [c.lower() for c in cells]
            if "symbol" not in head:
                raise InsiderError("%s.csv does not look like NSE's deals file (header: %s)"
                                   % (kind, ", ".join(cells[:4])))
            continue
        if len(cells) < 3 or "no record" in " ".join(cells).lower():
            continue
        row = dict(zip(head, cells))

        def col(*names):
            for n in names:
                for h, v in row.items():
                    if h.startswith(n):
                        return v
            return None
        out.append(deal_row(kind, col("date"), col("symbol"), col("security name"), col("client name"),
                            col("buy/sell", "buy / sell"), col("quantity"), col("trade price", "price"),
                            col("remarks")))
    return out


def deals_from_json(doc, kind):
    """NSE's historical bulk/block answer ({"data": [...]} or a bare list) -> rows."""
    data = doc.get("data", []) if isinstance(doc, dict) else doc
    if not isinstance(data, list):
        raise InsiderError("the %s deals answer has no list of deals" % kind)
    return [deal_row(kind, r.get("BD_DT_DATE"), r.get("BD_SYMBOL"), r.get("BD_SCRIP_NAME"),
                     r.get("BD_CLIENT_NAME"), r.get("BD_BUY_SELL"), r.get("BD_QTY_TRD"), r.get("BD_TP_WATP"),
                     r.get("BD_REMARKS"))
            for r in data if isinstance(r, dict)]


def sort_deals(rows):
    return sorted(rows, key=lambda r: (r["date"] or "", r["value_inr"] or 0), reverse=True)


def select_deals(rows, symbols=None, min_value_cr=None):
    out = []
    for r in rows:
        if symbols and r["symbol"] not in symbols:
            continue
        if min_value_cr and (r["value_inr"] or 0) < min_value_cr * 1e7:
            continue
        out.append(r)
    return out


# ---------------------------------------------------------------- network

def _http(url, headers, opener):
    """GET -> (status, content type, body bytes). HTTP errors come back as a status, not raised."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with opener.open(req, timeout=TIMEOUT) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        try:
            body = e.read()
        except Exception:
            body = b""
        finally:
            e.close()
        return e.code, e.headers.get("Content-Type", "") if e.headers else "", body
    except urllib.error.URLError as e:
        raise InsiderError("could not reach %s: %s" % (url.split("?")[0], e.reason))
    except OSError as e:
        raise InsiderError("network error for %s: %s" % (url.split("?")[0], e))


def _text(body):
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    return body.decode("utf-8", "replace")


def refused(status, page):
    return InsiderError("NSE refused the script (%s): nseindia.com's bot protection blocks many scripts. "
                        "Open %s in your browser and paste the table into the chat, or try again later."
                        % ("HTTP %d" % status if status else "it sent a web page, not data", page))


class Nse(object):
    """One cookie jar for www.nseindia.com: home page first, then the API, a pause between calls."""

    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.warm = False
        self.calls = 0

    def _pause(self):
        if self.calls:
            SLEEP(PAUSE)
        self.calls += 1

    def get_json(self, url, params, page):
        if not self.warm:
            self._pause()
            status, _, _ = _http(NSE_HOME, {"User-Agent": BROWSER_UA, "Accept-Language": "en-IN,en;q=0.9",
                                            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
                                 self.opener)
            if status in (401, 403):
                raise refused(status, page)
            self.warm = True
        self._pause()
        full = url + "?" + urllib.parse.urlencode(params)
        status, ctype, body = _http(full, {"User-Agent": BROWSER_UA, "Accept": "application/json, text/plain, */*",
                                           "Accept-Language": "en-IN,en;q=0.9", "Referer": page}, self.opener)
        if status in (401, 403):
            raise refused(status, page)
        if status >= 400:
            raise InsiderError("NSE answered HTTP %d for %s: open %s in your browser" % (status, url, page))
        text = _text(body).strip()
        if "html" in ctype.lower() or text.startswith("<"):
            raise refused(None, page)
        try:
            return json.loads(text)
        except ValueError:
            raise InsiderError("NSE sent something that is not JSON for %s: open %s in your browser" % (url, page))

    def archive_csv(self, kind):
        self._pause()
        url = ARCHIVE_CSV % kind
        status, ctype, body = _http(url, {"User-Agent": BROWSER_UA, "Accept": "text/csv,*/*;q=0.8"}, self.opener)
        if status in (401, 403):
            raise refused(status, DEALS_PAGE)
        if status == 404:
            raise InsiderError("NSE has no %s.csv right now (HTTP 404): it is published after the session"
                               % kind)
        if status >= 400:
            raise InsiderError("NSE answered HTTP %d for %s" % (status, url))
        text = _text(body)
        if text.lstrip().startswith("<"):
            raise refused(None, DEALS_PAGE)
        return text


def dmy(d):
    return d.strftime("%d-%m-%Y")


def offline_file(path, name):
    if os.path.isdir(path):
        p = os.path.join(path, name)
        if not os.path.isfile(p):
            raise InsiderError("no %s in %s" % (name, path))
        return p
    return path


def _read(path):
    with open(path, "rb") as fh:
        return _text(fh.read())


def load_pit(start, end, symbol=None, offline=None, nse=None):
    if offline:
        try:
            return pit_rows(json.loads(_read(offline_file(offline, "pit.json"))))
        except ValueError as e:
            raise InsiderError("the saved insider answer is not JSON: %s" % e)
    params = {"index": "equities", "from_date": dmy(start), "to_date": dmy(end)}
    if symbol:
        params["symbol"] = symbol
    return pit_rows((nse or Nse()).get_json(PIT_API, params, PIT_PAGE))


def load_deals(kind, start=None, end=None, offline=None, nse=None):
    """kind bulk or block. No dates: today's archive CSV. Dates: NSE's history API."""
    if offline:
        if os.path.isdir(offline):
            name = ("%s_deals.json" % kind) if start else ("%s.csv" % kind)
            path = offline_file(offline, name)
        else:
            path = offline
        text = _read(path)
        if path.endswith(".json"):
            try:
                return deals_from_json(json.loads(text), kind)
            except ValueError as e:
                raise InsiderError("the saved %s deals answer is not JSON: %s" % (kind, e))
        return deals_from_csv(text, kind)
    nse = nse or Nse()
    if start:
        doc = nse.get_json(DEALS_API, {"optionType": "%s_deals" % kind, "from": dmy(start), "to": dmy(end)},
                           DEALS_PAGE)
        return deals_from_json(doc, kind)
    return deals_from_csv(nse.archive_csv(kind), kind)


# ---------------------------------------------------------------- state

def load_state(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {"seen": {}, "last_check": None}


def save_state(path, state):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.write("\n")


def remember(state, items, today):
    """Mark items as shown; forget entries older than KEEP_DAYS."""
    seen = dict(state.get("seen") or {})
    for it in items:
        seen.setdefault(it["id"], today.isoformat())
    cutoff = (today - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    return state


def unseen(items, state):
    seen = state.get("seen") or {}
    return [it for it in items if it["id"] not in seen]


# ---------------------------------------------------------------- output

def holdings(path):
    out = set()
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            try:
                doc = json.load(fh)
            except ValueError:
                raise InsiderError("%s is not valid JSON" % path)
        for p in doc.get("positions", []) if isinstance(doc, dict) else []:
            t = p.get("ticker") or p.get("symbol")
            if t:
                try:
                    out.add(nse_symbol(t))
                except InsiderError:
                    continue
    return out


WORDS = {"buy": "bought", "sell": "sold", "pledge": "pledged", "pledge revoked": "released a pledge on",
         "pledge invoked": "had a pledge invoked on"}


def describe_pit(r):
    who = "%s (%s)" % (r["person"] or "someone", r["category"])
    verb = WORDS.get(r["action"], r["action"])
    what = ("%s shares" % count(r["shares"])) if r["shares"] is not None else "shares (count not given)"
    bits = ["%s: %s %s %s" % (r["symbol"], who, verb, what)]
    if r["pledge"]:
        bits.append("a pledge, not a trade: shares used as loan collateral")
    elif r["mode"]:
        bits.append(r["mode"])
    if r["value_inr"] and not r["pledge"]:
        v = amount(r["value_inr"])
        if r["price"]:
            v += ", about %.2f a share" % r["price"]
        bits.append(v)
    if r["before_pct"] is not None and r["after_pct"] is not None:
        bits.append("holding %.2f%% -> %.2f%%" % (r["before_pct"], r["after_pct"]))
    done = "dated" if r["pledge"] else "traded"
    if r["from"] and r["to"] and r["from"] != r["to"]:
        bits.append("%s %s to %s" % (done, r["from"], r["to"]))
    elif r["to"] or r["from"]:
        bits.append("%s %s" % (done, r["to"] or r["from"]))
    if r["disclosed"]:
        d = "disclosed %s" % r["disclosed"]
        if r["days_to_disclose"] is not None:
            d += ", %d trading day%s after the %s" % (r["days_to_disclose"],
                                                      "" if r["days_to_disclose"] == 1 else "s",
                                                      "pledge" if r["pledge"] else "trade")
            if r["late"]:
                d += " (SLOW: SEBI allows 4"
                if r["insider_days"] is not None and r["insider_days"] > 2:
                    d += "; the insider took %d to tell the company" % r["insider_days"]
                if r["company_days"] is not None and r["company_days"] > 2:
                    d += "; the company took %d to tell NSE" % r["company_days"]
                d += ")"
        bits.append(d)
    return ", ".join(bits)


def describe_deal(r):
    verb = {"buy": "bought", "sell": "sold"}.get(r["side"], r["side"] or "traded")
    out = "%s %s %s: %s %s %s shares" % (r["kind"].upper(), r["date"] or "?", r["symbol"], r["client"] or "?",
                                         verb, count(r["qty"]) if r["qty"] is not None else "?")
    if r["price"] is not None:
        out += " at %.2f" % r["price"]
    if r["value_inr"] is not None:
        out += " = %s" % amount(r["value_inr"])
    if r["remarks"]:
        out += " (%s)" % r["remarks"]
    return out


def flag(sym, mine):
    return "  <- you hold %s" % sym if sym in mine else ""


def render_pit(rows, mine=()):
    out = []
    for r in rows:
        mark = "**" if r["highlight"] else "  "
        out.append("%s %s%s" % (mark, describe_pit(r), flag(r["symbol"], mine)))
        out.append("     %s" % r["url"])
    if any(r["highlight"] for r in rows):
        out.append("** a promoter, promoter group, director or KMP trading in the open market.")
    out.append(NOTE_PIT)
    return "\n".join(out) + "\n"


def render_deals(rows, mine=()):
    out = ["%s%s" % (describe_deal(r), flag(r["symbol"], mine)) for r in rows]
    out.append(NOTE_DEALS)
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- CLI

def _today(args):
    if getattr(args, "today", None):
        return parse_date_arg(args.today, "--today")
    return to_ist(utc_now()).date()


def _window(args, default_days):
    end = parse_date_arg(args.to, "--to") if args.to else _today(args)
    if args.from_:
        start = parse_date_arg(args.from_, "--from")
    else:
        start = end - datetime.timedelta(days=max(0, (args.days or default_days) - 1))
    if start > end:
        raise InsiderError("--from %s is after --to %s" % (start, end))
    if (end - start).days > 365:
        raise InsiderError("NSE serves at most a year at a time: narrow --from / --to")
    return start, end


def _symbols(args):
    return {nse_symbol(s) for s in (args.symbol or [])} or None


def _parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--symbol", action="append", help="an NSE symbol (repeat for more)")
        p.add_argument("--holdings", action="store_true", help="only stocks in trader/portfolio.json")
        p.add_argument("--portfolio", default="trader/portfolio.json")
        p.add_argument("--offline", metavar="PATH", help="saved answers: a folder or one file")
        p.add_argument("--today", help="YYYY-MM-DD, India date (for reproducible runs)")
        p.add_argument("--json", action="store_true")

    def window(p, days):
        p.add_argument("--from", dest="from_", help="first day, 2026-10-13")
        p.add_argument("--to", help="last day (default today)")
        p.add_argument("--days", type=int, help="days back from --to (default %d)" % days)

    p = sub.add_parser("pit", help="insider trades disclosed under SEBI's PIT rules")
    common(p)
    window(p, 7)
    p.add_argument("--category", action="append",
                   help="promoter, promoter-group, promoters, director, kmp, employee, relative, insiders, all")
    p.add_argument("--market-only", action="store_true", help="open-market purchases and sales only")
    p.add_argument("--action", choices=["buy", "sell", "pledge"])
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("deals", help="bulk and block deals")
    common(p)
    window(p, 7)
    p.add_argument("--kind", choices=["bulk", "block", "both"], default="both")
    p.add_argument("--min-value-cr", type=float, help="only deals worth at least this many crore")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("new", help="insider trades and deals not shown at the last check")
    common(p)
    p.add_argument("--days", type=int, default=3, help="insider disclosures from the last N days (default 3)")
    p.add_argument("--all", action="store_true",
                   help="every row, not just the notable ones (key people, your stocks, big deals)")
    p.add_argument("--min-value-cr", type=float, default=10.0,
                   help="deals worth at least this many crore count as notable (default 10)")
    p.add_argument("--state", default="trader/state/insider.json")
    p.add_argument("--dry-run", action="store_true", help="do not update the state file")
    return ap


def main(argv=None):
    ap = _parser()
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2
    try:
        return _run(args)
    except (InsiderError, OSError) as exc:
        print("insider: %s" % exc, file=sys.stderr)
        return 2


def _run(args):
    symbols = _symbols(args)
    mine = holdings(args.portfolio)
    if args.holdings:
        if not mine:
            raise InsiderError("--holdings: no positions in %s" % args.portfolio)
        symbols = (symbols or set()) | mine

    if args.cmd == "pit":
        start, end = _window(args, 7)
        rows = load_pit(start, end, sorted(symbols)[0] if symbols and len(symbols) == 1 else None, args.offline)
        rows = in_window(rows, start, end)
        rows = select_pit(rows, symbols, categories_wanted(args.category), args.market_only, args.action)
        rows = rows[:args.limit]
        if args.json:
            print(json.dumps(rows, indent=2))
        elif rows:
            print("Insider trades disclosed %s to %s%s:" % (start, end, (" for " + ", ".join(sorted(symbols)))
                                                             if symbols else ""))
            sys.stdout.write(render_pit(rows, mine))
        else:
            print("No matching insider disclosures from %s to %s." % (start, end))
        return 0 if rows else 1

    if args.cmd == "deals":
        dated = bool(args.from_ or args.to or args.days)
        start, end = _window(args, 7) if dated else (None, None)
        kinds = ("bulk", "block") if args.kind == "both" else (args.kind,)
        nse = None if args.offline else Nse()
        rows = []
        for k in kinds:
            rows += load_deals(k, start, end, args.offline, nse)
        rows = sort_deals(select_deals(rows, symbols, args.min_value_cr))[:args.limit]
        if args.json:
            print(json.dumps(rows, indent=2))
        elif rows:
            span = ("%s to %s" % (start, end)) if dated else "the latest session (NSE's daily files)"
            print("%s deals, %s:" % (" and ".join(kinds).capitalize(), span))
            sys.stdout.write(render_deals(rows, mine))
        else:
            print("No matching %s deals%s." % (" or ".join(kinds), (" from %s to %s" % (start, end)) if dated else
                                                " in the latest files"))
        return 0 if rows else 1

    # new
    today = _today(args)
    state = load_state(args.state)
    notes, failed = [], 0
    nse = None if args.offline else Nse()
    start = today - datetime.timedelta(days=max(1, args.days) - 1)
    try:
        pit = in_window(load_pit(start, today, None, args.offline, nse), start, today)
    except InsiderError as e:
        pit, failed = [], failed + 1
        notes.append("insider trades: %s" % e)
    deals = []
    for k in ("bulk", "block"):
        try:
            deals += load_deals(k, None, None, args.offline, nse)
        except InsiderError as e:
            failed += 1
            notes.append("%s deals: %s" % (k, e))
    if failed == 3:
        raise InsiderError("every source failed:\n  " + "\n  ".join(notes))
    if symbols:
        pit = [r for r in pit if r["symbol"] in symbols]
        deals = [r for r in deals if r["symbol"] in symbols]
    if not args.all:
        pit = [r for r in pit if r["highlight"] or r["symbol"] in mine or (symbols and r["symbol"] in symbols)
               or (r["pledge"] and r["category"] in ("promoter", "promoter group"))]
        deals = [r for r in deals if r["symbol"] in mine or (symbols and r["symbol"] in symbols)
                 or r["kind"] == "block" or (r["value_inr"] or 0) >= args.min_value_cr * 1e7]
    deals = sort_deals(deals)
    fresh_pit, fresh_deals = unseen(pit, state), unseen(deals, state)
    if args.json:
        print(json.dumps({"insider": fresh_pit, "deals": fresh_deals, "notes": notes}, indent=2))
    else:
        for n in notes:
            print("NOTE %s" % n)
        if not fresh_pit and not fresh_deals:
            print("Nothing new since %s." % (state.get("last_check") or "the last check"))
        for r in fresh_pit:
            print("NEW insider %s%s" % (("trade" if not r["pledge"] else "pledge"), (" **" if r["highlight"] else "")))
            print("    %s%s" % (describe_pit(r), flag(r["symbol"], mine)))
            print("    %s" % r["url"])
        for r in fresh_deals:
            print("NEW %s deal" % r["kind"])
            print("    %s%s" % (describe_deal(r), flag(r["symbol"], mine)))
        if fresh_pit:
            print(NOTE_PIT)
        if fresh_deals:
            print(NOTE_DEALS)
    if not args.dry_run:
        remember(state, pit + deals, today)
        state["last_check"] = to_ist(utc_now()).strftime("%Y-%m-%d %H:%M IST")
        save_state(args.state, state)
    return 0 if (fresh_pit or fresh_deals) else 1


if __name__ == "__main__":
    sys.exit(main())
