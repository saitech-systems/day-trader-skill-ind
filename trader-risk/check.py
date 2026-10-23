#!/usr/bin/env python3
"""Pre-trade risk check for trader-risk. Standard library only, no network.

Reads the proposed orders (trader/orders.json, Zerodha Kite place_order shape)
and checks every one against your rules (trader/config.json), your account
(trader/portfolio.json), prices (trader/prices.json) and what was placed today
(trader/state/broker.json). Each order gets PASS or BLOCK with the reasons.
Orders are checked in file order as if each PASS had filled, so buys that are
fine one by one but break a limit together are caught.

    python3 check.py                                 # the trader/ files, writes trader/state/risk.json
    python3 check.py --now "2026-10-19 10:00"        # pin the clock (India time)
    python3 check.py --orders trader/orders.json --json
    python3 check.py --no-write                      # check without recording the verdicts

Entries and exits. An ENTRY opens or adds to a position (a buy, or a sell
that would go short). An EXIT only reduces what you hold. Getting out must
always be possible: an exit is checked for format, asset and short sales
only; every other rule turns into a warning for it.

What blocks an order (every order):

  format        no tradingsymbol, transaction_type not BUY or SELL, quantity
                not a whole number (Kite's place_order takes numbers, not
                strings), variety other than regular (no cover orders, AMO or
                iceberg here), an unknown order_type, product or validity, a
                LIMIT without a price, an SL without a trigger_price and price,
                a price off the NSE tick grid, NRML (that is for F&O); a
                stop_order that is not an SL / SL-M sell of the same stock,
                product and exchange, or that sells more than the order buys
  asset         futures and options (NFO, or a symbol ending in CE / PE / FUT)
                unless "allow_options": true; BSE and other exchanges (this
                pack trades the NSE); anything that is not an NSE equity symbol
  margin        product MTF (margin trading facility) unless "allow_margin": true
  short         selling more than you hold, unless "allow_short": true

What blocks an entry (on top):

  blocked       a symbol on your "blocked" list
  size          under min_order_inr, over max_order_inr
  position      the stock would be over max_position_pct of the account
  cash          the buy would dip into your cash buffer, or need MIS leverage
  duplicate     the same symbol and side twice in this batch, or already
                placed today (trader/state/broker.json)
  daily limit   max_orders_per_day orders placed today
  stop          no stop while "require_stop": true (the stop_order's trigger,
                or the order's plan stop); a stop on the wrong side of the entry
  loss          qty x (entry - stop) plus the estimated round-trip charges over
                max_loss_per_trade_inr. The entry is the limit price for a LIMIT
                (or SL) order, else the HIGHER of the plan's entry and a fresh
                price (under 15 minutes old), so a stale plan cannot understate
                the risk
  reward:risk   (target - entry) / (entry - stop) under min_reward_risk
  daily loss    the max losses at the stops of today's entries plus this one
                over max_daily_loss_inr (win or lose, each counts in full)
  trades        max_trades_per_day entries already placed today
  window        before no_entries_before or after no_entries_after (India
                time), or the NSE is closed (weekend, NSE holiday, outside the
                session; special sessions like Muhurat trading count as open)

Exit code 0 when every order passes, 1 when any order is blocked.
The verdicts go to trader/state/risk.json with a fingerprint of each order,
so trader-broker only shows an order as ready if THIS exact order passed today.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING, ROUND_HALF_UP

DEFAULTS = {
    "budget": None, "cash_buffer_pct": 10, "max_position_pct": 75, "max_order_inr": 75000,
    "min_order_inr": 100, "max_loss_per_trade_inr": 1000, "max_daily_loss_inr": 3000, "max_trades_per_day": 3,
    "max_orders_per_day": 10, "min_reward_risk": 1.0, "require_stop": True,
    "no_entries_before": "09:30", "no_entries_after": "14:45", "flat_by": "15:05",
    "paper": True, "blocked": [], "allow_options": False, "allow_margin": False, "allow_short": False,
    "charges": None,
}
KITE_FIELDS = ("variety", "exchange", "tradingsymbol", "transaction_type", "quantity", "product",
               "order_type", "price", "trigger_price", "validity", "validity_ttl", "disclosed_quantity", "tag")
PRICE_FIELDS = ("price", "trigger_price")
ORDER_TYPES = {"MARKET", "LIMIT", "SL", "SL-M"}
PRODUCTS = {"MIS", "CNC", "NRML", "MTF"}
VALIDITIES = {"DAY", "IOC", "TTL"}
FNO = re.compile(r"(\d(CE|PE)|FUT)$")
HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
PAISA = 0.005
FRESH_MINUTES = 15
REF_WORDS = {"plan": "the plan's entry", "price": "the latest price", "limit": "the limit price",
             "trigger": "the trigger price", "stale price": "an old price"}


class RiskError(Exception):
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
        raise RiskError('--now wants an India time like "2026-10-19 10:15" (or ...Z for UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_utc(text):
    if not text:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt


def hhmm(value, key):
    """"09:30" -> time(9, 30); None or "" -> None (that bound is off)."""
    if value in (None, "", False):
        return None
    m = HHMM.match(str(value).strip())
    if not m:
        raise RiskError("%s must be an India time like \"09:30\", got %r" % (key, value))
    return datetime.time(int(m.group(1)), int(m.group(2)))


def window_check(now_ist, c):
    """Reasons an entry cannot be placed now (India time). Empty when the window is off."""
    before = hhmm(c.get("no_entries_before"), "no_entries_before")
    after = hhmm(c.get("no_entries_after"), "no_entries_after")
    if before is None and after is None:
        return [], []
    warns = []
    d, t = now_ist.date(), now_ist.time().replace(second=0, microsecond=0)
    if d.year not in CALENDAR_YEARS:
        warns.append("the NSE holiday list here covers %s only: check nseindia.com for %s"
                     % (" and ".join(map(str, CALENDAR_YEARS)), d.isoformat()))
    ok, why = trading_day(d)
    stamp = now_ist.strftime("%a %Y-%m-%d %H:%M")
    if not ok:
        return ["window: the market is closed today (%s, %s IST)" % (why, stamp)], warns
    opens, closes = session(d)
    if t < opens or now_ist.time() >= closes:
        return ["window: the market is closed (%s IST; the session is %s to %s)"
                % (stamp, opens.strftime("%H:%M"), closes.strftime("%H:%M"))], warns
    if why:
        warns.append("%s today, %s to %s" % (why, opens.strftime("%H:%M"), closes.strftime("%H:%M")))
    if before is not None and t < before:
        return ["window: no new entries before %s IST (it is %s)" % (before.strftime("%H:%M"),
                                                                     t.strftime("%H:%M"))], warns
    if after is not None and t > after:
        return ["window: no new entries after %s IST (it is %s)" % (after.strftime("%H:%M"),
                                                                    t.strftime("%H:%M"))], warns
    return [], warns


# ---------------------------------------------------------------- NSE symbols

NSE_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")   # RELIANCE, M&M, BAJAJ-AUTO, 3MINDIA


# ---------------------------------------------------------------- rupees and ticks

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


# ---------------------------------------------------------------- Zerodha intraday charges

CHARGES = {  # Zerodha equity intraday (MIS) on NSE, zerodha.com/charges (read 2026-10-23)
    "brokerage_pct": 0.03,         # per executed order ...
    "brokerage_max_inr": 20,       # ... capped at Rs 20
    "stt_sell_pct": 0.025,         # securities transaction tax, sell side only
    "exchange_txn_pct": 0.00297,   # NSE transaction charge, both sides
    "sebi_per_crore_inr": 10,      # SEBI turnover fee, Rs 10 per crore, both sides
    "stamp_buy_pct": 0.003,        # stamp duty, buy side only
    "gst_pct": 18,                 # GST on brokerage + transaction charge + SEBI fee
}


def round_trip_charges(qty, buy_price, sell_price, rates=None):
    """Estimated charges in rupees to buy qty at buy_price and sell at sell_price,
    intraday. An estimate: the contract note is the real number."""
    c = dict(CHARGES)
    c.update(rates or {})
    buy, sell = float(qty) * float(buy_price), float(qty) * float(sell_price)
    cap = float(c["brokerage_max_inr"])
    brokerage = min(buy * float(c["brokerage_pct"]) / 100, cap) + min(sell * float(c["brokerage_pct"]) / 100, cap)
    txn = (buy + sell) * float(c["exchange_txn_pct"]) / 100
    sebi = (buy + sell) * float(c["sebi_per_crore_inr"]) / 1e7
    stt = sell * float(c["stt_sell_pct"]) / 100
    stamp = buy * float(c["stamp_buy_pct"]) / 100
    gst = (brokerage + txn + sebi) * float(c["gst_pct"]) / 100
    return round(brokerage + txn + sebi + stt + stamp + gst, 2)


# ---------------------------------------------------------------- files and helpers

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise RiskError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise RiskError("%s is not valid JSON: %s" % (path, e))


def fingerprint(order):
    """Same function in trader-broker/ticket.py: the exact order that was checked,
    its stop-loss order and its target."""
    core = {k: order[k] for k in KITE_FIELDS if k in order and order[k] is not None}
    stop = order.get("stop_order")
    if isinstance(stop, dict):
        core["stop_order"] = {k: stop[k] for k in KITE_FIELDS if k in stop and stop[k] is not None}
    if order.get("target_price") is not None:
        core["target_price"] = order["target_price"]
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _num_arg(v):
    """A Kite number: an int or float, never a bool or a string. None if it is not one."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _paisa(x):
    """A price to the paisa, half up (1369.845 -> 1369.85)."""
    return int(float(x) * 100 + 0.5 + 1e-9) / 100.0


def _p(x):
    return "%.2f" % float(x)


def _pct(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.2f" % x)


def _num(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.4f" % x).rstrip("0")


def account(cfg, portfolio, prices):
    """Starting account: cash and position values. No portfolio = a fresh
    account funded with the budget."""
    warnings = []
    if portfolio is None:
        budget = _f(cfg.get("budget"))
        if not budget:
            raise RiskError("no trader/portfolio.json and no budget in the config: "
                            "nothing to check the orders against")
        return {"cash": budget, "shares": {}, "values": {}}, warnings
    cash = _f(portfolio.get("cash"))
    if cash is None:
        raise RiskError("portfolio.json needs a \"cash\" number")
    shares, values = {}, {}
    for p in portfolio.get("positions", []):
        t = str(p.get("ticker") or p.get("symbol") or "").upper()
        if not t:
            continue
        n = _f(p.get("shares", p.get("qty")))
        if n is None:
            n = sum(_f(l.get("shares")) or 0 for l in p.get("lots", []))
        shares[t] = shares.get(t, 0) + n
        if prices.get(t):
            values[t] = shares[t] * prices[t]
        else:
            basis = sum(_f(l.get("cost")) or 0 for l in p.get("lots", []))
            values[t] = basis
            warnings.append("no price for %s: valued at its cost basis %s" % (t, inr(basis)))
    return {"cash": cash, "shares": shares, "values": values}, warnings


def rec_day(rec):
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


def rec_is_entry(rec):
    """trader-broker logs the kind trader-risk gave; older records: a buy is an entry."""
    k = rec.get("kind")
    if k:
        return k == "entry"
    return str(rec.get("side", "")).lower() == "buy"


def placed_today(state, today):
    """Orders recorded as placed today by trader-broker (paper or live). An entry's
    stop-loss leg is part of that entry, not another order."""
    out = []
    for rec in (state or {}).get("orders", []):
        if rec.get("status") == "placed" and rec.get("leg") != "stop" and rec_day(rec) == today:
            out.append(rec)
    return out


# ---------------------------------------------------------------- one Kite order's format

def kite_format(o, c, label=""):
    """Format, asset and margin reasons and warnings for one Kite place_order dict."""
    reasons, warns = [], []
    pre = label + ": " if label else ""
    sym = str(o.get("tradingsymbol", "")).strip().upper()
    exch = str(o.get("exchange") or "").strip().upper()
    tt = str(o.get("transaction_type", "")).strip().upper()
    typ = str(o.get("order_type", "")).strip().upper()
    product = str(o.get("product", "")).strip().upper()
    validity = str(o.get("validity") or "DAY").strip().upper()
    variety = str(o.get("variety") or "regular").strip().lower()
    if not sym:
        reasons.append("format: %sno tradingsymbol" % pre)
    if tt not in ("BUY", "SELL"):
        reasons.append("format: %stransaction_type must be BUY or SELL, got %r" % (pre, o.get("transaction_type")))
    q = o.get("quantity")
    if q is None:
        reasons.append("format: %sno quantity" % pre)
    elif isinstance(q, str):
        reasons.append("format: %squantity must be a number (%s), not the string %r: Kite's place_order takes "
                       "numbers" % (pre, q, q))
    elif _num_arg(q) is None or _num_arg(q) <= 0:
        reasons.append("format: %squantity must be a positive whole number, got %r" % (pre, q))
    elif abs(_num_arg(q) - round(_num_arg(q))) > 1e-9:
        reasons.append("format: %squantity must be whole shares (NSE has no fractions), got %r" % (pre, q))
    if variety != "regular":
        reasons.append("format: %svariety %r: this pack sends regular orders only (no cover orders, AMO, "
                       "iceberg or auction orders)" % (pre, o.get("variety")))
    if typ not in ORDER_TYPES:
        reasons.append("format: %sunknown order_type %r (MARKET, LIMIT, SL or SL-M)" % (pre, o.get("order_type")))
    if validity not in VALIDITIES:
        reasons.append("format: %sunknown validity %r" % (pre, o.get("validity")))
    elif validity == "TTL" and not o.get("validity_ttl"):
        reasons.append("format: %sa TTL order needs validity_ttl (minutes)" % pre)
    if not product:
        reasons.append("format: %sno product (MIS for an intraday trade)" % pre)
    elif product not in PRODUCTS:
        reasons.append("format: %sunknown product %r" % (pre, o.get("product")))
    elif product == "NRML":
        reasons.append("format: %sproduct NRML is for futures and options; an NSE day trade here is MIS" % pre)
    elif product == "MTF":
        if not c.get("allow_margin"):
            reasons.append("margin: %sproduct MTF borrows money (margin trading facility); margin is off "
                           "(\"allow_margin\": false)" % pre)
        else:
            warns.append("%sMTF: a margin position, you pay interest on the borrowed part" % pre)
    elif product == "CNC" and not label:
        warns.append("product CNC is delivery: Zerodha does not square it off at the close; a day trade "
                     "here is MIS")
    prices = {}
    for k in PRICE_FIELDS:
        v = o.get(k)
        if v is None or (v == 0 and typ in ("MARKET", "SL-M") and k == "price"):
            continue
        if isinstance(v, str):
            reasons.append("format: %s%s must be a number (%s), not the string %r: Kite's place_order takes "
                           "numbers" % (pre, k, v, v))
            continue
        n = _num_arg(v)
        if n is None or n <= 0:
            reasons.append("format: %s%s must be a positive number, got %r" % (pre, k, v))
            continue
        tick = tick_size(n)
        if D(n) != to_tick(n, ROUND_HALF_UP, tick):
            reasons.append("format: %s%s %s is not on the %s tick for that price; NSE rejects it (%s or %s)"
                           % (pre, k, n, tick, to_tick(n, ROUND_FLOOR, tick), to_tick(n, ROUND_CEILING, tick)))
            continue
        prices[k] = n
    if typ == "LIMIT" and o.get("price") is None:
        reasons.append("format: %sa LIMIT order needs a price" % pre)
    if typ in ("SL", "SL-M") and o.get("trigger_price") is None:
        reasons.append("format: %san %s order needs a trigger_price" % (pre, typ))
    if typ == "SL" and o.get("price") is None:
        reasons.append("format: %san SL order needs a price (its limit)" % pre)
    if typ == "SL" and "price" in prices and "trigger_price" in prices:
        if tt == "SELL" and prices["price"] > prices["trigger_price"] + 1e-9:
            reasons.append("format: %san SL sell's price %s must be at or under its trigger %s"
                           % (pre, _p(prices["price"]), _p(prices["trigger_price"])))
        if tt == "BUY" and prices["price"] < prices["trigger_price"] - 1e-9:
            reasons.append("format: %san SL buy's price %s must be at or over its trigger %s"
                           % (pre, _p(prices["price"]), _p(prices["trigger_price"])))
    if typ in ("MARKET", "SL-M"):
        warns.append("%s%s order: Kite's MCP place_order cannot send market protection, and Zerodha may reject a "
                     "%s API order without it (LIMIT and SL are the defaults)" % (pre, typ, typ))

    # asset
    if exch in ("NFO", "BFO") or (sym and FNO.search(sym)):
        if not c.get("allow_options"):
            reasons.append("asset: %s%s is a futures or options contract; F&O is off (\"allow_options\": false)"
                           % (pre, sym))
        else:
            warns.append("%sF&O order: options can lose 100%% of what you pay, futures are leveraged" % pre)
    elif exch == "BSE":
        reasons.append("asset: %sexchange BSE: this pack trades on the NSE only (exchange \"NSE\")" % pre)
    elif exch and exch != "NSE":
        reasons.append("asset: %sexchange %s: this pack trades NSE stocks and ETFs only" % (pre, exch))
    elif not exch:
        reasons.append("format: %sno exchange (\"NSE\")" % pre)
    elif sym and not NSE_SYMBOL.match(sym):
        reasons.append("asset: %s%s does not look like an NSE equity symbol; this pack trades NSE stocks and "
                       "ETFs only" % (pre, sym))
    return reasons, warns


# ---------------------------------------------------------------- the check

def check(orders, cfg, portfolio=None, prices=None, state=None, today=None, now=None, price_times=None):
    """Pure function. Returns {"results": [...], "pass": n, "block": n, "warnings": [...]}.

    now          naive UTC (default: the real clock); its India date is "today"
    price_times  {SYM: naive UTC time of that price}; None = every price counts as fresh"""
    c = dict(DEFAULTS)
    c.update(cfg or {})
    rates = dict(CHARGES)
    rates.update(c.get("charges") or {})
    prices = {str(k).upper(): float(v) for k, v in (prices or {}).items() if _f(v)}
    now_utc = now or parse_now(None)
    now_ist = to_ist(now_utc)
    today = today or now_ist.date().isoformat()
    acct, top_warn = account(c, portfolio, prices)
    blocked = {str(b).upper() for b in c.get("blocked") or []}
    done_today = placed_today(state, today)
    count_today = len(done_today)
    entries_today = [r for r in done_today if rec_is_entry(r)]
    loss_today = 0.0
    for r in entries_today:
        ml = _f(r.get("max_loss"))
        if ml is None:
            top_warn.append("an entry placed today (%s %s) has no max loss in the log; not counted in "
                            "the daily loss" % (r.get("side"), r.get("symbol")))
        else:
            loss_today += ml
    placed_keys = {(str(r.get("symbol", "")).upper(), str(r.get("side", "")).lower()) for r in done_today}
    win_reasons, win_warns = window_check(now_ist, c)
    seen = set()
    results = []
    batch_entries, batch_loss = 0, 0.0
    if not c.get("paper", True):
        top_warn.append("LIVE account (\"paper\": false): these orders use real money at Zerodha")
    top_warn.extend(win_warns)

    def fresh(sym):
        if sym not in prices:
            return None
        if price_times is None:
            return prices[sym]
        t = price_times.get(sym)
        if t is None:
            return None
        age = (now_utc - t).total_seconds()
        return prices[sym] if -60 <= age <= FRESH_MINUTES * 60 else None

    for i, o in enumerate(orders, 1):
        reasons, warns, info = [], [], []
        later = []                       # entry-only reasons, turned into warnings for an exit
        sym = str(o.get("tradingsymbol", "")).strip().upper()
        side = str(o.get("transaction_type", "")).strip().lower()
        typ = str(o.get("order_type", "")).strip().upper()
        plan = o.get("plan") if isinstance(o.get("plan"), dict) else {}
        q = _num_arg(o.get("quantity"))
        value = None

        r_fmt, w_fmt = kite_format(o, c)
        reasons.extend(r_fmt)
        warns.extend(w_fmt)

        so = o.get("stop_order")
        leg_stop = None
        if so is not None:
            if not isinstance(so, dict):
                reasons.append("format: stop_order must be a Kite order (an SL sell), got %r" % (so,))
            else:
                r_s, w_s = kite_format(so, c, "stop_order")
                reasons.extend(r_s)
                warns.extend(w_s)
                s_sym = str(so.get("tradingsymbol", "")).strip().upper()
                s_tt = str(so.get("transaction_type", "")).strip().upper()
                s_typ = str(so.get("order_type", "")).strip().upper()
                if s_sym and s_sym != sym:
                    reasons.append("format: the stop_order is for %s, not %s" % (s_sym, sym))
                for k in ("exchange", "product"):
                    if str(so.get(k) or "").upper() != str(o.get(k) or "").upper():
                        reasons.append("format: the stop_order's %s %r differs from the order's %r"
                                       % (k, so.get(k), o.get(k)))
                want = {"buy": "SELL", "sell": "BUY"}.get(side)
                if want and s_tt and s_tt != want:
                    reasons.append("format: the stop_order of a %s must be a %s" % (side.upper(), want))
                if s_typ and s_typ not in ("SL", "SL-M"):
                    reasons.append("format: the stop_order must be an SL or SL-M order, got %r" % so.get("order_type"))
                sq = _num_arg(so.get("quantity"))
                if sq is not None and q is not None:
                    if sq > q + 1e-9:
                        reasons.append("format: the stop_order sells %s, more than the %s this order buys: a filled "
                                       "stop would leave a short" % (_num(sq), _num(q)))
                    elif sq < q - 1e-9:
                        warns.append("the stop_order covers %s of the %s shares: the rest has no stop at Zerodha"
                                     % (_num(sq), _num(q)))
                leg_stop = _num_arg(so.get("trigger_price"))

        # the reference price: what one share costs to get in
        plan_entry = _f(plan.get("entry"))
        live = fresh(sym)
        ref, ref_from = None, None
        if typ in ("LIMIT", "SL") and _num_arg(o.get("price")):
            ref, ref_from = _num_arg(o["price"]), "limit"
        elif typ == "SL-M" and _num_arg(o.get("trigger_price")):
            ref, ref_from = _num_arg(o["trigger_price"]), "trigger"
        if ref is None:
            cands = [(p, w) for p, w in ((plan_entry, "plan"), (live, "price")) if p]
            if cands:
                ref, ref_from = max(cands, key=lambda x: x[0])
                ref = _paisa(ref)
                if live and ref_from == "price" and plan_entry and live > plan_entry + PAISA:
                    info.append("%s last %s is above the plan's entry %s: the risk is measured from %s"
                                % (sym, _p(ref), _p(plan_entry), _p(ref)))
            elif sym in prices:
                ref, ref_from = prices[sym], "stale price"
        if plan_entry and live is None and sym in prices and price_times is not None and typ == "MARKET":
            warns.append("the %s price in trader/prices.json is older than %d minutes: not used"
                         % (sym, FRESH_MINUTES))
        if q and ref:
            value = q * ref

        # entry or exit
        held = acct["shares"].get(sym, 0.0)
        held_value = acct["values"].get(sym, 0.0)
        exiting = False
        if side == "sell" and held > 1e-9 and q is not None:
            exiting = q <= held + 1e-9
        elif side == "buy" and held < -1e-9 and q is not None:
            exiting = q <= -held + 1e-9
        kind = "exit" if exiting else "entry"
        if kind == "entry" and value is None and q:
            later.append("price: no price for %s to size a %s-share order; add it to "
                         "trader/prices.json (trader-alerts: alerts.py quote %s --write)" % (sym, _num(q), sym))

        # blocked list and size
        if sym in blocked:
            later.append("blocked: %s is on your blocked list" % sym)
        if value is not None:
            if value < float(c.get("min_order_inr") or 0) - PAISA:
                later.append("size: %s is under your %s minimum order"
                             % (inr(value), inr(c["min_order_inr"])))
            if c.get("max_order_inr") and value > float(c["max_order_inr"]) + PAISA:
                later.append("size: %s is over your %s max order"
                             % (inr(value), inr(c["max_order_inr"])))

        # account effects, simulated in order
        cash = acct["cash"]
        total = cash + sum(acct["values"].values())
        buffer_inr = total * float(c.get("cash_buffer_pct") or 0) / 100.0
        if value is not None and side == "buy" and kind == "entry":
            after_cash = cash - value
            if after_cash < -PAISA and not c.get("allow_margin"):
                later.append("cash: needs %s but only %s cash; that would be MIS leverage (borrowed money), and "
                             "margin is off" % (inr(value), inr(cash)))
            elif after_cash < buffer_inr - PAISA:
                later.append("cash: leaves %s cash, under your %s%% buffer of %s"
                             % (inr(max(after_cash, 0)), _pct(c["cash_buffer_pct"]), inr(buffer_inr)))
            pos_after = held_value + value
            limit = total * float(c.get("max_position_pct") or 100) / 100.0
            if total > 0 and pos_after > limit + PAISA:
                later.append("position: %s would be %s, %.1f%% of the %s account, over your %s%% cap"
                             % (sym, inr(pos_after), 100.0 * pos_after / total, inr(total),
                                _pct(c["max_position_pct"])))
        if side == "sell" and sym and kind == "entry":
            over = (q > held + 1e-9) if q is not None else held <= 0
            if over and not c.get("allow_short"):
                reasons.append("short: selling more %s than you hold (%s shares); short selling is off. "
                               "If you bought it, record the fill first (ticket.py fill)" % (sym, _num(held)))

        # day-trade checks: stop, loss, reward:risk, daily loss, trades, window
        max_loss = risk_ps = rr = stop = target = charges = None
        if kind == "entry" and side in ("buy", "sell"):
            long = side == "buy"
            plan_stop = _f(plan.get("stop"))
            stop = leg_stop if leg_stop else plan_stop
            target = _f(o.get("target_price")) or _f(plan.get("target"))
            if leg_stop and plan_stop and abs(leg_stop - plan_stop) > PAISA:
                warns.append("the stop_order's trigger %s differs from the plan's stop %s: the stop_order's is the "
                             "one Zerodha uses" % (_p(leg_stop), _p(plan_stop)))
            if stop and not leg_stop:
                warns.append("the stop %s is in the plan only, not an order at Zerodha: set a stop alert "
                             "(trader-alerts: alerts.py add --from-plan)" % _p(stop))
            if not stop:
                if c.get("require_stop", True):
                    later.append("stop: no stop. Every entry needs one (\"require_stop\": true): a stop_order "
                                 "(an SL sell), from trader-exit and trader-budget")
                else:
                    warns.append("no stop: the max loss is not capped, so it is not counted in the daily loss")
            elif ref:
                if (long and stop >= ref - PAISA) or (not long and stop <= ref + PAISA):
                    later.append("stop: the stop %s must be %s the entry %s for a %s"
                                 % (_p(stop), "below" if long else "above", _p(ref), side))
                else:
                    risk_c = int(round(abs(ref - stop) * 100))
                    risk_ps = risk_c / 100.0
                    shares = q or 0
                    gross = round(shares * risk_c / 100.0, 2)
                    charges = round_trip_charges(shares, ref if long else stop, stop if long else ref, rates)
                    max_loss = round(gross + charges, 2)
                    cap = _f(c.get("max_loss_per_trade_inr"))
                    if cap and max_loss > cap + PAISA:
                        later.append("loss: %s shares x %s risk a share = %s + about %s charges = %s at the stop, "
                                     "over your %s max loss per trade"
                                     % (_num(shares), _p(risk_ps), inr(gross), inr(charges), inr(max_loss),
                                        inr(cap)))
                    min_rr = _f(c.get("min_reward_risk"))
                    if target:
                        if (long and target <= ref + PAISA) or (not long and target >= ref - PAISA):
                            later.append("target: the target %s must be %s the entry %s"
                                         % (_p(target), "above" if long else "below", _p(ref)))
                        else:
                            rr = round(int(round(abs(target - ref) * 100)) / float(risk_c), 2)
                            if min_rr and rr < min_rr - 1e-9:
                                later.append("reward:risk: %.2f (target %s, entry %s, stop %s) is under your "
                                             "minimum %s" % (rr, _p(target), _p(ref), _p(stop), _num(min_rr)))
                    elif min_rr:
                        later.append("reward:risk: no target to measure it against (min_reward_risk %s)"
                                     % _num(min_rr))
            daily = _f(c.get("max_daily_loss_inr"))
            if daily and max_loss is not None:
                used = loss_today + batch_loss
                if used + max_loss > daily + PAISA:
                    later.append("daily loss: %s already at risk today plus %s for this one = %s, over your %s "
                                 "daily loss limit" % (inr(used), inr(max_loss), inr(used + max_loss),
                                                       inr(daily)))
            cap_t = int(c.get("max_trades_per_day") or 0)
            if cap_t and len(entries_today) + batch_entries >= cap_t:
                later.append("trades: %d entries placed today plus %d passed earlier in this batch, your limit "
                             "is %d a day" % (len(entries_today), batch_entries, cap_t))
            later.extend(win_reasons)
            if stop and ref and risk_ps:
                info.append("entry %s (%s), stop %s, target %s: risk %s a share"
                            % (_p(ref), REF_WORDS.get(ref_from, ref_from), _p(stop),
                               _p(target) if target else "none", _p(risk_ps)))
                info.append("max loss %s incl. about %s charges%s, reward:risk %s%s"
                            % (inr(max_loss), inr(charges), (" (limit %s)" % inr(c["max_loss_per_trade_inr"]))
                               if _f(c.get("max_loss_per_trade_inr")) else "",
                               ("%.2f" % rr) if rr is not None else "n/a",
                               (" (min %s)" % _num(c["min_reward_risk"])) if _f(c.get("min_reward_risk")) else ""))
            if daily or cap_t:
                info.append("today before this: %d of %s trades, %s of %s daily loss"
                            % (len(entries_today) + batch_entries, cap_t or "unlimited",
                               inr(loss_today + batch_loss), inr(daily) if daily else "no limit on"))

        # duplicates and the daily order limit
        key = (sym, side)
        if key in seen:
            later.append("duplicate: a second %s %s in this batch" % (side, sym))
        if key in placed_keys:
            later.append("duplicate: a %s %s was already placed today" % (side, sym))
        passed_so_far = sum(1 for r in results if r["verdict"] == "PASS")
        cap_n = int(c.get("max_orders_per_day") or 0)
        if cap_n and count_today + passed_so_far >= cap_n:
            later.append("daily limit: %d placed today plus %d passed earlier in this batch, "
                         "your limit is %d a day" % (count_today, passed_so_far, cap_n))
        seen.add(key)

        if kind == "entry":
            reasons.extend(later)
        else:
            warns.extend("exit, not blocked: " + x for x in later)

        verdict = "BLOCK" if reasons else "PASS"
        if verdict == "PASS":
            if kind == "entry" and side in ("buy", "sell"):
                batch_entries += 1
                batch_loss += max_loss or 0.0
            if side == "buy":
                if value is not None:
                    acct["cash"] -= value
                    acct["values"][sym] = acct["values"].get(sym, 0.0) + value
                if q is not None:
                    acct["shares"][sym] = held + q
            elif side == "sell":
                if value is not None:
                    acct["cash"] += value
                    acct["values"][sym] = max(acct["values"].get(sym, 0.0) - value, 0.0)
                if q is not None:
                    acct["shares"][sym] = held - q
        results.append({
            "index": i, "symbol": sym, "side": side, "kind": kind,
            "amount": "%s shares" % _num(q) if q is not None else "%s shares" % o.get("quantity"),
            "value_inr": round(value, 2) if value is not None else None,
            "entry_ref": round(ref, 2) if ref else None, "entry_ref_from": ref_from,
            "stop": stop, "target": target, "risk_per_share": risk_ps, "charges": charges,
            "max_loss": max_loss, "reward_risk": rr, "fingerprint": fingerprint(o), "verdict": verdict,
            "reasons": reasons, "warnings": warns, "info": info,
        })

    return {
        "date": today,
        "now_ist": now_ist.strftime("%Y-%m-%d %H:%M"),
        "paper": bool(c.get("paper", True)),
        "results": results,
        "pass": sum(1 for r in results if r["verdict"] == "PASS"),
        "block": sum(1 for r in results if r["verdict"] == "BLOCK"),
        "warnings": top_warn,
        "cash_after": round(acct["cash"], 2),
        "today": {"orders_placed": count_today, "entries_placed": len(entries_today),
                  "loss_at_risk": round(loss_today, 2)},
    }


def report(res):
    lines = []
    for w in res["warnings"]:
        lines.append("WARNING: " + w)
    if res["warnings"]:
        lines.append("")
    for r in res["results"]:
        lines.append("%-5s #%d %s %s %s, %s" % (r["verdict"], r["index"], r["side"], r["symbol"], r["amount"],
                                                r["kind"]))
        for x in r.get("info", []):
            lines.append("          " + x)
        for x in r["reasons"]:
            lines.append("        - " + x)
        for x in r["warnings"]:
            lines.append("        ~ " + x)
    lines.append("")
    lines.append("%d passed, %d blocked at %s IST. Cash after the passed orders: %s (%s account)."
                 % (res["pass"], res["block"], res["now_ist"], inr(res["cash_after"]),
                    "paper" if res["paper"] else "LIVE"))
    if res["block"]:
        lines.append("Blocked orders do not go to the broker. Fix the order or your rules, then check again.")
    else:
        lines.append("Every order passed. trader-broker still asks you yes or no for each one.")
    lines.append("Education and tooling, not financial advice.")
    return "\n".join(lines)


def load_prices(pdoc):
    """prices.json -> ({SYM: price}, {SYM: price time as naive UTC or None}).

    The price time is the quote's own time (detail.time), else when it was fetched
    (detail.fetched), else the file's as_of."""
    if not isinstance(pdoc, dict):
        return {}, {}
    prices = pdoc.get("prices", pdoc)
    if not isinstance(prices, dict):
        return {}, {}
    out, times = {}, {}
    as_of = parse_utc(pdoc.get("as_of"))
    detail = pdoc.get("detail") or {}
    for s, p in prices.items():
        if _f(p) is None:
            continue
        s = str(s).upper()
        out[s] = float(p)
        d = detail.get(s) or {}
        times[s] = parse_utc(d.get("time")) or parse_utc(d.get("fetched")) or as_of
    return out, times


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check proposed orders against your limits (trader-risk).")
    ap.add_argument("--orders", default="trader/orders.json")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--portfolio", default="trader/portfolio.json",
                    help="your account; if missing, a fresh account funded with the budget")
    ap.add_argument("--prices", default="trader/prices.json")
    ap.add_argument("--state", default="trader/state/broker.json", help="what trader-broker placed today")
    ap.add_argument("--now", help="pretend it is this India time, e.g. \"2026-10-19 10:00\"")
    ap.add_argument("--today", help="YYYY-MM-DD (default: today in India)")
    ap.add_argument("--out", default="trader/state/risk.json", help="where the verdicts are recorded")
    ap.add_argument("--no-write", action="store_true", help="do not record the verdicts")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        now = parse_now(a.now)
        if a.today and not re.match(r"^\d{4}-\d{2}-\d{2}$", a.today):
            raise RiskError("--today wants a date like 2026-10-19")
        doc = read_json(a.orders, "orders file (run trader-budget first)")
        orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
        if not isinstance(orders, list) or not orders:
            raise RiskError("no orders in %s" % a.orders)
        cfg = read_json(a.config, "config", required=False) or {}
        portfolio = read_json(a.portfolio, "portfolio", required=False)
        pdoc = read_json(a.prices, "prices", required=False) or {}
        prices, times = load_prices(pdoc)
        state = read_json(a.state, "broker state", required=False)
        res = check(orders, cfg, portfolio, prices, state, a.today, now, times)
        if pdoc.get("as_of"):
            res["prices_as_of"] = pdoc["as_of"]
        if not a.no_write:
            d = os.path.dirname(a.out)
            if d:
                os.makedirs(d, exist_ok=True)
            rec = dict(res)
            rec["checked_at"] = utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
            rec["orders_file"] = a.orders
            tmp = a.out + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(rec, fh, indent=2)
                fh.write("\n")
            os.replace(tmp, a.out)
    except RiskError as e:
        print("check.py: %s" % e, file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2) if a.json else report(res))
    return 1 if res["block"] else 0


if __name__ == "__main__":
    sys.exit(main())
