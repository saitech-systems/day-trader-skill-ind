#!/usr/bin/env python3
"""Position sizing for trader-budget. Standard library only, no network.

Default mode: sizes ONE NSE day trade from trader/plan.json (written by
trader-exit) to your budget and your max loss per trade, then writes one
proposed order to trader/orders.json in the shape of Zerodha Kite's
place_order: the entry (a LIMIT buy, MIS intraday) plus its stop-loss sell (an
SL order, sent only after the buy fills) and the target, which is watched by
trader-alerts and never sent. It never places anything: trader-risk checks the
order and trader-broker asks you yes or no.

    python3 size.py                                  # trader/plan.json + trader/config.json
    python3 size.py --write                          # write trader/orders.json
    python3 size.py --limit 1366.00 --write          # a limit at 1366.00 instead of the plan's entry
    python3 size.py --market                         # a MARKET buy (see the note below)
    python3 size.py --now "2026-10-19 10:00" --json  # pin the clock (India time)

How many shares. Whole shares only: NSE has no fractional shares.

    shares = the smallest of
      max loss       the most shares whose loss at the stop, plus the estimated
                     round-trip charges, fits max_loss_per_trade_inr
      position cap   budget x max_position_pct / entry
      max order      max_order_inr / entry
      cash           (budget minus the cash buffer) / entry
                     (and no more than the cash in trader/portfolio.json, if you have one)

The entry it sizes at: the LIMIT price (the plan's entry plus entry_buffer_pct,
rounded UP to the NSE tick). With --market, the plan's entry, or the price in
trader/prices.json if that is fresh (under 15 minutes old) and higher, because a
price that ran up since the plan means a bigger loss at the same stop. Kite's
MCP place_order cannot send the market protection Zerodha wants on MARKET and
SL-M API orders, so LIMIT and SL are the defaults.

Charges are Zerodha's intraday rates from "charges" in the config (brokerage
0.03% or Rs 20 an order, STT 0.025% on the sell, NSE transaction charge, SEBI
fee, stamp duty on the buy, GST): an estimate, the contract note is the real
number. Zero shares (the stop is too far away for your max loss) is a plain
refusal: no order is written. So is a reward:risk under min_reward_risk.

Weights mode (kept from v1, no longer the headline): split a budget across a
list of NSE stocks by weight, for a longer-term buy (CNC, delivery).

    python3 size.py --weights RELIANCE=30,HDFCBANK=25,INFY=20,TCS=15,ITC=10
    python3 size.py --targets my-weights.json --budget 100000 --write
    python3 size.py --weights ... --whole-shares --prices trader/prices.json

  1. cash buffer   budget x cash_buffer_pct stays in cash, never invested
  2. weights       renormalised to 100 over the stocks you keep (blocked
                   tickers are dropped first; --top N keeps the N biggest)
  3. cap           no stock gets more than max_position_pct of the budget;
                   anything over the cap is shared out to the others by
                   weight, again and again until nobody is over
  4. min order     a slice under min_order_inr is dropped and its weight
                   shared out
  5. rounding      whole rupees by default (--paise for paise); the rounding
                   rupee goes to the biggest position that is not capped
  6. whole shares  with --whole-shares or "fractional": false; needs prices.
                   Zerodha takes whole shares only, so only whole-share
                   splits become orders.

Money that cannot be invested is reported as "left idle", never hidden.
"""

import argparse
import datetime
import json
import math
import os
import re
import sys
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING, ROUND_HALF_UP

# The India edition's config.example.json. Missing keys fall back to these.
DEFAULTS = {
    "budget": None,
    "cash_buffer_pct": 10,
    "max_position_pct": 75,
    "max_order_inr": 75000,
    "min_order_inr": 100,
    "max_loss_per_trade_inr": 1000,
    "max_daily_loss_inr": 3000,
    "max_trades_per_day": 3,
    "max_orders_per_day": 10,
    "min_reward_risk": 1.0,
    "require_stop": True,
    "no_entries_before": "09:30",
    "no_entries_after": "14:45",
    "flat_by": "15:05",
    "exchange": "NSE",
    "product": "MIS",
    "entry_order_type": "LIMIT",
    "entry_buffer_pct": 0,
    "stop_order_type": "SL",
    "stop_buffer_pct": 0.25,
    "exit_buffer_pct": 0.5,
    "charges": None,
    "fractional": False,
    "broker": "zerodha",
    "paper": True,
    "blocked": [],
    "allow_options": False,
    "allow_margin": False,
    "allow_short": False,
}
EPS = 1e-9
FRESH_MINUTES = 15
SETUP_WORDS = {"orb": "opening-range breakout", "level": "close above a level", "flag": "flag",
               "bounce": "oversold bounce"}


class SizeError(Exception):
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
        raise SizeError('--now wants an India time like "2026-10-19 10:15" (or ...Z for UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_utc(text):
    """"2026-10-19T05:19:45Z" -> naive UTC, or None."""
    if not text:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt


def utc_iso(now_utc=None):
    now_utc = now_utc or utc_now()
    return now_utc.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        raise SizeError("%s is a BSE listing: this pack trades NSE symbols (like RELIANCE)" % text)
    if not NSE_SYMBOL.match(s):
        raise SizeError("%r is not an NSE symbol (like RELIANCE, M&M or BAJAJ-AUTO)" % text)
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


# ---------------------------------------------------------------- inputs

def read_json(path, what):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise SizeError("no %s at %s" % (what, path))
    except json.JSONDecodeError as e:
        raise SizeError("%s is not valid JSON: %s" % (path, e))


def load_config(path):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        cfg.update(read_json(path, "config"))
    return cfg


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_weights(data):
    """Accept a weights file, a {ticker: weight} dict or a list of rows.

    Returns [{"ticker", "name", "weight"}] with weight as a float."""
    if isinstance(data, dict) and "weights" in data:
        data = data["weights"]
    rows = []
    if isinstance(data, dict):
        for t, w in data.items():
            rows.append({"ticker": t, "name": "", "weight": w})
    elif isinstance(data, list):
        for i, r in enumerate(data):
            if not isinstance(r, dict):
                raise SizeError("weights[%d] is not an object" % i)
            t = r.get("ticker") or r.get("symbol")
            w = r.get("weight", r.get("weight_pct", r.get("pct")))
            if not t or w is None:
                raise SizeError("weights[%d] needs a ticker and a weight" % i)
            rows.append({"ticker": t, "name": r.get("name", ""), "weight": w})
    else:
        raise SizeError("weights must be a list or an object")
    out = []
    for r in rows:
        try:
            w = float(r["weight"])
        except (TypeError, ValueError):
            raise SizeError("weight for %s is not a number: %r" % (r["ticker"], r["weight"]))
        out.append({"ticker": nse_symbol(r["ticker"]), "name": r["name"] or "", "weight": w})
    return out


def weights_from_arg(text):
    """RELIANCE=30,HDFCBANK=25 -> {ticker: weight}"""
    out = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise SizeError("--weights wants TICKER=WEIGHT pairs, got %r" % part)
        t, w = part.split("=", 1)
        out[t.strip().upper()] = w.strip()
    return out


def load_prices(path):
    if not path:
        return {}
    data = read_json(path, "prices")
    prices = data.get("prices", data) if isinstance(data, dict) else {}
    out = {}
    for t, p in prices.items():
        try:
            out[str(t).upper()] = float(p)
        except (TypeError, ValueError):
            continue
    return out


def price_time(doc, sym):
    """When this symbol's price is from: the quote's own time (detail[sym].time), else when it
    was fetched (detail[sym].fetched), else the file's as_of."""
    det = ((doc or {}).get("detail") or {}).get(sym) or {}
    for key in ("time", "fetched"):
        t = parse_utc(det.get(key))
        if t:
            return t
    return parse_utc((doc or {}).get("as_of"))


def fresh_price(doc, sym, now_utc, minutes=FRESH_MINUTES):
    """(price, fetched_utc, fresh) for sym from a prices.json doc, or (None, None, False)."""
    if not isinstance(doc, dict):
        return None, None, False
    p = _f((doc.get("prices") or {}).get(sym))
    if p is None or p <= 0:
        return None, None, False
    t = price_time(doc, sym)
    if t is None:
        return p, None, False
    age = (now_utc - t).total_seconds()
    return p, t, -60 <= age <= minutes * 60


# ---------------------------------------------------------------- money helpers

def paise(x):
    """A price in whole paise (366.475 -> 36648, half up)."""
    return int(math.floor(float(x) * 100 + 0.5 + EPS))


def shares_for(rupees, per_share):
    """Whole shares that fit in rupees at per_share, in exact integer paise."""
    u, p = int(round(float(rupees) * 100)), int(round(float(per_share) * 100))
    if p <= 0 or u <= 0:
        return 0
    return u // p


def _price(x):
    """1357.5 -> "1357.50": a price for people."""
    return "%.2f" % (paise(x) / 100.0)


def _num(x):
    """1250 -> "1250", 1118.13 -> "1118.13"."""
    x = round(float(x), 2)
    if abs(x - round(x)) < 0.005:
        return str(int(round(x)))
    return "%.2f" % x


def _pf(x):
    """A Decimal or float price -> a float for the JSON order (1366.4)."""
    return round(float(x), 2) + 0.0


# ---------------------------------------------------------------- plan mode (the default)

def max_loss_shares(budget_loss, entry, stop, rates):
    """The most whole shares whose loss at the stop plus the round-trip charges fits budget_loss."""
    risk_p = paise(entry) - paise(stop)
    if risk_p <= 0 or budget_loss <= 0:
        return 0
    q = int(round(budget_loss * 100)) // risk_p
    while q > 0 and q * risk_p / 100.0 + round_trip_charges(q, entry, stop, rates) > budget_loss + 0.005:
        q -= 1
    return q


def size_trade(plan, cfg, budget=None, limit=None, price=None, cash=None, today=None, market=None):
    """Pure function: one plan + your rules -> the sizing (a dict). qty may be 0.

    limit   your limit price (None = the plan's entry plus entry_buffer_pct)
    market  True for a MARKET buy (None = the config's entry_order_type)
    price   a FRESH latest price; a MARKET buy is sized at the higher of it and the plan's entry
    cash    the cash in trader/portfolio.json, if there is one
    today   India date (YYYY-MM-DD); a plan for another day is refused"""
    c = dict(DEFAULTS)
    c.update(cfg or {})
    rates = dict(CHARGES)
    rates.update(c.get("charges") or {})
    if not isinstance(plan, dict):
        raise SizeError("the plan is not a JSON object")
    if not str(plan.get("symbol") or "").strip():
        raise SizeError("the plan has no symbol (run trader-exit)")
    sym = nse_symbol(plan["symbol"])
    side = str(plan.get("side") or "buy").strip().lower()
    if side != "buy":
        raise SizeError("this pack sizes long (buy) day trades only; the plan says %r" % side)
    if today and plan.get("date") and str(plan["date"]) != today:
        raise SizeError("the plan is for %s and today is %s in India: make a new plan with trader-exit"
                        % (plan["date"], today))
    entry_plan, stop, target = _f(plan.get("entry")), _f(plan.get("stop")), _f(plan.get("target"))
    for name, v in (("entry", entry_plan), ("stop", stop), ("target", target)):
        if v is None or v <= 0:
            raise SizeError("the plan needs a positive %s (run trader-exit); an order needs entry, "
                            "stop and target" % name)
    blocked = {str(b).upper() for b in (c.get("blocked") or [])}
    if sym in blocked:
        raise SizeError("%s is on your blocked list" % sym)

    budget = float(budget if budget is not None else (c.get("budget") or 0))
    if budget <= 0:
        raise SizeError("no budget: pass --budget or set \"budget\" in trader/config.json")
    buf_pct = float(c.get("cash_buffer_pct") or 0)
    if not 0 <= buf_pct < 100:
        raise SizeError("cash_buffer_pct must be between 0 and 100")
    if market is None:
        market = str(c.get("entry_order_type") or "LIMIT").upper() == "MARKET"
    notes = []

    if market:
        order_type = "MARKET"
        if price is not None and paise(price) > paise(entry_plan):
            ref, ref_from = paise(price) / 100.0, "price"
        else:
            ref, ref_from = paise(entry_plan) / 100.0, "plan"
    else:
        order_type = "LIMIT"
        if limit is not None:
            want = D(limit)
            ref_d = to_tick(want, ROUND_CEILING)
            ref_from = "limit"
            if ref_d != want.quantize(Decimal("0.01")):
                notes.append("Your limit %s is not on the %s tick: rounded up to %s." % (
                    _price(limit), tick_size(ref_d), ref_d))
        else:
            buf = D(c.get("entry_buffer_pct") or 0)
            ref_d = to_tick(D(entry_plan) * (1 + buf / 100), ROUND_CEILING)
            ref_from = "plan limit" if buf == 0 else "buffered limit"
        ref = float(ref_d)
        if price is not None and paise(price) > paise(ref):
            notes.append("%s last %s is above your limit %s: a LIMIT buy fills only if the price comes back "
                         "to %s." % (sym, _price(price), _price(ref), _price(ref)))
    stop = float(to_tick(stop, ROUND_FLOOR))
    target = float(to_tick(target, ROUND_HALF_UP))
    if paise(stop) >= paise(ref):
        raise SizeError("the stop %s is not below the entry %s; a long trade needs its stop underneath"
                        % (_price(stop), _price(ref)))
    if paise(target) <= paise(ref):
        raise SizeError("the target %s is not above the entry %s: the move has already happened. "
                        "Skip it or make a new plan with trader-exit" % (_price(target), _price(ref)))
    stop_type = str(c.get("stop_order_type") or "SL").upper()
    stop_limit = None
    if stop_type == "SL":
        stop_limit = float(to_tick(D(stop) * (1 - D(c.get("stop_buffer_pct") or 0) / 100), ROUND_FLOOR))
    risk_p = paise(ref) - paise(stop)
    reward_p = paise(target) - paise(ref)
    risk = risk_p / 100.0

    cash_buffer = round(budget * buf_pct / 100.0, 2)
    available = budget - cash_buffer
    cash_note = "budget %s minus the %s cash buffer" % (inr(budget), inr(cash_buffer))
    if cash is not None and cash - cash_buffer < available - 0.005:
        available = cash - cash_buffer
        cash_note = "cash %s in trader/portfolio.json minus the %s buffer" % (inr(cash), inr(cash_buffer))
    available = max(round(available, 2), 0.0)

    limits = []
    max_loss = _f(c.get("max_loss_per_trade_inr"))
    if max_loss:
        limits.append({"name": "max loss", "inr": max_loss, "per": risk,
                       "shares": max_loss_shares(max_loss, ref, stop, rates),
                       "how": "%s max loss / (%s risk a share + charges)" % (inr(max_loss), _price(risk))})
    cap_pct = _f(c.get("max_position_pct"))
    if cap_pct:
        cap = round(budget * cap_pct / 100.0, 2)
        limits.append({"name": "position cap", "inr": cap, "per": ref, "shares": shares_for(cap, ref),
                       "how": "%s%% of %s = %s / %s" % (_num(cap_pct), inr(budget), inr(cap), _price(ref))})
    max_order = _f(c.get("max_order_inr"))
    if max_order:
        limits.append({"name": "max order", "inr": max_order, "per": ref, "shares": shares_for(max_order, ref),
                       "how": "%s / %s" % (inr(max_order), _price(ref))})
    limits.append({"name": "cash", "inr": available, "per": ref, "shares": shares_for(available, ref),
                   "how": "%s = %s / %s" % (cash_note, inr(available), _price(ref))})

    qty = max(0, min(l["shares"] for l in limits))
    bound = [l["name"] for l in limits if l["shares"] == qty]
    for l in limits:
        l["binds"] = l["name"] == bound[0]
    cost = round(qty * paise(ref) / 100.0, 2)
    gross = round(qty * risk_p / 100.0, 2)
    charges = round_trip_charges(qty, ref, stop, rates) if qty else 0.0
    loss = round(gross + charges, 2)
    refusal = None
    if qty == 0:
        if bound[0] == "max loss":
            one = round(risk + round_trip_charges(1, ref, stop, rates), 2)
            refusal = ("one share risks %s at the stop with charges (entry %s minus stop %s, plus about %s "
                       "charges), more than your %s max loss per trade. No order. Skip this trade, or plan a "
                       "closer stop with trader-exit."
                       % (inr(one), _price(ref), _price(stop), inr(round(one - risk, 2)), inr(max_loss)))
        else:
            refusal = ("one share of %s costs %s, more than your %s limit (%s). No order."
                       % (sym, inr(ref), bound[0], limits[[l["name"] for l in limits].index(bound[0])]["how"]))
    elif _f(c.get("min_reward_risk")) and reward_p < _f(c["min_reward_risk"]) * risk_p - EPS:
        refusal = ("reward:risk %.2f (target %s, entry %s, stop %s) is under your minimum %s, so trader-risk "
                   "would block it. No order. Skip it, or make a new plan with trader-exit."
                   % (reward_p / float(risk_p), _price(target), _price(ref), _price(stop),
                      _num(c["min_reward_risk"])))
    elif cost < float(c.get("min_order_inr") or 0) - 0.005:
        refusal = ("%d share%s of %s is %s, under your %s minimum order. No order."
                   % (qty, "" if qty == 1 else "s", sym, inr(cost), inr(c["min_order_inr"])))
    return {
        "symbol": sym, "side": "buy", "setup": plan.get("setup", ""), "date": plan.get("date"),
        "entry_time": plan.get("entry_time", ""), "entry_plan": paise(entry_plan) / 100.0,
        "entry": ref, "entry_from": ref_from, "order_type": order_type, "stop": stop, "stop_type": stop_type,
        "stop_limit": stop_limit, "target": target, "risk_per_share": risk,
        "reward_risk": round(reward_p / float(risk_p), 2),
        "stop_label": plan.get("stop_label", ""), "target_label": plan.get("target_label", ""),
        "budget": budget, "cash_buffer_pct": buf_pct, "cash_buffer": cash_buffer, "cash_available": available,
        "max_loss_per_trade_inr": max_loss, "limits": limits, "qty": qty, "bound_by": bound[0],
        "cost": cost, "loss_at_stop": gross, "charges": charges, "max_loss": loss,
        "flat_by": plan.get("flat_by") or c.get("flat_by"),
        "exchange": str(c.get("exchange") or "NSE").upper(), "product": str(c.get("product") or "MIS").upper(),
        "refusal": refusal, "notes": notes,
    }


def kite_order(s):
    """The one proposed order: Kite place_order fields (numbers), its stop-loss order, the target, why and plan."""
    if s["refusal"] or s["qty"] <= 0:
        return None
    q = int(s["qty"])
    o = {"variety": "regular", "exchange": s["exchange"], "tradingsymbol": s["symbol"], "transaction_type": "BUY",
         "quantity": q, "product": s["product"], "order_type": s["order_type"]}
    if s["order_type"] == "LIMIT":
        o["price"] = _pf(s["entry"])
    o["validity"] = "DAY"
    stop = {"variety": "regular", "exchange": s["exchange"], "tradingsymbol": s["symbol"],
            "transaction_type": "SELL", "quantity": q, "product": s["product"], "order_type": s["stop_type"],
            "trigger_price": _pf(s["stop"])}
    if s["stop_type"] == "SL":
        stop["price"] = _pf(s["stop_limit"])
    stop["validity"] = "DAY"
    setup = SETUP_WORDS.get(s["setup"], s["setup"] or "day trade")
    bound = {"max loss": "%s max loss per trade" % inr(s["max_loss_per_trade_inr"] or 0),
             "position cap": "position cap", "max order": "max order",
             "cash": "cash after the buffer"}[s["bound_by"]]
    why = ("%s on %s: %d shares, bound by your %s (%d x %s = %s at the stop, plus about %s charges = %s)"
           % (setup, s["symbol"], q, bound, q, _price(s["risk_per_share"]), inr(s["loss_at_stop"]),
              inr(s["charges"]), inr(s["max_loss"])))
    o.update({
        "stop_order": stop,
        "target_price": _pf(s["target"]),
        "why": why,
        "plan": {"entry": _pf(s["entry"]), "stop": _pf(s["stop"]), "stop_limit": s["stop_limit"],
                 "target": _pf(s["target"]), "risk_per_share": s["risk_per_share"], "charges": s["charges"],
                 "max_loss": s["max_loss"], "setup": s["setup"], "entry_time": s["entry_time"],
                 "flat_by": s["flat_by"]},
    })
    return o


def plan_report(s, order, notes=()):
    lines = []
    setup = SETUP_WORDS.get(s["setup"], s["setup"] or "setup")
    lines.append("%s buy, %s, entry %s%s" % (s["symbol"], setup, _price(s["entry_plan"]),
                                            (" at " + s["entry_time"]) if s["entry_time"] else ""))
    lines.append("Stop %s%s. Target %s%s." % (_price(s["stop"]), (": " + s["stop_label"]) if s["stop_label"] else "",
                                             _price(s["target"]),
                                             (": " + s["target_label"]) if s["target_label"] else ""))
    for n in list(notes) + list(s.get("notes") or []):
        lines.append(n)
    src = {"plan": "the plan's entry, a MARKET buy", "price": "the latest price, above the plan's entry",
           "limit": "your limit price", "plan limit": "a LIMIT buy at the plan's entry",
           "buffered limit": "a LIMIT buy at the plan's entry plus your entry buffer"}[s["entry_from"]]
    lines.append("Sized at %s (%s). Risk %s a share (entry %s minus stop %s). Reward:risk %.2f."
                 % (_price(s["entry"]), src, _price(s["risk_per_share"]), _price(s["entry"]), _price(s["stop"]),
                    s["reward_risk"]))
    lines.append("")
    lines.append("Limits (whole shares; the smallest one wins):")
    for l in s["limits"]:
        lines.append("  %-13s %-72s %3d shares%s" % (l["name"], l["how"], l["shares"],
                                                      "  <- binds" if l["binds"] else ""))
    lines.append("")
    if order is None:
        lines.append("No order: " + s["refusal"])
        lines.append("Education and tooling, not financial advice.")
        return "\n".join(lines)
    lines.append("Size: %d shares of %s = %s at %s. Max loss at the stop: %d x %s = %s, plus about %s charges = %s."
                 % (s["qty"], s["symbol"], inr(s["cost"]), _price(s["entry"]), s["qty"],
                    _price(s["risk_per_share"]), inr(s["loss_at_stop"]), inr(s["charges"]), inr(s["max_loss"])))
    lines.append("Charges are Zerodha's intraday rates, an estimate: they come off a win and add to a loss.")
    if s["stop_type"] == "SL":
        lines.append("A stop is an order, not a promise: the SL sell triggers at %s with a limit of %s. In a fast "
                     "fall the price can gap through %s and the stop does not fill at all; then you sell by hand."
                     % (_price(s["stop"]), _price(s["stop_limit"]), _price(s["stop_limit"])))
    else:
        lines.append("A stop is an order, not a promise: in a fast market it can fill below %s." % _price(s["stop"]))
    lines.append("")
    lines.append("Proposed order (Kite place_order arguments, nothing has been placed):")
    how = "LIMIT %s" % _price(order["price"]) if order["order_type"] == "LIMIT" else "MARKET"
    lines.append("  1. BUY %d %s, %s, %s intraday, %s, day" % (order["quantity"], order["tradingsymbol"], how,
                                                               order["product"], order["exchange"]))
    so = order["stop_order"]
    leg = ("SL trigger %s limit %s" % (_price(so["trigger_price"]), _price(so["price"])) if so["order_type"] == "SL"
           else "SL-M trigger %s" % _price(so["trigger_price"]))
    lines.append("  2. after it fills: SELL %d %s, %s, %s, day" % (so["quantity"], so["tradingsymbol"], leg,
                                                                    so["product"]))
    lines.append("  target %s is watched by trader-alerts, not sent to Zerodha" % _price(order["target_price"]))
    if order["order_type"] == "MARKET" or so["order_type"] == "SL-M":
        lines.append("Note: Kite's MCP place_order cannot send market protection; Zerodha may reject a MARKET or "
                     "SL-M API order. LIMIT and SL are the defaults for that reason.")
    if s["flat_by"]:
        lines.append("Flat by %s IST: anything still open then gets closed (trader-alerts rings, trader-broker "
                     "flatten). Zerodha's own MIS auto square-off comes later and costs Rs 50 + GST an order."
                     % s["flat_by"])
    lines.append("")
    lines.append("Next: trader-risk checks it, then trader-broker asks you yes or no.")
    lines.append("Education and tooling, not financial advice.")
    return "\n".join(lines)


# ---------------------------------------------------------------- weights mode (v1)

def normalise(rows, blocked=(), top=None):
    """Drop blocked and non-positive weights, keep the top N, renormalise to 100."""
    blocked = {str(b).upper() for b in (blocked or [])}
    dropped, keep, seen = [], [], set()
    for r in rows:
        if r["ticker"] in seen:
            raise SizeError("%s is listed twice in the weights" % r["ticker"])
        seen.add(r["ticker"])
        if r["ticker"] in blocked:
            dropped.append({"ticker": r["ticker"], "reason": "on your blocked list"})
        elif r["weight"] <= 0:
            dropped.append({"ticker": r["ticker"], "reason": "weight is zero or negative"})
        else:
            keep.append(dict(r))
    keep.sort(key=lambda r: -r["weight"])
    if top:
        for r in keep[top:]:
            dropped.append({"ticker": r["ticker"], "reason": "outside the top %d" % top})
        keep = keep[:top]
    total = sum(r["weight"] for r in keep)
    for r in keep:
        r["source_weight"] = r["weight"]
        r["weight"] = 100.0 * r["weight"] / total if total else 0.0
    return keep, dropped


def allocate(rows, investable, cap):
    """Exact (unrounded) rupees per ticker with the cap enforced.

    Proportional to weight; any stock over the cap is pinned at the cap and
    the rest of the money is shared over the others, repeated until no stock
    is over. Returns ({ticker: rupees}, set of capped tickers)."""
    alloc, capped = {}, set()
    active = [r for r in rows]
    money_left = investable
    while active:
        wsum = sum(r["weight"] for r in active)
        trial = {r["ticker"]: money_left * r["weight"] / wsum for r in active}
        over = [r for r in active if trial[r["ticker"]] > cap + EPS]
        if not over:
            alloc.update(trial)
            break
        for r in over:
            alloc[r["ticker"]] = cap
            capped.add(r["ticker"])
            money_left -= cap
        active = [r for r in active if r["ticker"] not in capped]
        if money_left <= EPS:
            for r in active:
                alloc[r["ticker"]] = 0.0
            break
    return alloc, capped


def round_to_unit(exact, capped, order, cap, unit):
    """Round to whole rupees (unit 1) or paise (unit 0.01) and keep the total.

    The total placed is the exact total rounded DOWN to the unit, so it never
    spends money you do not have. Each slice is rounded to the nearest unit,
    then the difference (usually one unit) goes to the biggest stock that is
    not capped, then the next, never pushing anyone over the cap."""
    def units(x):
        return int(math.floor(x / unit + EPS))

    cap_u = units(cap)
    total_u = units(sum(exact.values()))
    out = {}
    for t, v in exact.items():
        out[t] = cap_u if t in capped else int(math.floor(v / unit + 0.5 + EPS))
    diff = total_u - sum(out.values())
    takers = [t for t in order if t not in capped]
    guard = 0
    while diff != 0 and takers and guard < 100000:
        moved = False
        for t in takers:
            if diff == 0:
                break
            if diff > 0 and out[t] + 1 <= cap_u:
                out[t] += 1
                diff -= 1
                moved = True
            elif diff < 0 and out[t] > 0:
                out[t] -= 1
                diff += 1
                moved = True
        if not moved:
            break
        guard += 1
    return {t: round(u * unit, 2) for t, u in out.items()}


def whole_shares(rupees, prices, order, cap, cash):
    """Whole shares: floor each slice, then buy one more share at a time for the
    stock furthest below its slice while cash lasts and the cap holds."""
    shares = {}
    for t in order:
        shares[t] = int(math.floor(rupees[t] / prices[t] + EPS)) if rupees[t] > 0 else 0
    spent = sum(shares[t] * prices[t] for t in order)
    left = cash - spent
    while True:
        best, gap = None, None
        for t in order:
            p = prices[t]
            if p > left + EPS or (shares[t] + 1) * p > cap + EPS:
                continue
            g = rupees[t] - shares[t] * p
            if best is None or g > gap + EPS:
                best, gap = t, g
        if best is None:
            break
        shares[best] += 1
        left -= prices[best]
    return shares


def size(rows, cfg, budget=None, prices=None, whole=None, paise_mode=False, top=None):
    """Weights mode. Pure function: weights + rules -> the full sizing result (a dict)."""
    budget = float(budget if budget is not None else (cfg.get("budget") or 0))
    if budget <= 0:
        raise SizeError("no budget: pass --budget or set \"budget\" in trader/config.json")
    buf_pct = float(cfg.get("cash_buffer_pct", 10))
    cap_pct = float(cfg.get("max_position_pct", 25))
    min_order = float(cfg.get("min_order_inr", 100) or 0)
    if not 0 <= buf_pct < 100:
        raise SizeError("cash_buffer_pct must be between 0 and 100")
    if not 0 < cap_pct <= 100:
        raise SizeError("max_position_pct must be above 0 and at most 100")
    if whole is None:
        whole = not cfg.get("fractional", False)
    prices = prices or {}

    keep, dropped = normalise(rows, cfg.get("blocked"), top)
    if not keep:
        raise SizeError("nothing to size: no weights left after the blocked list")

    cash_buffer = round(budget * buf_pct / 100.0, 2)
    investable = round(budget - cash_buffer, 2)
    cap = round(budget * cap_pct / 100.0, 2)
    unit = 0.01 if (paise_mode or whole) else 1.0

    if whole:
        missing = [r["ticker"] for r in keep if not prices.get(r["ticker"])]
        if missing:
            raise SizeError("whole shares need a price for: %s (add them to trader/prices.json, "
                            "e.g. with trader-alerts: alerts.py quote %s --write)"
                            % (", ".join(missing), " ".join(missing)))

    while True:
        live = [dict(r) for r in keep]
        tot = sum(r["source_weight"] for r in live)
        for r in live:
            r["weight"] = 100.0 * r["source_weight"] / tot
        exact, capped = allocate(live, investable, cap)
        order = [r["ticker"] for r in sorted(live, key=lambda r: -exact[r["ticker"]])]
        rupees = round_to_unit(exact, capped, order, cap, unit)
        shares = None
        if whole:
            shares = whole_shares(rupees, prices, order, cap, sum(rupees.values()))
            rupees = {t: round(shares[t] * prices[t], 2) for t in order}
        small = [r for r in live if rupees[r["ticker"]] < min_order - EPS]
        if not small:
            break
        worst = min(small, key=lambda r: (r["source_weight"], r["ticker"]))
        if whole and shares is not None and shares[worst["ticker"]] == 0 and prices.get(worst["ticker"], 0) > 0:
            reason = ("one share costs Rs %s, more than its slice; it cannot be bought in whole shares"
                      % _num(prices[worst["ticker"]]))
        else:
            reason = "its slice (Rs %s) is under your Rs %s minimum order" % (
                _num(rupees[worst["ticker"]]), _num(min_order))
        dropped.append({"ticker": worst["ticker"], "reason": reason})
        keep = [r for r in keep if r["ticker"] != worst["ticker"]]
        if not keep:
            raise SizeError("every slice is under your minimum order; raise the budget or hold fewer stocks")

    positions = []
    for r in sorted(live, key=lambda r: -r["weight"]):
        t = r["ticker"]
        p = {"ticker": t, "name": r["name"], "weight": round(r["weight"], 2),
             "source_weight": r["source_weight"], "rupees": rupees[t],
             "capped": t in capped}
        if prices.get(t):
            p["price"] = prices[t]
            p["shares"] = shares[t] if whole else round(rupees[t] / prices[t], 4)
        positions.append(p)
    invested = round(sum(p["rupees"] for p in positions), 2)
    return {
        "budget": budget, "cash_buffer_pct": buf_pct, "cash_buffer": cash_buffer,
        "investable": investable, "max_position_pct": cap_pct, "cap": cap,
        "min_order_inr": min_order, "whole_shares": bool(whole),
        "positions": positions, "invested": invested,
        "idle": round(investable - invested, 2), "dropped": dropped,
    }


def orders_from(result, source=""):
    """Weights mode: proposed buys, one per position, Kite place_order shape (CNC delivery,
    a LIMIT at the price in trader/prices.json). Only whole-share splits become orders."""
    out = []
    if not result["whole_shares"]:
        return out
    for p in result["positions"]:
        if not p.get("shares"):
            continue
        why = "%.2f%% of the split" % p["weight"]
        if source:
            why = "%s, weights from %s" % (why, source)
        if p["capped"]:
            why += "; capped at %s%% of the %s budget" % (_num(result["max_position_pct"]), inr(result["budget"]))
        out.append({"variety": "regular", "exchange": "NSE", "tradingsymbol": p["ticker"], "transaction_type": "BUY",
                    "quantity": int(p["shares"]), "product": "CNC", "order_type": "LIMIT",
                    "price": _pf(to_tick(p["price"], ROUND_CEILING)), "validity": "DAY", "why": why})
    return out


def report(result, orders, source=""):
    """Weights mode table."""
    r = result
    lines = []
    if source:
        lines.append("Weights from: %s" % source)
    lines.append("Budget %s, cash buffer %s%% = %s, investing %s, cap %s%% = %s per stock"
                 % (inr(r["budget"]), _num(r["cash_buffer_pct"]), inr(r["cash_buffer"]),
                    inr(r["investable"]), _num(r["max_position_pct"]), inr(r["cap"])))
    lines.append("")
    has_price = any("price" in p for p in r["positions"])
    head = "%-11s %-24s %8s %15s" % ("symbol", "name", "weight", "rupees")
    if has_price:
        head += " %12s %10s" % ("price", "shares")
    lines.append(head + "  note")
    for p in r["positions"]:
        row = "%-11s %-24s %7.2f%% %15s" % (p["ticker"], (p["name"] or "")[:24], p["weight"], inr(p["rupees"]))
        if has_price:
            row += " %12s %10s" % (_price(p["price"]) if "price" in p else "", p.get("shares", ""))
        if p["capped"]:
            row += "  capped at %s%%" % _num(r["max_position_pct"])
        lines.append(row)
    lines.append("%-11s %-24s %8s %15s" % ("", "invested", "", inr(r["invested"])))
    lines.append("%-11s %-24s %8s %15s" % ("", "cash buffer", "", inr(r["cash_buffer"])))
    lines.append("%-11s %-24s %8s %15s" % ("", "left idle", "", inr(r["idle"])))
    if r["idle"] >= 1:
        lines.append("")
        lines.append("Left idle %s: %s" % (inr(r["idle"]), (
            "whole shares do not divide evenly" if r["whole_shares"]
            else "every stock is at its cap; add stocks or raise max_position_pct")))
    for d in r["dropped"]:
        lines.append("Dropped %s: %s" % (d["ticker"], d["reason"]))
    lines.append("")
    if not r["whole_shares"]:
        lines.append("No orders: Zerodha takes whole shares only. Add prices (--whole-shares --prices "
                     "trader/prices.json) to turn this split into orders.")
    else:
        lines.append("Proposed orders (Kite place_order arguments, CNC delivery, nothing has been placed):")
        for o in orders:
            lines.append("  BUY %d %s, LIMIT %s, %s, %s" % (o["quantity"], o["tradingsymbol"], _price(o["price"]),
                                                           o["product"], o["validity"]))
        lines.append("")
        lines.append("These buys have no stop. trader-risk blocks them while \"require_stop\" is true and applies "
                     "your day-trade limits to every buy.")
        lines.append("Next: trader-risk checks every order, then trader-broker asks you yes or no for each one.")
    lines.append("The weights are yours: this split is arithmetic, not a recommendation. "
                 "Education and tooling, not financial advice.")
    return "\n".join(lines)


# ---------------------------------------------------------------- orders file

def write_orders(path, orders, cfg, source="", now_utc=None):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    doc = {
        "created_by": "trader-budget",
        "created_at": utc_iso(now_utc),
        "status": "proposed",
        "paper": bool(cfg.get("paper", True)),
        "note": ("Proposals only. Nothing is placed until trader-risk passes each order "
                 "and you say yes to it in the chat."),
        "source": source,
        "orders": orders,
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------- main

def run_plan(a, cfg):
    now = parse_now(a.now)
    today = to_ist(now).date().isoformat()
    plan = read_json(a.plan, "plan (run trader-exit first, or use --weights for a split)")
    limit = None
    if a.limit is not None:
        limit = _f(a.limit)
        if limit is None or limit <= 0:
            raise SizeError("--limit wants a price, like --limit 1366.00")
    market = True if a.market else None
    sym = str((plan or {}).get("symbol") or "").upper()
    notes, price = [], None
    ppath = a.prices or ("trader/prices.json" if os.path.exists("trader/prices.json") else None)
    if ppath and sym:
        pdoc = read_json(ppath, "prices")
        p, t, fresh = fresh_price(pdoc, sym, now)
        is_market = a.market or str(cfg.get("entry_order_type") or "LIMIT").upper() == "MARKET"
        if p is not None and fresh:
            price = p
            if is_market and paise(p) > paise(_f(plan.get("entry")) or 0):
                notes.append("%s last %s (as of %s IST) is above the plan's entry %s, so the risk "
                             "is bigger: sized at %s." % (sym, _price(p), to_ist(t).strftime("%H:%M"),
                                                          _price(plan.get("entry")), _price(p)))
        elif p is not None and is_market:
            notes.append("%s in %s is %s: not used (older than %d minutes). Sized at the plan's entry."
                         % (sym, ppath, ("from " + to_ist(t).strftime("%Y-%m-%d %H:%M") + " IST")
                            if t else "undated", FRESH_MINUTES))
    cash = None
    if a.portfolio and os.path.exists(a.portfolio):
        pf = read_json(a.portfolio, "portfolio")
        cash = _f(pf.get("cash")) if isinstance(pf, dict) else None
    s = size_trade(plan, cfg, a.budget, limit, price, cash, today, market)
    order = kite_order(s)
    if order and a.write:
        src = "trader/plan.json (%s %s %s)" % (s["symbol"], s["setup"] or "plan", s["date"] or today)
        write_orders(a.write, [order], cfg, src, now)
    if a.json:
        print(json.dumps({"sizing": s, "orders": [order] if order else [], "notes": notes}, indent=2))
    else:
        print(plan_report(s, order, notes))
        if order and a.write:
            print("Wrote 1 proposed order (entry + stop-loss) to %s" % a.write)
    return 0 if order else 1


def run_weights(a, cfg):
    source = ""
    if a.weights:
        rows = parse_weights(weights_from_arg(a.weights))
    else:
        data = read_json(a.targets, "weights file")
        rows = parse_weights(data)
        if isinstance(data, dict):
            source = data.get("source", "")
    prices = load_prices(a.prices)
    if not prices and os.path.exists("trader/prices.json") and not a.prices:
        prices = load_prices("trader/prices.json")
    whole = True if a.whole_shares else None
    result = size(rows, cfg, a.budget, prices, whole, a.paise, a.top)
    orders = orders_from(result, source)
    if a.write and orders:
        write_orders(a.write, orders, cfg, source, parse_now(a.now))
    if a.json:
        print(json.dumps({"result": result, "orders": orders}, indent=2))
    else:
        print(report(result, orders, source))
        if a.write and orders:
            print("Wrote %d proposed orders to %s" % (len(orders), a.write))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Size an NSE day trade to your budget and max loss (trader-budget).")
    ap.add_argument("--plan", default="trader/plan.json", help="the trade plan (default trader/plan.json)")
    ap.add_argument("--config", default="trader/config.json", help="rules (default trader/config.json)")
    ap.add_argument("--budget", type=float, help="override the budget in the config")
    ap.add_argument("--limit", metavar="PRICE", help="your limit price instead of the plan's entry")
    ap.add_argument("--market", action="store_true", help="a MARKET buy instead of a LIMIT (see --help text)")
    ap.add_argument("--prices", help="prices JSON (default trader/prices.json if it exists)")
    ap.add_argument("--portfolio", default="trader/portfolio.json",
                    help="your account; its cash caps the size if it exists")
    ap.add_argument("--now", help="pretend it is this India time, e.g. \"2026-10-19 10:00\"")
    ap.add_argument("--write", nargs="?", const="trader/orders.json", metavar="PATH",
                    help="write the proposed order (default trader/orders.json)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of the report")
    g = ap.add_argument_group("weights mode (split a budget across a list)")
    g.add_argument("--weights", help="inline weights: RELIANCE=30,HDFCBANK=25")
    g.add_argument("--targets", help="a weights file: {\"weights\": [{\"ticker\", \"weight\"}]}")
    g.add_argument("--top", type=int, help="keep only the N biggest weights")
    g.add_argument("--whole-shares", action="store_true", help="whole shares only (needs --prices)")
    g.add_argument("--paise", action="store_true", help="size to the paisa instead of whole rupees")
    a = ap.parse_args(argv)
    try:
        cfg = load_config(a.config)
        if a.weights or a.targets:
            return run_weights(a, cfg)
        if a.top or a.whole_shares or a.paise:
            raise SizeError("--top, --whole-shares and --paise work with --weights or --targets")
        if a.market and a.limit is not None:
            raise SizeError("use --limit PRICE or --market, not both")
        return run_plan(a, cfg)
    except SizeError as e:
        print("size.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
