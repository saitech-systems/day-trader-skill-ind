---
name: trader-risk
description: >-
  Risk Manager. Checks every NSE trade against your limits and stops anything
  over them, before it can reach Zerodha. For a day trade: a stop is required
  (the SL stop-loss order), the loss at the stop plus Zerodha's intraday
  charges must fit your max loss per trade (measured from the limit price, or
  the higher of the plan's entry and a fresh price for a MARKET buy),
  reward:risk must meet your minimum, the day's total risk must fit your daily
  loss limit, no more than your trades per day, and no new entries outside
  your entry window in India time or when the NSE is closed. It also keeps the
  basics: blocked symbols, order size, position cap, cash buffer, MIS
  leverage, short sales, F&O, BSE, Kite place_order format (numbers, regular
  orders, MIS, prices on the NSE tick grid) and a stop-loss that matches its
  entry, duplicates and a daily order limit. Getting out is never blocked by
  a day limit. Prints PASS or BLOCK with the reason and records the verdicts
  so trader-broker only shows an order that passed. Use when the user says
  "check my order", "is this safe", "risk check", "does this break my rules",
  "can I take this trade", "how much have I risked today", or before
  /trader-broker, always.
argument-hint: "[path to orders.json]"
---

# trader-risk

The Risk Manager is the gate between a proposal and Zerodha. Every order
written by trader-budget or trader-broker's flatten passes through it. A
BLOCK is final for that order: it does not go to the user for a yes, and it
does not go to the broker. Most people who day trade lose money: SEBI found 7
in 10 individual intraday traders in the cash segment lost money in FY23.
These limits keep each loss and each day's losses small. They do not make a
trade win.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py"                           # trader/ files; writes trader/state/risk.json
python3 "${CLAUDE_SKILL_DIR}/check.py" --json
python3 "${CLAUDE_SKILL_DIR}/check.py" --no-write                # look without recording
python3 "${CLAUDE_SKILL_DIR}/check.py" --now "2026-10-19 10:00"  # pin the clock (India time)
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget, or trader-broker's flatten) |
| `trader/config.json` | the limits (trader-budget ships `config.example.json`) |
| `trader/portfolio.json` | cash and holdings; if missing, a fresh account funded with the budget |
| `trader/prices.json` | a fresh price raises a MARKET buy's entry; also values holdings |
| `trader/state/broker.json` | what was placed today, paper or live: duplicates, trades per day, daily loss |
| `trader/state/risk.json` | the verdicts it writes, with a fingerprint of each exact order |

## Entries and exits

An **entry** opens or adds to a position: a buy, or a sell that would go
short. An **exit** only reduces what you already hold. Getting out must
always be possible, so an exit is checked for format, asset and short sales
only. Every other rule (day limits, entry window, loss, reward:risk, blocked
list, size, duplicates) turns into a warning for an exit, never a BLOCK. An
entry's stop-loss leg (recorded by trader-broker with `--leg stop`) is part of
that entry, not another order.

## The checks

Orders are checked in file order, as if each passed order had filled, so a
batch that is fine order by order but breaks a limit together is caught.

| check | blocks when |
| --- | --- |
| format | no tradingsymbol or exchange; transaction_type not BUY or SELL; quantity not a whole number, or sent as a string (Kite's `place_order` takes numbers); variety other than regular (no cover orders, AMO, iceberg); an unknown order_type, product or validity; a LIMIT without a price, an SL without a trigger_price and price, an SL sell whose limit is above its trigger; a price off the NSE tick grid; NRML on the NSE; a stop_order that is not an SL or SL-M sell of the same symbol, exchange and product, or that sells more than the entry buys |
| asset | futures and options (NFO, or a symbol ending in CE, PE or FUT) while `allow_options` is false; BSE or any exchange but the NSE; anything that is not an NSE equity symbol |
| margin | product MTF while `allow_margin` is false |
| short | a sell of more than you hold (`allow_short` false) |
| blocked | (entries) the symbol is on the `blocked` list |
| size | (entries) under `min_order_inr` or over `max_order_inr` |
| position | (entries) the stock would be over `max_position_pct` of the account |
| cash | (entries) the buy would leave less than the cash buffer, or need MIS leverage |
| stop | (entries) no stop while `require_stop` is true (the stop_order's trigger, or the order's plan stop); a stop on the wrong side of the entry |
| loss | (entries) qty x (entry minus stop) plus the estimated round-trip charges over `max_loss_per_trade_inr` |
| reward:risk | (entries) (target minus entry) / (entry minus stop) under `min_reward_risk` |
| daily loss | (entries) the max losses at the stops of today's entries, plus this one, over `max_daily_loss_inr` |
| trades | (entries) `max_trades_per_day` entries already placed today |
| window | (entries) before `no_entries_before` or after `no_entries_after`, or the NSE is closed |
| duplicate | (entries) the same symbol and side twice in one batch, or already placed today |
| daily limit | (entries) `max_orders_per_day` orders already placed today |

How the numbers work:

- **The entry it measures from**: the limit price for a LIMIT or SL order,
  the trigger for SL-M. For a MARKET order, the HIGHER of the plan's entry and
  a fresh price from `trader/prices.json` (under 15 minutes old). A plan
  written at 10:00 cannot understate the risk at 10:55 after the price ran up.
  An older price is not used, and it says so.
- **Charges** are Zerodha's intraday rates from `charges` in the config
  (brokerage 0.03% or Rs 20 an order, STT 0.025% on the sell, NSE 0.00297%,
  SEBI Rs 10 a crore, stamp 0.003% on the buy, GST 18%). An estimate: the
  contract note is the real number.
- **Daily loss** counts the full max loss of every entry placed today, win or
  lose, as trader-broker logged it. Three full-size trades can use up a day.
- **The window** is India time (UTC+5:30, no daylight saving), worked out in
  the tool, never from the computer's clock zone. Weekends and NSE holidays
  (the 2026 list from nseindia.com) are closed; special sessions such as
  Muhurat trading on Sunday 2026-11-08 (14:20 to 15:30) are open. Set both
  `no_entries_*` to null to switch the window off.
- There is no pattern-day-trader rule in India, so there is no PDT check.

Warnings do not block, but say them to the user: a LIVE account, a MARKET or
SL-M order (Kite's MCP `place_order` cannot send market protection, and
Zerodha may reject it), CNC on a day trade, a stop that lives only in the plan
and not as an order at Zerodha, a stop-loss that covers only part of the
shares, a price too old to use, a special session today, a date outside the
holiday list.

## Steps

1. If the price may have moved since the plan, refresh it first
   (trader-alerts: `alerts.py quote DEMO --write`, or Kite `get_ltp`).
2. Run `check.py`. Exit code 0 means every order passed, 1 means at least one
   was blocked.
3. Tell the user the result in plain words, order by order: the max loss at
   the stop with the charges, reward:risk, and how much of today's limits it
   uses. For a BLOCK, give the reason and the fix (fewer shares, a closer stop
   from trader-exit, wait for the window, or skip the trade). Never change a
   limit yourself.
4. If the order changes in any way, run the check again. trader-broker matches
   each order to its verdict by fingerprint (the Kite fields, the stop-loss
   order and the target), so an edited order is not ready until it passes
   again, and a verdict from yesterday does not count today.

## Example (synthetic test data)

The DEMO order from trader-budget (DEMO is a made-up NSE stock) at 10:00 AM on
Monday 2026-10-19, on a fresh Rs 1,00,000 paper account with the example config:

```
PASS  #1 buy DEMO 54 shares, entry
          entry 1366.40 (the limit price), stop 1357.50, target 1375.30: risk 8.90 a share
          max loss Rs 553.67 incl. about Rs 73.07 charges (limit Rs 1,000), reward:risk 1.00 (min 1)
          today before this: 0 of 3 trades, Rs 0 of Rs 3,000 daily loss

1 passed, 0 blocked at 2026-10-19 10:00 IST. Cash after the passed orders: Rs 26,214.40 (paper account).
```

With a Rs 500 max loss per trade it is blocked:

```
BLOCK #1 buy DEMO 54 shares, entry
        - loss: 54 shares x 8.90 risk a share = Rs 480.60 + about Rs 73.07 charges = Rs 553.67 at the stop,
          over your Rs 500 max loss per trade
```

The same order as a MARKET buy checked at 10:55 with a fresh 1369.85 quote in
`trader/prices.json` is blocked on reward:risk (0.44). At 9:20 it is blocked
by the window (no new entries before 09:30). On Tuesday 2026-10-20 the NSE is
closed (Dussehra). The 15:05 sell that closes the position passes, even though
it is after the 14:45 entry cutoff: it is an exit.

## Rules

- A PASS only means the order is allowed to be shown to the user. It is not a yes.
- Every number comes from the config, the order, the portfolio, the prices or the broker log.
- Free Yahoo prices are unofficial and can lag; Zerodha's price is the one that fills, and an SL
  stop can fill worse than its trigger, or not at all if the price gaps through its limit.

## What it never does

It never places an order, never edits an order to make it pass, never
loosens a limit, never blocks a sell that only gets the user out, and never
treats a PASS as the user's yes.

## Safety

- Education and tooling, not financial advice. Neither this pack nor Claude is a SEBI-registered
  investment adviser or research analyst. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`): nothing reaches Zerodha. Going live is the user's own
  decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and
  then the user places it by hand in Kite, or, on the Kite MCP route, clicks Allow once on Claude's own
  card because the Kite order tools are set to "Needs approval" (Customize > Connectors > tool
  permissions). Never set an order tool to "Always allow".
- No F&O, options, MTF or margin, short selling or leverage unless the config allows it and the user confirms.
- Kite API keys and secrets live in the Claude Desktop config or environment variables, never in the chat.
