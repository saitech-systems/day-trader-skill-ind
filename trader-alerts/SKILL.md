---
name: trader-alerts
description: >-
  Price Alerts. Rings when a price, a stop or the opening bell hits on the
  NSE. Turns the trade plan into three alerts in one go (the stop, the target
  and "flat by close" at your flat-by time, 15:05 by default, before
  Zerodha's own MIS square-off), plus above / below / stop / target levels,
  percent moves from the previous close, any time of day, and the NSE opening
  bell at 9:15, each ringing once so you are not spammed. Knows NSE hours in
  India time with the 2026 holidays, the Budget-day Sunday, Muhurat trading
  and the closing auction, fetches free delayed quotes (Yahoo, SYMBOL.NS) one
  symbol at a time, stores a live Kite get_ltp price when you have one, and
  writes trader/prices.json for sizing and the risk check. Use when the user
  says "alert me when", "tell me if RELIANCE hits 1,400", "watch my stop",
  "watch my trade", "remind me to square off by 3:05", "is the market open",
  "is NSE open today", "is it a holiday", "when does the market open", "get
  me a price", or right after /trader-exit writes a plan.
argument-hint: "[NSE symbol] [price]"
---

# trader-alerts

Price Alerts watches the numbers so the user does not have to stare at a
chart. An alert is information, never an order: when a stop alert rings,
nothing is sold by this skill. Once the entry fills, the stop-loss (SL) order
waits at Zerodha and does the stopping. The target is NOT an order at the
broker (a target order and a stop order for the same shares could both fill
and leave you short), so the target alert is the one that tells the user it
is time to get out, and "flat by close" says it is time to be flat.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock                          # NSE open or closed, next open
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan                # stop, target, flat by from trader/plan.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --opening-bell
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --stop 1357.50
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --target 1375.30
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --at 15:05 --label "flat by close" --symbol RELIANCE
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol TCS --pct 3
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check                          # exit 0 = something rang
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote RELIANCE --write         # -> trader/prices.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote --holdings --write       # portfolio + plan symbols
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote --set RELIANCE=1369.85 --source kite   # a Kite get_ltp price
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
python3 "${CLAUDE_SKILL_DIR}/alerts.py" reset reliance-plan-stop
```

`--now "2026-10-19 15:05"` pins the clock (India time); `--offline DIR`
reads saved quote files instead of the network. Symbols are NSE symbols
(`RELIANCE`, `M&M`, `BAJAJ-AUTO`); `RELIANCE.NS` works too, a BSE `.BO`
listing is refused.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the trade from trader-exit, for `add --from-plan` |
| `trader/config.json` | `flat_by` when the plan has none |
| `trader/alerts.json` | the rules (`alerts.example.json` in this folder) |
| `trader/state/alerts.json` | which rules rang and when, so each rings once |
| `trader/prices.json` | the latest prices, merged in, for trader-budget and trader-risk |
| `trader/portfolio.json` | the symbols held, for `quote --holdings` |

## Rule types

| type | rings when | rings again |
| --- | --- | --- |
| `above` / `target` | price at or above `price` | after `reset`, or on its own if `"rearm": true` and the price fell back |
| `below` / `stop` | price at or below `price` | same |
| `pct_move` | moved `pct` percent from the previous close (or `ref`), optional `direction` up or down | once per day at most |
| `time` | the India clock passed `at` ("15:05") on a trading day | once per trading day |
| `opening_bell` | the NSE is open | once per trading day |

A rule with `"date"` is watched on that India date only; `--from-plan` rules
carry the plan's date, so yesterday's stop never rings today.

## Quotes

- `yahoo` (default): `query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS`,
  an unofficial free JSON endpoint, one request per symbol, half a second
  apart. On a 429 it waits and tries once more; on a 429 or a 5xx after that
  it tries `query1`. It identifies itself as `day-trader-skill-ind/1.0`.
- `kite`: Zerodha's Kite connector (`get_ltp` with `["NSE:RELIANCE"]`, read
  only, on the hosted `mcp.kite.trade` server too) gives a live last price.
  Store it with `quote --set RELIANCE=1369.85 --source kite`, so sizing and
  the risk check use it. It is live only at the moment it was read.
- Free quotes are unofficial, delayed and can lag. They are fine for alerts
  and for checking a size, not for timing an order. The broker's price is the
  one that fills. Each price in `trader/prices.json` keeps its time, and
  trader-risk only uses one under 15 minutes old.

## The NSE clock

Normal market 9:15 a.m. to 3:30 p.m. India time (pre-open 9:00 to 9:08), no
daylight saving, closed on weekends and on these 2026 equity holidays, from
the NSE holiday list (nseindia.com, read 2026-10-23):

- Jan 15 (Maharashtra municipal elections), Jan 26 (Republic Day), Mar 3
  (Holi), Mar 26 (Ram Navami), Mar 31 (Mahavir Jayanti), Apr 3 (Good Friday),
  Apr 14 (Ambedkar Jayanti), May 1 (Maharashtra Day), May 28 (Bakri Id),
  Jun 26 (Muharram), Sep 14 (Ganesh Chaturthi), Oct 2 (Gandhi Jayanti), Oct 20
  (Dussehra), Nov 10 (Diwali Balipratipada), Nov 24 (Guru Nanak Jayanti),
  Dec 25 (Christmas).
- Special sessions on days that are otherwise closed: Sunday Feb 1 (Union
  Budget, normal hours) and Sunday Nov 8 (Muhurat trading, 2:20 to 3:30 p.m.).
  The opening bell and time alerts follow them.
- The closing auction: from 2026-08-03 to 2026-12-06, F&O stocks stop
  continuous trading at 3:15 p.m. (auction to about 3:35); from 2026-12-07
  every stock trades to 3:30 and the auction runs 3:30 to 3:40. The default
  flat-by time, 15:05, is before both, and before Zerodha's own square-off of
  MIS positions (about 3:12 p.m. for F&O stocks until 2026-12-06, 3:25 p.m.
  otherwise, Rs 50 + GST an order).

India time is worked out in the tool (UTC+5:30), so it needs no time zone
files and ignores the computer's own zone. NSE publishes next year's
holidays in December: for 2027 the clock says the holiday list is unknown.

## Steps

1. Right after trader-exit writes the plan, run `add --from-plan`. Confirm
   the three alerts back to the user: the stop, the target and the flat-by time.
2. Run `check` while the trade is open: every few minutes from a Claude
   scheduled task or `/loop`. Each run fetches only the symbols in today's
   rules and rings each alert once. A check reads the latest delayed quote;
   it is not a live feed, and a price can pass a level between two checks.
   With the Kite connector, `get_ltp` then `quote --set ... --source kite`
   gives a fresher price.
3. Tell the user what rang in one line each. A stop alert means the SL order
   has probably filled: check `get_orders` / `get_positions` and record the
   fill with trader-broker. A target alert or "flat by close" means getting
   out is the user's call: trader-broker `flatten` (it cancels the open SL
   order first), then trader-risk, then the user's yes. Never act for them.

## Example (synthetic test data)

The tests use a made-up NSE stock, DEMO, with fixed quotes; the numbers show
the output, not a real market. Monday 2026-10-19: entry 1366.40 at 10:00 AM,
stop 1357.50, target 1375.30, flat by 15:05. `add --from-plan` writes
`demo-plan-stop`, `demo-plan-target` and `demo-flat-by` for that day. A
`check` at 10:53 reads the 10:49:45 quote, 1369.85, between the stop and the
target:

```
NSE OPEN (2026-10-19 10:53 IST, regular session).
Nothing hit. 4 rule(s) watched today, DEMO Rs 1,369.85.
```

At 3:05 PM:

```
ALERT Flat by close: it is 15:05 IST (set for 15:05), the market closes at 15:30. If DEMO is still
open, close it: trader-broker flatten --symbol DEMO builds the sell ticket, then trader-risk and your
yes. Zerodha squares off open MIS positions itself at about 15:25 (15:12 for F&O stocks until
2026-12-06) and charges Rs 50 + GST an order for it.
```

It rings once. The next day, Tuesday 2026-10-20, is Dussehra: the NSE is
closed and the opening bell stays quiet. It rings on Wednesday at 9:15.

## Rules

- An alert is a fact about a price or the clock. Say it as a fact.
- Never present a Yahoo quote as live. A Kite `get_ltp` price is live only
  at the moment it was read.
- Confirm every new rule back to the user: symbol, type, level, day.

## What it never does

It never places, cancels or changes an order, never claims a delayed quote
is live, and never predicts where a price is going.

## Safety

- Education and tooling, not financial advice. Neither this pack nor Claude is a SEBI-registered
  investment adviser or research analyst. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`): nothing reaches Zerodha. Going live is the user's own
  decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the Kite order tools are set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No F&O, options, MTF or margin, short selling or leverage unless the config allows it and the user confirms.
- Kite API keys and secrets live in the Claude Desktop config or environment variables, never in the chat.
