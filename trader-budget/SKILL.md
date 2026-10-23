---
name: trader-budget
description: >-
  Budget Manager. Sizes every NSE position to your budget and your max loss per
  trade, in rupees. Reads the trade plan from trader-exit (entry, stop,
  target), finds the whole shares that fit inside four limits (max loss per
  trade including Zerodha's intraday charges, position cap, max order, cash
  after your buffer), says which limit decided it, and writes ONE proposed
  order to trader/orders.json in the exact shape of Zerodha Kite's
  place_order: a LIMIT buy (MIS intraday), its SL stop-loss sell for after the
  fill, and the target to watch. A stop too far away for your max loss is a
  plain refusal, not a smaller order. Nothing is placed. Use when the user
  says "how many shares", "size this trade", "kitne shares lun", "how much
  should I buy", "I have 1 lakh", "I only want to risk 1,000 a trade", "set my
  rules", "keep 10% in cash", or after /trader-exit writes a plan.
argument-hint: "[budget in rupees]"
---

# trader-budget

The Budget Manager turns a plan ("buy DEMO at 1366.40, stop 1357.50") into an
exact order ("buy 54 shares"), inside rules the user sets once. The stop
decides the size: the further away the stop, the fewer shares. Charges count:
on an intraday trade Zerodha's brokerage, STT, exchange fees, stamp duty and
GST come off a win and add to a loss, so the max loss includes them. It writes
a proposal only. trader-risk checks it and trader-broker asks the user yes or
no. SEBI found that 7 in 10 individual intraday traders in the cash segment
lost money (FY23). Sizing to a max loss keeps each loss small; it does not
make a trade win.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py"                        # trader/plan.json + trader/config.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --write                # write trader/orders.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit 1366.00 --write   # your limit instead of the plan's entry
python3 "${CLAUDE_SKILL_DIR}/size.py" --market --json        # a MARKET buy (read the note first)
python3 "${CLAUDE_SKILL_DIR}/size.py" --weights RELIANCE=30,ITC=10 --config split.json   # weights mode, below
```

`--now "2026-10-19 10:00"` pins the clock (India time). A plan for another
day is refused: make a new one with trader-exit.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/config.json` | the rules (start from `config.example.json` in this folder) |
| `trader/plan.json` | the trade: entry, stop, target, setup, flat-by time (trader-exit writes it) |
| `trader/prices.json` | optional: a fresh price (trader-alerts, or Kite `get_ltp`) |
| `trader/portfolio.json` | optional: its cash caps the size (trader-broker keeps it) |
| `trader/orders.json` | the one proposed order it writes with `--write` |

## Step 1: set the rules once

If `trader/config.json` does not exist, copy `config.example.json` into
`trader/config.json` and ask the user for each value in plain words:

- **budget**: how many rupees this is for. Only money they can afford to lose.
- **cash_buffer_pct**: how much always stays in cash (default 10).
- **max_loss_per_trade_inr**: the most one trade may lose if the stop fills at the stop, charges
  included (default Rs 1,000).
- **max_position_pct**, **max_order_inr**, **min_order_inr**: the biggest position (75% of the
  budget), the biggest order (Rs 75,000) and the smallest order (Rs 100).
- The day limits trader-risk enforces: **max_daily_loss_inr** (3,000), **max_trades_per_day** (3),
  **min_reward_risk** (1.0), **require_stop** (true), **no_entries_before** / **no_entries_after**
  ("09:30" / "14:45") and **flat_by** ("15:05"), all India time.
- The order shape: **product** "MIS" (intraday), **entry_order_type** "LIMIT" with
  **entry_buffer_pct** 0, **stop_order_type** "SL" with **stop_buffer_pct** 0.25, and
  **exit_buffer_pct** 0.5 for trader-broker's flatten. **charges** holds Zerodha's intraday rates.
- **blocked**: NSE symbols they never want to trade.
- Keep **paper** true until they have watched it work on paper. MIS leverage stays off
  (`allow_margin` false): a position needs the full cash.

## Step 2: size the trade

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py" --write
```

Whole shares only: NSE has no fractional shares. The size is the smallest of
four limits:

| limit | shares |
| --- | --- |
| max loss | the most shares where shares x (entry minus stop) + the round-trip charges fits `max_loss_per_trade_inr` |
| position cap | budget x `max_position_pct` / entry |
| max order | `max_order_inr` / entry |
| cash | (budget minus the cash buffer) / entry, never more than the cash in `trader/portfolio.json` |

The entry it sizes at is the LIMIT price: the plan's entry plus
`entry_buffer_pct`, rounded UP to the NSE tick. A buffer makes the buy more
likely to fill but worsens reward:risk. With `--market` it sizes at the plan's
entry, or the price in `trader/prices.json` if it is fresh (under 15 minutes
old) and higher. Kite's MCP `place_order` cannot send the market protection
Zerodha wants on MARKET and SL-M API orders, which is why LIMIT and SL are the
defaults.

Prices sit on the NSE tick grid (0.01 under Rs 250, 0.05 to 1,000, 0.10 to
5,000, 0.50 to 10,000, 1.00 to 20,000, 5.00 above): the stop rounds down,
the target to the nearest tick, a buy limit up.

It refuses, and writes no order, when: the size is zero shares, the order is
under the minimum, reward:risk is under `min_reward_risk`, the stop is not
below the entry, the target is not above it (the move already happened), the
plan is for another day, the symbol is blocked or a BSE listing, or the plan
is a short. Say the reason to the user in one line.

## Step 3: show the user

Show the four limits, which one decided the size, the shares, the rupees and
the max loss at the stop with the charges. Say plainly that a stop is an order,
not a promise: the SL sell has a limit under its trigger, and in a fast fall the
price can gap through it and the stop does not fill at all. Then hand over to
`/trader-risk`.

## Example (synthetic test data)

DEMO, a made-up NSE stock, on Monday 2026-10-19. Opening-range breakout:
entry 1366.40 at 10:00 AM, stop 1357.50 (the opening-range midpoint), target
1375.30 (1R). Risk 8.90 a share. The example config: Rs 1,00,000 budget, 10%
cash buffer, Rs 1,000 max loss.

| limit | the math | shares |
| --- | --- | --- |
| max loss | 101 x 8.90 = Rs 898.90 + Rs 95.58 charges = Rs 994.48 (102 would be Rs 1,003.86) | 101 |
| position cap | 75% of Rs 1,00,000 = Rs 75,000 / 1366.40 | **54 (decides it)** |
| max order | Rs 75,000 / 1366.40 | 54 |
| cash | Rs 1,00,000 minus Rs 10,000 = Rs 90,000 / 1366.40 | 65 |

54 shares = Rs 73,785.60. Max loss at the stop: 54 x 8.90 = Rs 480.60 plus
about Rs 73.07 charges (two Rs 20 brokerage caps, STT, NSE, SEBI, stamp, GST)
= Rs 553.67. It writes:

```json
{"variety": "regular", "exchange": "NSE", "tradingsymbol": "DEMO", "transaction_type": "BUY",
 "quantity": 54, "product": "MIS", "order_type": "LIMIT", "price": 1366.4, "validity": "DAY",
 "stop_order": {"variety": "regular", "exchange": "NSE", "tradingsymbol": "DEMO", "transaction_type": "SELL",
                "quantity": 54, "product": "MIS", "order_type": "SL", "trigger_price": 1357.5, "price": 1354.1,
                "validity": "DAY"},
 "target_price": 1375.3,
 "why": "opening-range breakout on DEMO: 54 shares, bound by your position cap (...)",
 "plan": {"entry": 1366.4, "stop": 1357.5, "stop_limit": 1354.1, "target": 1375.3, "risk_per_share": 8.9,
          "charges": 73.07, "max_loss": 553.67, "setup": "orb", "entry_time": "10:00 AM", "flat_by": "15:05"}}
```

Zerodha has no bracket orders, so this is two `place_order` calls under one
yes: the LIMIT buy, then, only after it fills, the SL sell (never before: a
triggered stop with no shares would open a short). The target is not sent: a
target limit and a stop for the same shares could both fill. trader-alerts
watches it and trader-broker's flatten exits. `stop_order`, `target_price`,
`why` and `plan` are notes for trader-risk and trader-broker; only the Kite
fields are sent. With the stop at 300.00 instead, one share would risk Rs
1,067.17 with charges, more than the Rs 1,000 max loss: no order. With a 0.1%
entry buffer the limit becomes 1367.80, the risk 10.30 and reward:risk 0.73:
refused, because the plan's 1R target no longer pays for the risk.

## Weights mode (kept from v1)

`--weights RELIANCE=30,HDFCBANK=25` or `--targets FILE` (`targets.example.json`
here: five NSE large caps, an example of the arithmetic, not a recommendation)
splits a budget across a list by weight: the cash buffer comes off the top,
weights are renormalised to 100, any stock over `max_position_pct` is pinned at
the cap and its excess shared out, slices under the minimum order are dropped,
and amounts round to whole rupees so nothing is left idle by accident. With
Rs 1,00,000, a 10% buffer and `"max_position_pct": 25`, the example gives
RELIANCE Rs 25,000 (capped), HDFCBANK Rs 23,214, INFY Rs 18,571, TCS Rs 13,929,
ITC Rs 9,286. Zerodha takes whole shares only, so only a whole-share split
(`--whole-shares` or `"fractional": false`, with prices from `--prices
trader/prices.json`) becomes orders: CNC (delivery) LIMIT buys with no stop.
trader-risk blocks them while `require_stop` is true and counts each one
against `max_trades_per_day`. Use a separate config (`--config`) for a
long-term split.

## Rules

- Proposals only. This skill never calls a broker tool.
- Never size money the user has not said is for this. Ask for the budget and the max loss.
- Every number comes from the config, the plan or the prices. Nothing is guessed.
- No projections: never say what a trade will make. A target is a plan, not a forecast.
- Free Yahoo prices are unofficial and can lag. Zerodha's price is the one that fills.
- The charges are an estimate from the config's rates; the contract note is the real number.

## What it never does

It never places, approves or schedules an order, never raises a limit on its
own, never shrinks a stop to fit more shares, never uses MIS leverage unless
the config allows it, and never sizes F&O, MTF or short positions.

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
