#!/usr/bin/env python3
"""Earnings reader for trader-analyst. Standard library only.

Pulls an NSE-listed company's reported quarterly numbers from NSE and lays
out what changed, quarter by quarter: revenue, profit before tax, net profit,
basic and diluted EPS, margins, growth against the quarter before and against
the same quarter a year earlier. It also lists the latest results filings
(standalone and consolidated, with their XBRL links) and the next board
meeting called to approve results.

    python3 facts.py RELIANCE
    python3 facts.py RELIANCE TCS --quarters 4
    python3 facts.py RELIANCE --json
    python3 facts.py RELIANCE TCS INFY --metrics trader/metrics.json

Sources (nseindia.com's own JSON, no key; behind bot protection, so the tool
visits the home page first for its cookies, sends browser headers and waits a
second between calls; when NSE refuses anyway it says so and names the page):
  https://www.nseindia.com/api/results-comparision?symbol=<SYMBOL>     (NSE's spelling)
      the last few quarters' profit and loss, in Rs lakh, STANDALONE (the
      company on its own, without subsidiaries)
  https://www.nseindia.com/api/corporates-financial-results?index=equities&period=Quarterly&symbol=<SYMBOL>
      the results filings: standalone and consolidated, audited or not, with XBRL links
  https://www.nseindia.com/api/corporate-board-meetings?index=equities&symbol=<SYMBOL>
      board meetings, past and announced: the next results date

How the numbers are read:
  - Amounts come in Rs lakh and print in Rs crore (100 lakh = 1 crore).
  - Quarters are named for India's financial year (April to March): the
    quarter ending 2026-06-30 is Q1 FY27.
  - Growth is against the same quarter a year earlier when NSE's list reaches
    back that far (it usually holds five quarters), and against the quarter before.
  - Banks report differently (interest earned, provisions): their "revenue"
    is total income and the operating margin is left blank, not zero.

--metrics FILE merges one row per symbol into a JSON file (for notes or a
sheet). Offline: --offline DIR reads results-<SYMBOL>.json,
financial-results-<SYMBOL>.json and board-meetings-<SYMBOL>.json from DIR.

Numbers only: this tool never says whether a stock is a buy.
"""

import argparse
import datetime
import gzip
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

NSE_HOME = "https://www.nseindia.com/"
NSE_API = "https://www.nseindia.com/api/"
NSE_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-financial-results"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")
NSE_PAUSE = 1.0                 # seconds between calls to nseindia.com
OTHER_INCOME_FLAG = 25.0        # other income above this share of profit before tax is called out
RESULTS = re.compile(r"financial results?|\bresults\b", re.I)


class FactsError(Exception):
    pass


class NseRefused(FactsError):
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
        raise FactsError('--now wants an India time like "2026-10-19 10:15" (or ...Z for UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


# ---------------------------------------------------------------- NSE symbols

NSE_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")   # RELIANCE, M&M, BAJAJ-AUTO, 3MINDIA


def nse_symbol(text):
    """"reliance", "RELIANCE.NS", "NSE:RELIANCE" -> "RELIANCE". BSE (.BO) is refused."""
    s = str(text or "").strip().upper()
    if s.startswith("NSE:"):
        s = s[4:]
    if s.endswith(".NS"):
        s = s[:-3]
    if s.endswith(".BO") or s.startswith("BSE:"):
        raise FactsError("%s is a BSE listing: this pack trades NSE symbols (like RELIANCE)" % text)
    if not NSE_SYMBOL.match(s):
        raise FactsError("%r is not an NSE symbol (like RELIANCE, M&M or BAJAJ-AUTO)" % text)
    return s


# ---------------------------------------------------------------- fetching

class Fetcher(object):
    """nseindia.com with a cookie jar, browser headers, a home-page visit first
    and a pause between calls. Offline: files in a folder."""

    def __init__(self, offline=None, nse_pause=NSE_PAUSE):
        self.offline = offline
        self.nse_pause = nse_pause
        self._nse_last = 0.0
        self._nse_ready = False
        self.nse_refused = None          # the first refusal: NSE is not asked again this run
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def _open(self, url, headers):
        """One GET -> (body bytes, content type). Raises urllib errors."""
        req = urllib.request.Request(url, headers=headers)
        with self._opener.open(req, timeout=60) as resp:
            data = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
            return data, resp.headers.get("Content-Type") or ""

    def _wait(self):
        gap = time.time() - self._nse_last
        if gap < self.nse_pause:
            time.sleep(self.nse_pause - gap)
        self._nse_last = time.time()

    def _refused(self, why):
        self.nse_refused = NseRefused(
            "NSE refused the script (%s): nseindia.com's bot protection blocks many scripts. Read the "
            "results in your browser instead: %s" % (why, NSE_PAGE))
        return self.nse_refused

    def json(self, endpoint, params, name):
        """GET www.nseindia.com/api/<endpoint>?params -> parsed JSON, or NseRefused."""
        if self.nse_refused is not None:
            raise self.nse_refused
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise FactsError("offline: no file %s" % path)
            with open(path, "rb") as fh:
                data = fh.read()
        else:
            headers = {"User-Agent": BROWSER_UA, "Accept-Language": "en-IN,en;q=0.9", "Accept-Encoding": "gzip"}
            try:
                if not self._nse_ready:
                    self._wait()
                    self._open(NSE_HOME, dict(headers, Accept="text/html,application/xhtml+xml,*/*;q=0.8"))
                    self._nse_ready = True
                self._wait()
                data, ctype = self._open(NSE_API + endpoint + "?" + urllib.parse.urlencode(params),
                                         dict(headers, Accept="application/json, text/plain, */*",
                                              Referer=NSE_HOME))
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise self._refused("HTTP %d" % exc.code)
                raise FactsError("NSE answered HTTP %d for %s" % (exc.code, endpoint))
            except urllib.error.URLError as exc:
                raise FactsError("could not reach nseindia.com: %s" % (exc.reason,))
            if "html" in ctype.lower():
                raise self._refused("a web page instead of data")
        if data.lstrip()[:1] == b"<":
            raise self._refused("a web page instead of data")
        try:
            return json.loads(data.decode("utf-8"))
        except ValueError:
            raise FactsError("NSE sent something that is not JSON for %s" % endpoint)


# ---------------------------------------------------------------- reading NSE's numbers

def num(v):
    """"872100" -> 872100.0; "", "-", None, "NA" -> None."""
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def nse_date(v):
    """"31-DEC-2024", "31-Dec-2024", "16-Jan-2025 20:20", "2024-12-31" -> date, or None."""
    s = str(v or "").strip()
    for fmt in ("%d-%b-%Y", "%d-%b-%Y %H:%M", "%d-%b-%Y %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, fmt).date()      # %b ignores case: DEC and Dec
        except ValueError:
            continue
    return None


def nse_stamp(v):
    """"23-Jul-2026 18:31" -> "2026-07-23 18:31" (India time), or the text as it came."""
    s = str(v or "").strip()
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M"):
        try:
            return datetime.datetime.strptime(s, fmt).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    return s or None


def fy_quarter(end):
    """India's financial year runs April to March: the quarter ending 2026-06-30 is Q1 FY27."""
    q = {6: 1, 9: 2, 12: 3, 3: 4}.get(end.month)
    if q is None:
        return "quarter"
    fy = end.year + (1 if end.month > 3 else 0)
    return "Q%d FY%02d" % (q, fy % 100)


def crore(lakhs):
    """Rs lakh -> Rs crore, to the paisa-free 2 places."""
    return None if lakhs is None else round(lakhs / 100.0, 2)


def first(row, *keys):
    for k in keys:
        v = num(row.get(k))
        if v is not None:
            return v
    return None


def is_bank(doc, rows):
    flag = str((doc or {}).get("bankNonBnking") or "").strip().upper()
    if flag in ("B", "Y", "BANK"):
        return True
    return any(num(r.get("re_int_earned")) is not None and num(r.get("re_net_sale")) is None for r in rows)


def pct(a, b):
    if a is None or b in (None, 0):
        return None
    return round(100.0 * (a - b) / abs(b), 1)


def margin(part, whole):
    if part is None or not whole:
        return None
    return round(100.0 * part / whole, 1)


def _match(rows, end, days, slack=10):
    """The row whose quarter ended about `days` before `end`."""
    target = end - datetime.timedelta(days=days)
    best = None
    for r in rows:
        gap = abs((r["_end"] - target).days)
        if gap <= slack and (best is None or gap < best[0]):
            best = (gap, r)
    return best[1] if best else None


def analyse(doc, quarters=5, name=None):
    """Every number in the report, as a dict. doc: NSE's results-comparision answer."""
    raw = (doc or {}).get("resCmpData") if isinstance(doc, dict) else None
    if not raw:
        raise FactsError("NSE has no quarterly results for this symbol (is it an NSE equity symbol?)")
    bank = is_bank(doc, raw)
    by_end = {}
    for r in raw:
        end = nse_date(r.get("re_to_dt"))
        if end is None:
            continue
        if end in by_end and (num(by_end[end].get("re_seq_num")) or 0) >= (num(r.get("re_seq_num")) or 0):
            continue
        by_end[end] = r
    if not by_end:
        raise FactsError("NSE's results have no quarter end dates")
    base = []
    for end in sorted(by_end, reverse=True):
        r = by_end[end]
        start = nse_date(r.get("re_from_dt"))
        total = first(r, "re_total_inc", "re_tot_inc")
        if bank:
            revenue = total if total is not None else (
                (first(r, "re_int_earned") or 0) + (first(r, "re_oth_inc", "re_oth_inc_new") or 0)) or None
        else:
            revenue = first(r, "re_net_sale")
        other = first(r, "re_oth_inc_new", "re_oth_inc")
        finance = first(r, "re_int_new") if not bank else None
        exceptional = first(r, "re_excepn_items_new", "re_excepn_items")
        pbt = first(r, "re_pro_loss_bef_tax")
        profit = first(r, "re_net_profit", "re_con_pro_loss", "re_proloss_ord_act")
        core = None
        if not bank and pbt is not None:
            core = pbt + (finance or 0) - (other or 0)
        res_type = str(r.get("re_res_type") or "").strip().upper()
        base.append({
            "_end": end, "end": end.isoformat(), "start": start.isoformat() if start else None,
            "quarter": fy_quarter(end),
            "audited": {"A": True, "U": False}.get(res_type),
            "revenue": crore(revenue), "total_income": crore(total), "other_income": crore(other),
            "finance_cost": crore(finance), "exceptional": crore(exceptional), "pbt": crore(pbt),
            "tax": crore(first(r, "re_tax")), "net_profit": crore(profit),
            "eps_basic": first(r, "re_basic_eps_for_cont_dic_opr", "re_basic_eps", "re_bsc_eps_bfr_exi"),
            "eps_diluted": first(r, "re_dilut_eps_for_cont_dic_opr", "re_diluted_eps", "re_dil_eps_bfr_exi"),
            "pbt_margin": margin(pbt, revenue), "operating_margin": margin(core, revenue),
            "net_margin": margin(profit, revenue),
            "other_income_share": (round(100.0 * other / pbt, 1)
                                   if not bank and other is not None and pbt and pbt > 0 else None),
            "gross_npa_pct": first(r, "re_grs_npa_per", "re_per_grs_npa") if bank else None,
            "filed": (nse_date(r.get("re_create_dt")) or end).isoformat() if r.get("re_create_dt") else None,
        })
    rows = []
    for b in base:
        year = _match(base, b["_end"], 365)
        prev = _match(base, b["_end"], 91, slack=12)
        row = {k: v for k, v in b.items() if not k.startswith("_")}
        row.update({
            "revenue_yoy": pct(b["revenue"], (year or {}).get("revenue")),
            "net_profit_yoy": pct(b["net_profit"], (year or {}).get("net_profit")),
            "eps_yoy": pct(b["eps_diluted"], (year or {}).get("eps_diluted")),
            "revenue_qoq": pct(b["revenue"], (prev or {}).get("revenue")),
            "net_profit_qoq": pct(b["net_profit"], (prev or {}).get("net_profit")),
            "prev_end": prev["end"] if prev else None,
        })
        rows.append(row)
    ttm = {}
    last4 = base[:4]
    if len(last4) == 4 and (last4[0]["_end"] - last4[-1]["_end"]).days < 300:
        for key in ("revenue", "net_profit", "eps_diluted"):
            vals = [b[key] for b in last4]
            if all(v is not None for v in vals):
                ttm[key] = round(sum(vals), 2)
    return {"name": name, "basis": "standalone", "bank": bank, "rows": rows[:quarters], "ttm": ttm}


# ---------------------------------------------------------------- filings and board meetings

def latest_filings(rows):
    """The newest quarter's results filings on NSE: {"end", "relating", "consolidated", "standalone"}."""
    rows = [r for r in (rows if isinstance(rows, list) else (rows or {}).get("data") or [])
            if isinstance(r, dict) and nse_date(r.get("toDate"))]
    if not rows:
        return None
    newest = max(nse_date(r["toDate"]) for r in rows)
    out = {"end": newest.isoformat(), "relating": None, "consolidated": None, "standalone": None, "name": None}
    for r in sorted(rows, key=lambda r: (nse_date(r.get("toDate")), nse_stamp(r.get("filingDate")) or "")):
        if nse_date(r["toDate"]) != newest:
            continue
        kind = "standalone" if "non" in str(r.get("consolidated") or "").lower() else "consolidated"
        out[kind] = {"filed": nse_stamp(r.get("filingDate") or r.get("broadCastDate")),
                     "audited": str(r.get("audited") or "").strip() or None, "xbrl": r.get("xbrl") or None,
                     "seq": r.get("seqNumber")}
        out["relating"] = r.get("relatingTo") or out["relating"]
        out["name"] = r.get("companyName") or out["name"]
    return out


def next_results(meetings, today):
    """The first board meeting on or after today called to approve results, or None."""
    rows = meetings if isinstance(meetings, list) else (meetings or {}).get("data") or []
    ahead = []
    for m in rows:
        if not isinstance(m, dict):
            continue
        d = nse_date(m.get("bm_date"))
        if d and d >= today and RESULTS.search(str(m.get("bm_purpose") or "")):
            ahead.append((d, m))
    if not ahead:
        return None
    d, m = min(ahead, key=lambda x: x[0])
    return {"date": d.isoformat(), "weekday": d.strftime("%A"), "purpose": m.get("bm_purpose"),
            "announced": nse_stamp(m.get("bm_timestamp")), "name": m.get("sm_name"), "link": m.get("attachment")}


def stale_note(result, filings):
    """A note when NSE's results comparison trails results that are already filed."""
    if not filings or not result["rows"]:
        return None
    latest = result["rows"][0]["end"]
    if filings["end"] > latest and (nse_date(filings["end"]) - nse_date(latest)).days > 20:
        f = filings.get("consolidated") or filings.get("standalone") or {}
        return ("NSE's results comparison stops at %s, but results for the quarter ending %s were filed %s. "
                "Read the filing directly: %s" % (latest, filings["end"], f.get("filed") or "already",
                                                 f.get("xbrl") or NSE_PAGE))
    return None


# ---------------------------------------------------------------- output

def grouped(v, places=1):
    """1,28,260.5: Indian digit grouping (lakh, crore) to `places` decimals."""
    s = "%.*f" % (places, abs(v))
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return ("-" if v < 0 and float(s) != 0 else "") + whole + ("." + frac if frac else "")


def cr(v):
    return "n/a" if v is None else "Rs %s crore" % grouped(v)


def changes(result):
    """Plain sentences: what changed in the latest quarter."""
    rows = result["rows"]
    if not rows:
        return []
    cur = rows[0]
    prev = next((r for r in rows if r["end"] == cur.get("prev_end")), None)
    out = []
    word = "total income" if result.get("bank") else "revenue"
    status = {True: ", audited", False: ", unaudited"}.get(cur.get("audited"), "")
    line = "Quarter ending %s (%s%s): %s %s" % (cur["end"], cur["quarter"], status, word, cr(cur["revenue"]))
    if cur["revenue_yoy"] is not None:
        line += " (%+.1f%% vs a year earlier)" % cur["revenue_yoy"]
    line += ", net profit %s" % cr(cur["net_profit"])
    if cur["net_profit_yoy"] is not None:
        line += " (%+.1f%%)" % cur["net_profit_yoy"]
    if cur["eps_diluted"] is not None:
        line += ", diluted EPS Rs %.2f" % cur["eps_diluted"]
        if cur["eps_yoy"] is not None:
            line += " (%+.1f%%)" % cur["eps_yoy"]
    out.append(line + ".")
    for key, name in (("pbt_margin", "Profit before tax margin"),
                      ("operating_margin", "Operating margin (before other income and finance costs)"),
                      ("net_margin", "Net margin")):
        if cur[key] is None:
            continue
        s = "%s %.1f%%" % (name, cur[key])
        if prev and prev[key] is not None:
            s += " (%+.1f points vs the quarter before)" % (cur[key] - prev[key])
        out.append(s + ".")
    if cur.get("other_income_share") is not None and cur["other_income_share"] >= OTHER_INCOME_FLAG:
        out.append("Other income is %.1f%% of profit before tax (%s): interest, dividends or treasury gains "
                   "outside the business. Read the results before reading it as growth."
                   % (cur["other_income_share"], cr(cur["other_income"])))
    if cur.get("exceptional"):
        out.append("Exceptional items of %s are inside this quarter's profit: a one-off. Read the notes to the "
                   "results." % cr(cur["exceptional"]))
    if cur["net_profit"] is not None and cur["pbt"] is not None and cur["pbt"] > 0 \
            and cur["net_profit"] > cur["pbt"]:
        out.append("Net profit is above profit before tax: a tax credit or write-back is inside it.")
    if cur.get("gross_npa_pct") is not None:
        out.append("Gross NPA %.2f%% of advances." % cur["gross_npa_pct"])
    if prev and cur["revenue_qoq"] is not None:
        out.append("%s %+.1f%% vs the quarter before (many businesses are seasonal: compare with a year "
                   "earlier first)." % (word.capitalize(), cur["revenue_qoq"]))
    return out


def _c(v):
    return "-" if v is None else grouped(v)


def _p(v):
    return "-" if v is None else "%+.1f%%" % v if v != 0 else "0.0%"


def _pm(v):
    return "-" if v is None else "%.1f%%" % v


def render(symbol, result, filings=None, upcoming=None, today=None, notes=()):
    who = "%s (%s)" % (result["name"], symbol) if result.get("name") else symbol
    out = ["%s. Rs crore except EPS. Standalone results from NSE: the company on its own, without its "
           "subsidiaries." % who, ""]
    word = "tot inc" if result.get("bank") else "revenue"
    out.append("%-8s %-10s %11s %8s %8s %7s %7s %7s %10s %8s %8s %8s" % (
        "quarter", "period end", word, "rev yoy", "rev qoq", "PBT", "oper", "net", "net prof", "np yoy",
        "EPS dil", "EPS yoy"))
    for r in result["rows"]:
        out.append("%-8s %-10s %11s %8s %8s %7s %7s %7s %10s %8s %8s %8s%s" % (
            r["quarter"], r["end"], _c(r["revenue"]), _p(r["revenue_yoy"]), _p(r["revenue_qoq"]),
            _pm(r["pbt_margin"]), _pm(r["operating_margin"]), _pm(r["net_margin"]), _c(r["net_profit"]),
            _p(r["net_profit_yoy"]), "-" if r["eps_diluted"] is None else "%.2f" % r["eps_diluted"],
            _p(r["eps_yoy"]), "  audited" if r["audited"] else ""))
    if result.get("bank"):
        out.append("(PBT and net are margins on total income; a bank's interest costs are its business, so no "
                   "operating margin.)")
    else:
        out.append("(PBT, oper and net are margins on revenue; oper is before other income and finance costs.)")
    t = result["ttm"]
    if t:
        out.append("")
        out.append("Last 4 quarters: %s %s, net profit %s, diluted EPS %s." % (
            "total income" if result.get("bank") else "revenue", cr(t.get("revenue")), cr(t.get("net_profit")),
            "Rs %.2f" % t["eps_diluted"] if t.get("eps_diluted") is not None else "n/a"))
    out.append("")
    out.append("What changed:")
    for line in changes(result):
        out.append("  - " + line)
    if filings:
        out.append("")
        out.append("Latest results on NSE: quarter ending %s%s." % (
            filings["end"], (" (%s)" % filings["relating"]) if filings.get("relating") else ""))
        for kind in ("standalone", "consolidated"):
            f = filings.get(kind)
            if f:
                out.append("  %-12s filed %s, %s  %s" % (kind, f["filed"] or "?", (f["audited"] or "?").lower(),
                                                        f["xbrl"] or "(no XBRL link)"))
        cons = filings.get("consolidated")
        if cons:
            out.append("The table is standalone. The consolidated (group) numbers are in the consolidated "
                       "filing: %s" % (cons["xbrl"] or NSE_PAGE))
        else:
            out.append("The table is standalone. NSE lists no consolidated filing for this quarter.")
    if today is not None:
        out.append("")
        if upcoming:
            days = (nse_date(upcoming["date"]) - today).days
            out.append("Next results: board meeting on %s (%s, %s), per NSE's board meetings list: %s" % (
                upcoming["date"], upcoming["weekday"],
                "today" if days == 0 else "in %d day%s" % (days, "" if days == 1 else "s"),
                upcoming["purpose"]))
        else:
            out.append("Next results: no results board meeting on NSE's list yet. Check /trader-news headlines "
                       "or the company's investor page, and say where the date came from.")
    note = stale_note(result, filings)
    if note:
        out.append("")
        out.append("NOTE: " + note)
    for n in notes:
        out.append("note: " + n)
    return "\n".join(out) + "\n"


def metrics_row(symbol, result, upcoming=None):
    rows = result["rows"]
    cur = rows[0] if rows else {}
    t = result["ttm"]
    return {"symbol": symbol, "name": result.get("name"), "basis": "standalone", "bank": result.get("bank"),
            "period_end": cur.get("end"), "quarter": cur.get("quarter"),
            "revenue_ttm_cr": t.get("revenue"), "net_profit_ttm_cr": t.get("net_profit"),
            "eps_ttm": t.get("eps_diluted"), "revenue_growth_yoy": cur.get("revenue_yoy"),
            "net_profit_growth_yoy": cur.get("net_profit_yoy"), "eps_growth_yoy": cur.get("eps_yoy"),
            "pbt_margin": cur.get("pbt_margin"), "operating_margin": cur.get("operating_margin"),
            "net_margin": cur.get("net_margin"), "next_results": (upcoming or {}).get("date"),
            "source": "NSE results comparison (standalone)"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tickers", nargs="*", help="NSE symbols, e.g. RELIANCE TCS")
    ap.add_argument("--quarters", type=int, default=5)
    ap.add_argument("--no-filings", action="store_true", help="skip the filings list and the board meetings")
    ap.add_argument("--metrics", metavar="FILE", help="merge a row per symbol into this JSON")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--now", help='pretend it is this India time, e.g. "2026-10-19 10:15"')
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not args.tickers:
        ap.print_help()
        return 2
    try:
        symbols = [nse_symbol(t) for t in args.tickers]
        now = parse_now(args.now)
    except FactsError as exc:
        print("facts: %s" % exc, file=sys.stderr)
        return 2
    today = to_ist(now).date()
    fetcher = Fetcher(offline=args.offline)
    results, rows, failed = [], [], 0
    for sym in symbols:
        try:
            doc = fetcher.json("results-comparision", {"symbol": sym}, "results-%s.json" % sym)
            result = analyse(doc, quarters=args.quarters)
        except FactsError as exc:
            print("facts: %s: %s" % (sym, exc), file=sys.stderr)
            failed += 1
            continue
        filings = upcoming = None
        notes = []
        if not args.no_filings:
            try:
                filings = latest_filings(fetcher.json(
                    "corporates-financial-results", {"index": "equities", "period": "Quarterly", "symbol": sym},
                    "financial-results-%s.json" % sym))
            except FactsError as exc:
                if not (args.offline and "no file" in str(exc)):
                    notes.append("results filings: %s" % exc)
            try:
                upcoming = next_results(fetcher.json(
                    "corporate-board-meetings", {"index": "equities", "symbol": sym},
                    "board-meetings-%s.json" % sym), today)
            except FactsError as exc:
                if not (args.offline and "no file" in str(exc)):
                    notes.append("board meetings: %s" % exc)
            result["name"] = (filings or {}).get("name") or (upcoming or {}).get("name")
        results.append({"symbol": sym, "result": result, "filings": filings, "next_results": upcoming,
                        "changes": changes(result), "note": stale_note(result, filings), "notes": notes})
        rows.append(metrics_row(sym, result, upcoming))
        if not args.json:
            sys.stdout.write(render(sym, result, filings, upcoming, None if args.no_filings else today, notes))
            sys.stdout.write("\n")
    if args.json:
        print(json.dumps(results, indent=2))
    if args.metrics and rows:
        existing = {}
        if os.path.isfile(args.metrics):
            with open(args.metrics, encoding="utf-8") as fh:
                existing = {r.get("symbol") or r.get("ticker"): r for r in json.load(fh).get("companies", [])}
        for r in rows:
            existing[r["symbol"]] = r
        folder = os.path.dirname(args.metrics)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.metrics, "w", encoding="utf-8") as fh:
            json.dump({"as_of": today.isoformat(),
                       "companies": sorted(existing.values(), key=lambda r: r.get("symbol") or "")},
                      fh, indent=2)
            fh.write("\n")
        if not args.json:
            print("wrote %s (%d companies)" % (args.metrics, len(existing)))
    return 0 if results and not failed else (1 if results else 2)


if __name__ == "__main__":
    sys.exit(main())
