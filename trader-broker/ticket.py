#!/usr/bin/env python3
"""Order tickets and the decision log for trader-broker. Standard library only.

This tool never talks to a broker and never places anything. It does its
work around the one moment that matters, your yes:

  show      renders each proposed order as a plain-English ticket plus the
            exact order, field by field, as Kite's place_order takes it.
            An entry shows its two calls: the LIMIT buy, then (only after it
            fills) its stop-loss SL sell; the target, which is watched and not
            sent; and the max loss at the stop with charges. An order is READY
            only if trader-risk passed THIS exact order today (matched by
            fingerprint) and nobody has decided on it yet.
  approve   records your explicit yes for ONE order, with your reply word for
            word. It refuses "yes to all", "sure", "ok", a yes that names a
            different stock or amount, and any order that is not READY.
  decline   records a no.
  result    records what happened: placed (with Zerodha's order id), denied,
            or failed; --leg stop for the stop-loss order. In a paper account
            it records the paper order.
  match     reads Kite's order book (kite.py orders --write saves it) and
            records the order you placed yourself in Kite for this ticket:
            COMPLETE is placed and filled, OPEN or TRIGGER PENDING is placed,
            REJECTED or CANCELLED failed. --leg stop for its stop-loss.
  fill      records a fill in trader/portfolio.json (cash and shares).
  flatten   builds a sell ticket that closes a symbol's position by flat_by:
            a marketable LIMIT from a fresh price, else MARKET. It still goes
            through trader-risk and your yes, and the open stop-loss order is
            canceled first.
  log       prints today's decisions.

    python3 ticket.py show                    # every order, READY or not
    python3 ticket.py show --index 1 --json   # one ticket as JSON
    python3 ticket.py approve --index 1 --reply "yes"
    python3 ticket.py result --index 1 --status placed --broker-id 261019000123456
    python3 ticket.py result --index 1 --leg stop --status placed --broker-id 261019000123999
    python3 ticket.py match --index 1                # after kite.py orders --write
    python3 ticket.py match --index 1 --leg stop
    python3 ticket.py fill --symbol DEMO --side buy --qty 54 --price 1366.40
    python3 ticket.py flatten --symbol DEMO --qty 54 --price 1371.20
    python3 ticket.py log

Paper (the default, "paper": true): Claude calls no Kite tool at all. After
your yes the order is recorded as a paper order and filled at the price the
market gives (kite.py quote, Kite get_ltp or trader-alerts' quote).

Live ("paper": false) goes one of two ways, "order_route" in the config:
  manual    (the default) you place the exact order yourself in Kite, the app
            or kite.zerodha.com, after your yes; then kite.py orders --write
            and ticket.py match record it from Kite's order book. Nothing is
            ever sent for you: placing it by hand is the last check.
  kite_mcp  Claude calls place_order on your self-hosted Kite MCP server, and
            Claude's own Allow once / Deny card asks you before each call.

Everything is written to trader/state/broker.json (append only). Dates are
India dates; --now "2026-10-19 10:00" pins the clock.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP

KITE_FIELDS = ("variety", "exchange", "tradingsymbol", "transaction_type", "quantity", "product",
               "order_type", "price", "trigger_price", "validity", "validity_ttl", "disclosed_quantity", "tag")
VALIDITY_WORDS = {"DAY": "good for today", "IOC": "immediate or cancel"}
PRODUCT_WORDS = {"MIS": "MIS intraday", "CNC": "CNC delivery", "MTF": "MTF margin", "NRML": "NRML"}
YES_WORDS = ("yes", "y")
BATCH_WORDS = {"all", "every", "everything", "rest", "remaining", "them", "both", "batch", "each"}
DOUBT_WORDS = {"no", "not", "dont", "don't", "wait", "cancel", "stop", "but", "maybe", "later",
               "hold", "unless", "if"}
FRESH_MINUTES = 15
DEFAULT_FLAT_BY = "15:05"
DEFAULT_EXIT_BUFFER = 0.5
CANCEL_FIRST = "the open stop-loss order for %s (get_orders: status TRIGGER PENDING), cancel_order it first"
ROUTES = ("manual", "kite_mcp")
KITE_ORDERS = "trader/state/kite-orders.json"
KITE_FILLED, KITE_DEAD = "COMPLETE", ("REJECTED", "CANCELLED")


class TicketError(Exception):
    pass


# ---------------------------------------------------------------- time (India, no tz files)

IST = datetime.timedelta(hours=5, minutes=30)   # India Standard Time: UTC+5:30 all year, no daylight saving


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
        raise TicketError('--now wants an India time like "2026-10-19 10:15" (or ...Z for UTC)')
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


def ist_iso(now_utc):
    """naive UTC -> "2026-10-19T10:00:00+05:30" (India wall clock with its offset)."""
    return "%s+05:30" % to_ist(now_utc).replace(microsecond=0).isoformat()


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
        raise TicketError("%s is a BSE listing: this pack trades NSE symbols (like RELIANCE)" % text)
    if not NSE_SYMBOL.match(s):
        raise TicketError("%r is not an NSE symbol (like RELIANCE, M&M or BAJAJ-AUTO)" % text)
    return s


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


# ---------------------------------------------------------------- files

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise TicketError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise TicketError("%s is not valid JSON: %s" % (path, e))


def write_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def rec_day(rec):
    """The trading day a decision belongs to: its "date" (the day it was made under), else the day of "at"."""
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


# ---------------------------------------------------------------- the order

def fingerprint(order):
    """Same function in trader-risk/check.py: the exact order that was checked,
    its stop-loss order and its target."""
    core = {k: order[k] for k in KITE_FIELDS if k in order and order[k] is not None}
    stop = order.get("stop_order")
    if isinstance(stop, dict):
        core["stop_order"] = {k: stop[k] for k in KITE_FIELDS if k in stop and stop[k] is not None}
    if order.get("target_price") is not None:
        core["target_price"] = order["target_price"]
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _str_num(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        f = float(v)
        return str(int(f)) if f == int(f) else repr(f)
    return str(v)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def tool_input(order):
    """The exact arguments for Kite's place_order: Kite's parameter names, numbers
    as numbers. "stop_order", "target_price", "why" and "plan" are ours and are
    never sent with it."""
    if not isinstance(order, dict):
        return None
    return {k: order[k] for k in KITE_FIELDS if k in order and order[k] is not None}


def stop_input(order):
    """The place_order arguments for the stop-loss, sent only after the entry fills."""
    so = order.get("stop_order")
    return tool_input(so) if isinstance(so, dict) else None


def _p(v):
    return "%.2f" % float(v)


def leg_words(o):
    """BUY 54 DEMO, limit 1366.40, MIS intraday, NSE, good for today"""
    typ = str(o.get("order_type", "")).upper()
    if typ == "LIMIT":
        how = "limit %s" % _p(o["price"]) if o.get("price") is not None else "limit (no price)"
    elif typ == "SL":
        how = "SL trigger %s limit %s" % (_p(o.get("trigger_price") or 0), _p(o.get("price") or 0))
    elif typ == "SL-M":
        how = "SL-M trigger %s" % _p(o.get("trigger_price") or 0)
    else:
        how = typ.lower() or "?"
    validity = str(o.get("validity") or "DAY").upper()
    return "%s %s %s, %s, %s, %s, %s" % (
        str(o.get("transaction_type", "")).upper(), _str_num(o.get("quantity")), o.get("tradingsymbol", "?"), how,
        PRODUCT_WORDS.get(str(o.get("product", "")).upper(), o.get("product")), o.get("exchange", "?"),
        VALIDITY_WORDS.get(validity, validity.lower()))


def max_loss(order):
    """(rupees, stop, charges) lost if the stop fills exactly at the stop, with the
    estimated round-trip charges, or (None, stop, None)."""
    plan = order.get("plan") or {}
    so = order.get("stop_order") if isinstance(order.get("stop_order"), dict) else {}
    stop = _f(so.get("trigger_price")) or _f(plan.get("stop"))
    q = _f(order.get("quantity"))
    if not stop or not q:
        return None, stop, None
    typ = str(order.get("order_type", "")).upper()
    if typ in ("LIMIT", "SL") and _f(order.get("price")):
        entry = _f(order["price"])
    elif typ == "SL-M" and _f(order.get("trigger_price")):
        entry = _f(order["trigger_price"])
    else:
        entry = _f(plan.get("entry"))
    if not entry:
        return None, stop, None
    if _f(plan.get("max_loss")) is not None and _f(plan.get("entry")) is not None \
            and abs(_f(plan["entry"]) - entry) < 0.005 and abs((_f(plan.get("stop")) or stop) - stop) < 0.005:
        return round(_f(plan["max_loss"]), 2), stop, _f(plan.get("charges"))
    risk = abs(entry - stop)
    charges = round_trip_charges(q, entry, stop)
    return round(q * int(round(risk * 100)) / 100.0 + charges, 2), stop, charges


def plain_parts(order):
    """The ticket in plain words, one part per line."""
    so = order.get("stop_order")
    if isinstance(so, dict):
        parts = [leg_words(order), "then stop-loss: %s, placed after the buy fills" % leg_words(so)]
        if order.get("target_price") is not None:
            parts.append("target %s (watched by trader-alerts, not sent to Zerodha)" % _p(order["target_price"]))
        loss, stop, charges = max_loss(order)
        if loss is not None:
            parts.append("max loss about %s if the stop fills at %s, incl. about %s charges (it can fill worse, "
                         "or not at all, in a fast market)" % (inr(loss), _p(stop), inr(charges or 0)))
        return parts
    q = _str_num(order.get("quantity"))
    what = "%s share%s of %s" % (q, "" if q == "1" else "s", order.get("tradingsymbol", "?"))
    typ = str(order.get("order_type", "")).upper()
    if typ == "LIMIT":
        how = "limit order at %s" % _p(order.get("price") or 0)
    elif typ == "SL":
        how = "SL order, trigger %s limit %s" % (_p(order.get("trigger_price") or 0), _p(order.get("price") or 0))
    elif typ == "SL-M":
        how = "SL-M order, trigger %s" % _p(order.get("trigger_price") or 0)
    else:
        how = "market order"
    validity = str(order.get("validity") or "DAY").upper()
    parts = ["%s %s, %s, %s, %s" % (str(order.get("transaction_type", "")).upper(), what, how,
                                    PRODUCT_WORDS.get(str(order.get("product", "")).upper(), order.get("product")),
                                    VALIDITY_WORDS.get(validity, validity.lower()))]
    if order.get("cancel_first"):
        parts.append("first: cancel " + str(order["cancel_first"]))
    return parts


def plain(order):
    """BUY 54 DEMO, limit 1366.40, ... / then stop-loss: SELL 54 DEMO, SL ... / target ... / max loss ..."""
    return " / ".join(plain_parts(order))


def amount_numbers(order):
    nums = []
    for o in (order, order.get("stop_order") if isinstance(order.get("stop_order"), dict) else {}):
        for k in ("quantity", "price", "trigger_price"):
            v = _f(o.get(k))
            if v is not None:
                nums.append(v)
    for v in (_f(order.get("target_price")), max_loss(order)[0]):
        if v is not None:
            nums.append(v)
    return nums


def parse_yes(reply, order, batch_symbols=()):
    """Is this reply an explicit yes for THIS one order? Returns (ok, why)."""
    raw = (reply or "").strip()
    if not raw:
        return False, "the reply is empty"
    if "?" in raw:
        return False, "the reply is a question, not a yes"
    words = re.findall(r"[a-z']+", raw.lower())
    if not words or words[0] not in YES_WORDS:
        return False, "only a reply that starts with \"yes\" counts (not ok, sure, haan, go, do it)"
    if BATCH_WORDS & set(words):
        return False, "a yes covers one order at a time, not a batch"
    if DOUBT_WORDS & set(words[1:]):
        return False, "the reply has a condition or a doubt in it; ask again"
    sym = str(order.get("tradingsymbol", "")).upper()
    tokens = set(re.findall(r"[A-Z][A-Z0-9&\-]{0,19}", raw.upper()))
    others = {s.upper() for s in batch_symbols} - {sym}
    named = tokens & others
    if named:
        return False, "the reply names %s, but this order is for %s" % (", ".join(sorted(named)), sym)
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", raw)]
    if nums:
        want = amount_numbers(order)
        if not any(abs(n - w) < 0.005 for n in nums for w in want):
            return False, "the reply says %s, but this order is %s" % (
                ", ".join(_str_num(n) for n in nums), plain(order))
    return True, "explicit yes for %s" % plain(order)


# ---------------------------------------------------------------- state

def load_state(path):
    return read_json(path, "broker state", required=False) or {"orders": []}


def latest(state, fp, today, leg=None):
    """The latest decision recorded today for this fingerprint (the entry's, or
    the stop-loss leg's with leg="stop"), or None."""
    found = None
    for rec in state.get("orders", []):
        if rec.get("fingerprint") == fp and rec_day(rec) == today and rec.get("leg") == leg:
            found = rec
    return found


def risk_verdict(risk, fp, today):
    if not risk:
        return None, "trader-risk has not checked these orders (run check.py)"
    if risk.get("date") != today:
        return None, "the risk check is from %s, not today; run check.py again" % risk.get("date")
    for r in risk.get("results", []):
        if r.get("fingerprint") == fp:
            return r, None
    return None, "this exact order was not in the last risk check (it changed or is new); run check.py again"


def route_of(cfg):
    """How a live order reaches Zerodha: "manual" (you place it in Kite, the
    default) or "kite_mcp" (Claude calls place_order behind its Allow once card)."""
    r = str((cfg or {}).get("order_route") or "manual").strip().lower()
    if r not in ROUTES:
        raise TicketError("order_route in the config is manual or kite_mcp, not %r" % r)
    return r


def status_of(order, risk, state, today, route="kite_mcp"):
    """(ready, label, risk_result)"""
    fp = fingerprint(order)
    rv, why = risk_verdict(risk, fp, today)
    if rv is None:
        return False, "NOT READY: " + why, None
    if rv.get("verdict") != "PASS":
        return False, "BLOCKED by trader-risk: " + "; ".join(rv.get("reasons", [])), rv
    rec = latest(state, fp, today)
    if rec:
        st = rec.get("status")
        if st == "approved":
            wait = ("waiting for you to place it in Kite (then kite.py orders --write, ticket.py match)"
                    if route == "manual" else "waiting for the place_order call")
            return False, "APPROVED by you at %s, %s" % (rec.get("at"), wait), rv
        label = "DONE: %s at %s" % (st, rec.get("at"))
        if st == "placed" and isinstance(order.get("stop_order"), dict):
            leg = latest(state, fp, today, "stop")
            label += ("; stop-loss %s at %s" % (leg.get("status"), leg.get("at")) if leg
                      else "; stop-loss not recorded yet: place it after the buy fills")
        return False, label, rv
    return True, "READY: trader-risk PASS, waiting for your yes", rv


def kind_of(order, rv):
    """entry or exit, as trader-risk saw it (older checks: a buy is an entry)."""
    if rv and rv.get("kind"):
        return rv["kind"]
    return "entry" if str(order.get("transaction_type", "")).upper() == "BUY" else "exit"


# ---------------------------------------------------------------- rendering

def _json_lines(doc, indent="    "):
    return [indent + l for l in json.dumps(doc, indent=2).splitlines()]


def render(order, idx, total, cfg, risk, state, today):
    paper = cfg.get("paper", True)
    route = route_of(cfg)
    ready, label, rv = status_of(order, risk, state, today, "paper" if paper else route)
    acct = "PAPER account (nothing is sent to Zerodha)" if paper else "LIVE ACCOUNT, REAL MONEY, Zerodha (Kite)"
    lines = ["ORDER %d of %d, %s" % (idx, total, acct)]
    for part in plain_parts(order):
        lines.append("  " + part)
    if order.get("why"):
        lines.append("  why: " + order["why"])
    lines.append("  " + label)
    for w in (rv or {}).get("warnings", []):
        lines.append("  note: " + w)
    broker = str(cfg.get("broker", "zerodha")).lower()
    if broker != "zerodha":
        lines.append("  note: broker %r in the config: these steps are written for Zerodha (Kite)" % broker)
    so = stop_input(order)
    if paper:
        lines.append("  Paper account: Claude calls no Kite tool for this order. After your yes it is recorded as")
        lines.append("  a paper order and filled at the price the market gives (get_ltp or alerts.py quote).")
        lines.append("  The order as Kite's place_order would take it, for reference:")
        lines.extend(_json_lines(tool_input(order)))
        if so:
            lines.append("  and its stop-loss, after the fill:")
            lines.extend(_json_lines(so))
    elif route == "manual":
        if order.get("cancel_first"):
            lines.append("  First cancel the open stop-loss order for %s yourself in Kite (Orders: the TRIGGER "
                         "PENDING sell)." % order.get("tradingsymbol"))
        lines.append("  After your yes, YOU place this exact order in Kite (the app or kite.zerodha.com); Claude")
        lines.append("  does not place it:")
        lines.append("    " + leg_words(order))
        lines.append("  field by field, as Kite names them:")
        lines.extend(_json_lines(tool_input(order)))
        if so:
            lines.append("  Once it is COMPLETE in Kite's order book, place its stop-loss yourself the same way:")
            lines.append("    " + leg_words(so))
            lines.extend(_json_lines(so))
            lines.append("  One yes covers both: the stop-loss only sells the shares this order buys. Never place it")
            lines.append("  before the buy fills: a triggered stop with no shares would open a short.")
        lines.append("  Then: kite.py orders --write, and ticket.py match --index %d%s records them." % (
            idx, " (and --leg stop)" if so else ""))
    else:
        if order.get("cancel_first"):
            lines.append("  First cancel %s (cancel_order, variety regular, its order_id)." % order["cancel_first"])
        lines.append("  After your yes, Claude calls place_order (Kite MCP) with exactly:")
        lines.extend(_json_lines(tool_input(order)))
        if so:
            lines.append("  Once it fills (get_orders shows COMPLETE), Claude calls place_order for the stop-loss")
            lines.append("  with exactly:")
            lines.extend(_json_lines(so))
            lines.append("  One yes covers both calls: the stop-loss only sells the shares this order buys. It is")
            lines.append("  never placed before the buy fills: a triggered stop with no shares would open a short.")
    if ready:
        lines.append("  Reply \"yes\" to place this one order, or \"no\" to skip it.")
        if not paper and route == "manual":
            lines.append("  Then you place it in Kite by hand: that is the last check, and nothing is sent for you.")
        elif not paper:
            lines.append("  Claude's own card then asks again before each Kite call: Allow once places it, Deny stops it.")
    return "\n".join(lines), ready


def ticket_json(order, idx, cfg, risk, state, today):
    paper = bool(cfg.get("paper", True))
    route = route_of(cfg)
    ready, label, rv = status_of(order, risk, state, today, "paper" if paper else route)
    loss, stop, charges = max_loss(order)
    return {"index": idx, "ready": ready, "status": label, "plain": plain(order),
            "fingerprint": fingerprint(order), "paper": paper, "order_route": "paper" if paper else route,
            "broker": cfg.get("broker", "zerodha"), "tool": "place_order",
            "tool_input": tool_input(order), "stop_input": stop_input(order),
            "target_price": order.get("target_price"), "cancel_first": order.get("cancel_first"),
            "why": order.get("why", ""), "max_loss": loss, "stop": stop, "charges": charges}


# ---------------------------------------------------------------- fills -> portfolio

def apply_fill(portfolio_path, cfg, symbol, side, qty, price, date):
    """Record a filled order in trader/portfolio.json: cash and shares, with a
    cost-basis lot per buy (oldest lots are relieved first on a sell). Charges
    are not deducted: your contract note has them."""
    pf = read_json(portfolio_path, "portfolio", required=False)
    if pf is None:
        budget = cfg.get("budget")
        if not budget:
            raise TicketError("no %s yet and no budget in the config to start it from" % portfolio_path)
        pf = {"cash": float(budget), "positions": []}
    symbol = nse_symbol(symbol)
    qty, price = float(qty), float(price)
    if qty <= 0 or price <= 0:
        raise TicketError("a fill needs a positive qty and price")
    cost = round(qty * price, 2)
    pos = None
    for p in pf.setdefault("positions", []):
        if str(p.get("ticker", "")).upper() == symbol:
            pos = p
    if side == "buy":
        if pos is None:
            pos = {"ticker": symbol, "shares": 0.0, "lots": []}
            pf["positions"].append(pos)
        lots = pos.setdefault("lots", [])
        lots.append({"id": "%s-%d" % (symbol, len(lots) + 1), "date": date, "shares": qty, "cost": cost})
        pos["shares"] = round(float(pos.get("shares", 0)) + qty, 6)
        pf["cash"] = round(float(pf.get("cash", 0)) - cost, 2)
    else:
        if pos is None or float(pos.get("shares", 0)) + 1e-9 < qty:
            raise TicketError("cannot record selling %s %s: the portfolio holds %s"
                              % (_str_num(qty), symbol, pos.get("shares", 0) if pos else 0))
        left = qty
        for lot in sorted(pos.get("lots", []), key=lambda l: l.get("date", "")):
            if left <= 1e-9:
                break
            take = min(left, float(lot["shares"]))
            per = float(lot["cost"]) / float(lot["shares"]) if float(lot["shares"]) else 0
            lot["cost"] = round(float(lot["cost"]) - per * take, 2)
            lot["shares"] = round(float(lot["shares"]) - take, 6)
            left -= take
        pos["lots"] = [l for l in pos.get("lots", []) if float(l["shares"]) > 1e-9]
        pos["shares"] = round(float(pos["shares"]) - qty, 6)
        if pos["shares"] <= 1e-9:
            pf["positions"].remove(pos)
        pf["cash"] = round(float(pf.get("cash", 0)) + cost, 2)
    write_json(portfolio_path, pf)
    return pf


def log_fill(state_path, now, today, symbol, side, qty, price, paper=None, broker_id=None):
    """A fill in the log, so trader-risk and the log can see it."""
    state = load_state(state_path)
    rec = {"at": ist_iso(now), "date": today, "status": "filled", "symbol": symbol.upper(), "side": side,
           "qty": _str_num(float(qty)), "price": float(price),
           "plain": "%s %s %s at %s" % (side.upper(), _str_num(float(qty)), symbol.upper(), _p(price))}
    if paper is not None:
        rec["paper"] = bool(paper)
    if broker_id:
        rec["broker_order_id"] = str(broker_id)
    state.setdefault("orders", []).append(rec)
    write_json(state_path, state)


# ---------------------------------------------------------------- commands

def _clock(a):
    now = parse_now(getattr(a, "now", None))
    today = getattr(a, "today", None) or to_ist(now).date().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", today):
        raise TicketError("--today wants a date like 2026-10-19")
    return now, today


def _load(a):
    doc = read_json(a.orders, "orders file (run trader-budget, or ticket.py flatten, first)")
    orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
    if not isinstance(orders, list) or not orders:
        raise TicketError("no orders in %s" % a.orders)
    cfg = read_json(a.config, "config", required=False) or {}
    risk = read_json(a.risk, "risk check", required=False)
    state = load_state(a.state)
    now, today = _clock(a)
    return orders, cfg, risk, state, today, now


def _pick(orders, index):
    if index is None:
        raise TicketError("say which order: --index N")
    if not 1 <= index <= len(orders):
        raise TicketError("there is no order %d (there are %d)" % (index, len(orders)))
    return orders[index - 1]


def cmd_show(a):
    orders, cfg, risk, state, today, now = _load(a)
    picks = [(a.index, _pick(orders, a.index))] if a.index else list(enumerate(orders, 1))
    if a.json:
        out = [ticket_json(o, i, cfg, risk, state, today) for i, o in picks]
        print(json.dumps(out[0] if a.index else out, indent=2))
        return 0 if any(t["ready"] for t in out) else 1
    blocks, any_ready = [], False
    for i, o in picks:
        text, ready = render(o, i, len(orders), cfg, risk, state, today)
        blocks.append(text)
        any_ready = any_ready or ready
    print("\n\n".join(blocks))
    print("\nOne order at a time: a yes covers one ticket, never the batch. Education and tooling, "
          "not financial advice.")
    return 0 if any_ready else 1


def cmd_approve(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    paper = bool(cfg.get("paper", True))
    route = route_of(cfg)
    ready, label, rv = status_of(order, risk, state, today, "paper" if paper else route)
    if not ready:
        raise TicketError("order %d is not ready: %s" % (a.index, label))
    ok, why = parse_yes(a.reply, order, [o.get("tradingsymbol", "") for o in orders])
    if not ok:
        print("Not recorded: %s. Ask the user again with the ticket for order %d." % (why, a.index))
        return 1
    loss, stop, charges = max_loss(order)
    rec = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fingerprint(order),
           "symbol": str(order.get("tradingsymbol", "")).upper(),
           "side": str(order.get("transaction_type", "")).lower(), "qty": order.get("quantity"),
           "kind": kind_of(order, rv), "max_loss": loss, "stop": stop, "plain": plain(order),
           "status": "approved", "reply": a.reply, "paper": paper, "broker": cfg.get("broker", "zerodha")}
    if not paper:
        rec["route"] = route
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    print("Recorded your yes for order %d: %s." % (a.index, plain(order)))
    if paper:
        print("Paper account: do not call any Kite tool. Record the paper order and its fill at the price the "
              "market gives (a LIMIT buy fills once the price trades at or under its limit):")
        print("  ticket.py result --index %d --status placed --filled-qty %s --filled-price PRICE"
              % (a.index, _str_num(order.get("quantity"))))
        if stop_input(order):
            print("The stop-loss is a paper order too: ticket.py result --index %d --leg stop --status placed, "
                  "and trader-alerts watches it (alerts.py add --from-plan)." % a.index)
        return 0
    if route == "manual":
        if order.get("cancel_first"):
            print("First cancel the open stop-loss order for %s yourself in Kite (Orders: the TRIGGER PENDING sell)."
                  % order.get("tradingsymbol"))
        print("Now place exactly this order yourself in Kite (the app or kite.zerodha.com). Claude does not place it:")
        print("  " + leg_words(order))
        print("When it shows in Kite's order book: kite.py orders --write, then ticket.py match --index %d." % a.index)
        if stop_input(order):
            print("Once the buy is COMPLETE, place its stop-loss yourself the same way:")
            print("  " + leg_words(stop_input(order)))
            print("and record it: kite.py orders --write, then ticket.py match --index %d --leg stop." % a.index)
        return 0
    if order.get("cancel_first"):
        print("First cancel %s (cancel_order: variety regular, its order_id; Needs approval)."
              % order["cancel_first"])
    print("Now call place_order with exactly:")
    print(json.dumps(tool_input(order), indent=2))
    print("Claude's card shows Allow once / Deny. Then: ticket.py result --index %d --status "
          "placed|denied|failed --broker-id ID" % a.index)
    if stop_input(order):
        print("After the buy fills, call place_order for the stop-loss with exactly:")
        print(json.dumps(stop_input(order), indent=2))
        print("and record it: ticket.py result --index %d --leg stop --status placed --broker-id ID" % a.index)
    return 0


def cmd_decline(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    rec = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fingerprint(order),
           "symbol": str(order.get("tradingsymbol", "")).upper(),
           "side": str(order.get("transaction_type", "")).lower(),
           "plain": plain(order), "status": "declined", "reply": a.reply or "no"}
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    print("Recorded: you said no to order %d (%s). Nothing was sent." % (a.index, plain(order)))
    return 0


def cmd_result(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    fp = fingerprint(order)
    rec = latest(state, fp, today)
    paper = bool(cfg.get("paper", True))
    if a.leg == "stop":
        so = stop_input(order)
        if not so:
            raise TicketError("order %d has no stop-loss order" % a.index)
        if not rec or rec.get("status") != "placed":
            raise TicketError("order %d's buy is not recorded as placed today; the stop-loss goes in only after "
                              "the buy fills" % a.index)
        new = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fp, "leg": "stop",
               "symbol": rec["symbol"], "side": so["transaction_type"].lower(), "plain": leg_words(so),
               "status": a.status, "paper": paper}
        if a.broker_id:
            new["broker_order_id"] = a.broker_id
        if a.note:
            new["note"] = a.note
        state["orders"].append(new)
        write_json(a.state, state)
        print("Recorded order %d's stop-loss as %s%s: %s." % (a.index, a.status, " (paper)" if paper else "",
                                                             leg_words(so)))
        if a.status != "placed":
            print("The shares have no stop at Zerodha. Fix it now (place it again after a new yes) or sell: "
                  "ticket.py flatten --symbol %s." % rec["symbol"])
        return 0
    if not rec or rec.get("status") != "approved":
        raise TicketError("order %d has no yes recorded today; a result can only follow an approval" % a.index)
    new = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fp, "symbol": rec["symbol"],
           "side": rec["side"], "plain": rec["plain"], "status": a.status, "paper": paper}
    for k in ("qty", "kind", "max_loss", "stop"):
        if rec.get(k) is not None:
            new[k] = rec[k]
    if a.broker_id:
        new["broker_order_id"] = a.broker_id
    if a.note:
        new["note"] = a.note
    state["orders"].append(new)
    write_json(a.state, state)
    print("Recorded order %d as %s%s." % (a.index, a.status, " (paper)" if paper else ""))
    if a.status == "placed" and a.filled_qty and a.filled_price:
        apply_fill(a.portfolio, cfg, rec["symbol"], rec["side"], a.filled_qty, a.filled_price, today)
        log_fill(a.state, now, today, rec["symbol"], rec["side"], a.filled_qty, a.filled_price, paper)
        print("Added the fill to %s." % a.portfolio)
    elif a.status == "placed":
        print("When it fills, record it: ticket.py fill --symbol %s --side %s --qty Q --price P"
              % (rec["symbol"], rec["side"]))
    so = stop_input(order)
    if a.status == "placed" and so:
        if paper:
            print("Paper stop-loss: ticket.py result --index %d --leg stop --status placed (nothing is sent)."
                  % a.index)
        elif route_of(cfg) == "manual":
            print("Once the buy is COMPLETE in Kite, place its stop-loss yourself: %s. Then kite.py orders --write "
                  "and ticket.py match --index %d --leg stop." % (leg_words(so), a.index))
        else:
            print("Once the buy shows COMPLETE in get_orders, place the stop-loss: place_order with exactly:")
            print(json.dumps(so, indent=2))
            print("then: ticket.py result --index %d --leg stop --status placed --broker-id ID" % a.index)
        print("When the stop-loss fills, or you exit at the target, record that sell: ticket.py fill.")
    return 0


# ---------------------------------------------------------------- match: Kite's order book -> the log

def kite_book(path, today):
    """Today's orders from the file kite.py orders --write saves."""
    doc = read_json(path, "Kite order book (run kite.py orders --write first)")
    if not isinstance(doc, dict) or not isinstance(doc.get("orders"), list):
        raise TicketError("%s is not an order book kite.py wrote" % path)
    if doc.get("date") != today:
        raise TicketError("%s is from %s, not today: run kite.py orders --write again" % (path, doc.get("date")))
    return doc["orders"]


def kite_time(text):
    """"2026-10-19 10:01:05" (India time) -> naive UTC, or None."""
    try:
        return ist_to_utc(datetime.datetime.strptime(str(text)[:19], "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        return None


def _same_price(got, want):
    g, w = _f(got), _f(want)
    if g is None or w is None:
        return g is None and w is None
    return abs(g - w) <= float(tick_size(w)) / 2 + 1e-9


def is_this_order(row, want):
    """Is Kite's order row the order the ticket asked for? Every field that matters,
    prices within half a tick."""
    for k in ("exchange", "tradingsymbol", "transaction_type", "product", "order_type"):
        if str(row.get(k) or "").upper() != str(want.get(k) or "").upper():
            return False
    if str(row.get("variety") or "regular").lower() != str(want.get("variety") or "regular").lower():
        return False
    if _f(row.get("quantity")) is None or int(_f(row["quantity"])) != int(want.get("quantity") or 0):
        return False
    typ = str(want.get("order_type") or "").upper()
    if typ in ("LIMIT", "SL") and not _same_price(row.get("price"), want.get("price")):
        return False
    if typ in ("SL", "SL-M") and not _same_price(row.get("trigger_price"), want.get("trigger_price")):
        return False
    return True


def outcome(row):
    """(what happened, filled qty, average price): filled, partial, open or failed."""
    st = str(row.get("status") or "").upper()
    q = _f(row.get("filled_quantity")) or 0
    avg = _f(row.get("average_price")) or 0
    if st == KITE_FILLED:
        return "filled", q or _f(row.get("quantity")) or 0, avg
    if st in KITE_DEAD:
        return ("partial" if q > 0 else "failed"), q, avg
    return "open", 0, 0


def fill_logged(state, broker_id):
    return any(r.get("status") == "filled" and r.get("broker_order_id") == str(broker_id)
               for r in state.get("orders", []))


def cmd_match(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    if cfg.get("paper", True):
        raise TicketError("this is a paper account: nothing went to Zerodha, so there is nothing to match. "
                          "Record paper orders with ticket.py result")
    fp = fingerprint(order)
    entry = latest(state, fp, today)
    leg = "stop" if a.leg == "stop" else None
    if leg:
        want = stop_input(order)
        if not want:
            raise TicketError("order %d has no stop-loss order" % a.index)
        if not entry or entry.get("status") != "placed":
            raise TicketError("order %d's buy is not recorded as placed today: match the buy first (ticket.py match "
                              "--index %d); the stop-loss goes in only after it fills" % (a.index, a.index))
        mine = latest(state, fp, today, "stop")
    else:
        want = tool_input(order)
        if not entry or entry.get("status") not in ("approved", "placed"):
            raise TicketError("order %d has no yes recorded today: the yes comes before you place it in Kite"
                              % a.index)
        mine = entry if entry.get("status") == "placed" else None
    yes = None
    for rec in state.get("orders", []):
        if rec.get("fingerprint") == fp and rec_day(rec) == today and rec.get("status") == "approved":
            yes = rec
    yes_at = parse_utc(yes.get("at")) if yes else None
    rows = kite_book(a.kite_orders, today)
    used = {str(r.get("broker_order_id")) for r in state.get("orders", [])
            if r.get("broker_order_id") and r.get("status") in ("placed", "failed")}
    if mine and mine.get("broker_order_id"):
        used.discard(str(mine["broker_order_id"]))
    found, early = [], []
    for row in rows:
        if not is_this_order(row, want):
            continue
        oid = str(row.get("order_id"))
        if mine and mine.get("broker_order_id") and oid != str(mine["broker_order_id"]):
            continue
        if oid in used:
            continue
        t = kite_time(row.get("order_timestamp"))
        if yes_at and t and t < yes_at:
            early.append(row)
            continue
        found.append(row)
    if a.order_id:
        found = [r for r in found if str(r.get("order_id")) == str(a.order_id)]
    what = leg_words(want)
    if not found:
        if early:
            last = max(early, key=lambda r: kite_time(r.get("order_timestamp")))
            print("Not matched: Kite's order %s (%s) was placed at %s, before your yes at %s. An order only "
                  "counts after the yes." % (last.get("order_id"), what,
                                             to_ist(kite_time(last.get("order_timestamp"))).strftime("%H:%M:%S"),
                                             to_ist(yes_at).strftime("%H:%M:%S")))
        else:
            print("No order in Kite's order book is %s%s. Did you place it, and run kite.py orders --write after? "
                  "An order with any other quantity or price is not this ticket." % (
                      what, " (stop-loss)" if leg else ""))
        return 1
    if len(found) > 1:
        print("More than one Kite order is %s: %s. Say which: ticket.py match --index %d%s --order-id ID." % (
            what, ", ".join("%s (%s)" % (r.get("order_id"), r.get("status")) for r in found), a.index,
            " --leg stop" if leg else ""))
        return 1
    row = found[0]
    oid = str(row.get("order_id"))
    happened, qty, avg = outcome(row)
    paper = False
    side = str(want["transaction_type"]).lower()
    sym = str(want["tradingsymbol"]).upper()
    base = {"date": today, "index": a.index, "fingerprint": fp, "symbol": sym, "broker_order_id": oid,
            "via": "kite order book", "paper": paper}
    rec_new = None
    if mine is None or happened == "failed":
        status = "failed" if happened == "failed" else "placed"
        rec_new = dict(base, at=ist_iso(now), status=status)
        if leg:
            rec_new.update(leg="stop", side=side, plain=leg_words(want))
        else:
            rec_new.update(side=entry["side"], plain=entry["plain"])
            for k in ("qty", "kind", "max_loss", "stop"):
                if entry.get(k) is not None:
                    rec_new[k] = entry[k]
        if happened == "failed" and row.get("status_message"):
            rec_new["note"] = str(row["status_message"])
        state["orders"].append(rec_new)
        write_json(a.state, state)
    label = "order %d's stop-loss" % a.index if leg else "order %d" % a.index
    print("Matched Kite order %s: %s, %s." % (oid, str(row.get("status")), what))
    if happened == "failed":
        print("Recorded %s as failed%s." % (label, (" (Kite says: %s)" % row["status_message"])
                                            if row.get("status_message") else ""))
        if leg:
            print("The shares have no stop at Zerodha. Fix it now (place the stop-loss again) or sell: "
                  "ticket.py flatten --symbol %s." % sym)
        else:
            print("A failed order is not retried without a new check and a new yes.")
        return 0
    if rec_new is not None:
        print("Recorded %s as placed (broker id %s)." % (label, oid))
    if happened == "open":
        if rec_new is None:
            print("Still %s at Kite: nothing new to record." % row.get("status"))
        if not leg:
            print("It has not filled yet. Once it is COMPLETE: kite.py orders --write and ticket.py match --index %d "
                  "again; place the stop-loss only then." % a.index)
        return 0
    if fill_logged(state, oid):
        print("Its fill (%s at %s) is already recorded." % (_str_num(qty), _p(avg)))
    else:
        apply_fill(a.portfolio, cfg, sym, side, qty, avg, today)
        log_fill(a.state, now, today, sym, side, qty, avg, paper, oid)
        print("Recorded the fill: %s %s %s at %s, into %s." % (side.upper(), _str_num(qty), sym, _p(avg),
                                                              a.portfolio))
    if happened == "partial":
        print("Kite %s the rest (%s of %s filled)." % (str(row.get("status")).lower(), _str_num(qty),
                                                      _str_num(want.get("quantity"))))
    so = stop_input(order)
    if not leg and so:
        sq = int(qty)
        if sq != int(so["quantity"]):
            print("Only %d shares filled: the stop-loss on the ticket is for %s. Place a stop for the %d you hold "
                  "after a new check (trader-risk) and a new yes." % (sq, _str_num(so["quantity"]), sq))
        else:
            print("Now place its stop-loss yourself in Kite: %s. Then kite.py orders --write and ticket.py match "
                  "--index %d --leg stop." % (leg_words(so), a.index))
    elif leg:
        print("The stop-loss filled: the trade is closed.")
    return 0


def cmd_fill(a):
    cfg = read_json(a.config, "config", required=False) or {}
    now = parse_now(a.now)
    date = a.date or to_ist(now).date().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise TicketError("--date wants a date like 2026-10-19")
    pf = apply_fill(a.portfolio, cfg, a.symbol, a.side, a.qty, a.price, date)
    log_fill(a.state, now, date, nse_symbol(a.symbol), a.side, a.qty, a.price, cfg.get("paper", True))
    print("Recorded %s %s %s at %s on %s in %s. Cash now %s." % (a.side, a.qty, nse_symbol(a.symbol),
                                                                _p(a.price), date, a.portfolio, inr(pf["cash"])))
    return 0


def fresh_price(path, sym, now_utc):
    """(price, quote time) from prices.json if it is under 15 minutes old, else (None, None)."""
    doc = read_json(path, "prices", required=False)
    if not isinstance(doc, dict):
        return None, None
    p = _f((doc.get("prices") or {}).get(sym))
    if not p or p <= 0:
        return None, None
    det = (doc.get("detail") or {}).get(sym) or {}
    t = parse_utc(det.get("time")) or parse_utc(det.get("fetched")) or parse_utc(doc.get("as_of"))
    if t is None:
        return None, None
    age = (now_utc - t).total_seconds()
    return (p, t) if -60 <= age <= FRESH_MINUTES * 60 else (None, None)


def cmd_flatten(a):
    cfg = read_json(a.config, "config", required=False) or {}
    sym = nse_symbol(a.symbol)
    now, today = _clock(a)
    source = "the broker, get_positions"
    if a.qty is not None:
        qty = _f(a.qty)
        if qty is None or qty <= 0 or qty != int(qty):
            raise TicketError("--qty wants the whole number of shares you hold, like --qty 54")
    else:
        pf = read_json(a.portfolio, "portfolio", required=False) or {}
        qty = sum(_f(p.get("shares", p.get("qty"))) or 0 for p in pf.get("positions", [])
                  if str(p.get("ticker") or p.get("symbol") or "").upper() == sym)
        source = a.portfolio
        if qty <= 1e-9:
            raise TicketError("nothing to flatten: no %s shares in %s. Check the broker (get_positions) "
                              "and pass --qty if you do hold some" % (sym, a.portfolio))
    plan = read_json(a.plan, "plan", required=False) or {}
    flat_by = (plan.get("flat_by") if str(plan.get("symbol", "")).upper() == sym else None) \
        or cfg.get("flat_by") or DEFAULT_FLAT_BY
    price, when = None, None
    if a.price is not None:
        price = _f(a.price)
        if price is None or price <= 0:
            raise TicketError("--price wants the last price, like --price 1371.20")
    else:
        price, when = fresh_price(a.prices, sym, now)
    order = {"variety": "regular", "exchange": str(cfg.get("exchange") or "NSE").upper(), "tradingsymbol": sym,
             "transaction_type": "SELL", "quantity": int(qty), "product": str(cfg.get("product") or "MIS").upper()}
    buf = _f(cfg.get("exit_buffer_pct"))
    buf = DEFAULT_EXIT_BUFFER if buf is None else buf
    if price:
        limit = to_tick(D(price) * (1 - D(buf) / 100), ROUND_FLOOR)
        order.update({"order_type": "LIMIT", "price": float(limit)})
    else:
        order["order_type"] = "MARKET"
    order["validity"] = "DAY"
    order["why"] = ("flat by %s IST: closes the %s day trade before the close (qty from %s)"
                    % (flat_by, sym, source))
    order["cancel_first"] = CANCEL_FIRST % sym
    old = read_json(a.orders, "orders", required=False)
    replaced = len(old.get("orders", [])) if isinstance(old, dict) else 0
    doc = {"created_by": "trader-broker", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": "proposed",
           "paper": bool(cfg.get("paper", True)),
           "note": "Flatten ticket. Nothing is placed until trader-risk passes it and you say yes in the chat.",
           "source": "ticket.py flatten %s on %s" % (sym, today), "orders": [order]}
    write_json(a.orders, doc)
    print("Flatten %s: %s (flat by %s IST)." % (sym, plain_parts(order)[0], flat_by))
    if price:
        print("Limit %s: the last price %s%s minus your %s%% exit buffer, rounded down to the tick, so it fills "
              "like a market order but never under %s." % (_p(order["price"]), _p(price),
                                                             (" (%s IST)" % to_ist(when).strftime("%H:%M"))
                                                             if when else "", _num_pct(buf), _p(order["price"])))
    else:
        print("No fresh price for %s (--price, or alerts.py quote %s --write, or Kite get_ltp): a MARKET sell. "
              "Kite's MCP place_order cannot send market protection and Zerodha may reject it; then sell it in "
              "the Kite app and record the fill." % (sym, sym))
    print("Wrote it to %s%s." % (a.orders, (" (it replaced %d earlier proposal%s)"
                                             % (replaced, "" if replaced == 1 else "s")) if replaced else ""))
    if a.qty is None:
        print("The qty comes from %s. Check it against get_positions before you say yes." % source)
    print("Next:")
    print("  1. trader-risk: check.py. A sell that only closes what you hold is never blocked by the daily")
    print("     limits or the entry window.")
    print("  2. ticket.py show, then the user's yes for this one order.")
    if cfg.get("paper", True):
        print("  3. Paper account: nothing is sent. Record the paper fill: ticket.py result --index 1 --status")
        print("     placed --filled-qty %d --filled-price PRICE." % int(qty))
    elif route_of(cfg) == "manual":
        print("  3. The stop-loss SL order still holds these shares at Zerodha. Cancel it yourself in Kite first")
        print("     (Orders: the TRIGGER PENDING sell), then place the sell exactly as the ticket shows, then")
        print("     kite.py orders --write and ticket.py match --index 1. Zerodha's own MIS auto square-off comes")
        print("     later and costs Rs 50 + GST an order.")
    else:
        print("  3. The stop-loss SL order still holds these shares at Zerodha. Cancel it first: get_orders")
        print("     (status TRIGGER PENDING), then cancel_order (variety regular, its order_id; Needs approval).")
        print("     Then place_order with the ticket. Zerodha's own MIS auto square-off comes later and costs")
        print("     Rs 50 + GST an order.")
    return 0


def _num_pct(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.2f" % x).rstrip("0")


def cmd_log(a):
    state = load_state(a.state)
    now, today = _clock(a)
    rows = [r for r in state.get("orders", []) if a.all or rec_day(r) == today]
    if not rows:
        print("No decisions recorded%s." % ("" if a.all else " today"))
        return 1
    for r in rows:
        extra = (" id " + r["broker_order_id"]) if r.get("broker_order_id") else ""
        reply = (' reply "%s"' % r["reply"]) if r.get("reply") and r["status"] in ("approved", "declined") else ""
        loss = (" max loss %s" % inr(r["max_loss"])) if r.get("max_loss") is not None and r["status"] == "placed" else ""
        idx = ("#%s" % r["index"]) if r.get("index") is not None else "  "
        status = ("stop " + r["status"]) if r.get("leg") == "stop" else r.get("status")
        paper = " (paper)" if r.get("paper") and r.get("status") in ("placed", "filled") else ""
        print("%s  %s %-12s %s%s%s%s%s" % (r.get("at"), idx, status, r.get("plain"), reply, loss, extra, paper))
    placed = sum(1 for r in rows if r.get("status") == "placed" and r.get("leg") != "stop")
    print("%d placed%s." % (placed, "" if a.all else " today"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Order tickets and your yes (trader-broker). Never places orders.")
    sub = ap.add_subparsers(dest="cmd")

    def clock(p):
        p.add_argument("--now", help="pretend it is this India time, e.g. \"2026-10-19 10:00\"")
        p.add_argument("--today", help="YYYY-MM-DD (default: today in India)")

    def common(p):
        p.add_argument("--orders", default="trader/orders.json")
        p.add_argument("--config", default="trader/config.json")
        p.add_argument("--risk", default="trader/state/risk.json")
        p.add_argument("--state", default="trader/state/broker.json")
        clock(p)

    p = sub.add_parser("show", help="render the tickets")
    common(p)
    p.add_argument("--index", type=int)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("approve", help="record the user's explicit yes for one order")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply", required=True, help="the user's reply, word for word")
    p = sub.add_parser("decline", help="record a no")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply")
    p = sub.add_parser("result", help="record what happened to an order (placed, denied, failed)")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--leg", choices=["entry", "stop"], default="entry", help="the buy (default) or its stop-loss")
    p.add_argument("--status", required=True, choices=["placed", "denied", "failed"])
    p.add_argument("--broker-id", help="Zerodha's order id")
    p.add_argument("--note")
    p.add_argument("--filled-qty")
    p.add_argument("--filled-price")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p = sub.add_parser("match", help="record the order you placed in Kite, from Kite's order book")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--leg", choices=["entry", "stop"], default="entry", help="the buy (default) or its stop-loss")
    p.add_argument("--order-id", help="Kite's order id, when more than one order matches")
    p.add_argument("--kite-orders", default=KITE_ORDERS, help="the order book kite.py orders --write saved")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p = sub.add_parser("fill", help="record a fill in trader/portfolio.json")
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", required=True, choices=["buy", "sell"])
    p.add_argument("--qty", required=True)
    p.add_argument("--price", required=True)
    p.add_argument("--date", help="YYYY-MM-DD (default: today in India)")
    p.add_argument("--now")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--state", default="trader/state/broker.json")
    p = sub.add_parser("flatten", help="a sell ticket that closes a symbol's position by flat_by")
    p.add_argument("--symbol", required=True)
    p.add_argument("--qty", help="shares you hold, from get_positions (default: trader/portfolio.json)")
    p.add_argument("--price", help="the last price, for a marketable LIMIT (default: a fresh trader/prices.json)")
    p.add_argument("--prices", default="trader/prices.json")
    p.add_argument("--orders", default="trader/orders.json", help="where the ticket is written")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--config", default="trader/config.json")
    clock(p)
    p = sub.add_parser("log", help="today's decisions")
    p.add_argument("--state", default="trader/state/broker.json")
    clock(p)
    p.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"show": cmd_show, "approve": cmd_approve, "decline": cmd_decline, "result": cmd_result,
                "match": cmd_match, "fill": cmd_fill, "flatten": cmd_flatten, "log": cmd_log}[a.cmd](a)
    except TicketError as e:
        print("ticket.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
