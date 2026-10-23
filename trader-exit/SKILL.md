---
name: trader-exit
description: >-
  Exit Planner. Sets your stop and your target before you enter, so you know
  when to get out. For NSE stocks: reads the entry from trader-entry (or a
  price you give), puts the stop at the opening-range midpoint by default (or
  the range low, your price, a percent, or a multiple of the ATR), rounds it
  down onto the NSE tick grid, and sets the target at 2R (or 1R, or your
  price). Refuses a stop at or above the entry, a target at or below it, or a
  stop too tight to survive the spread, and says why. Sets a flat-by time
  (15:05 IST by default) ahead of the closing auction and Zerodha's MIS auto
  square-off. Writes trader/plan.json for the Time Machine, the Budget Manager
  and Price Alerts. Long side only. Use when the user says "where's my stop",
  "set my stop loss and target", "plan the exit", "when do I get out", "where
  should I put my SL", "what's my R", "risk reward", or right after /trader-entry
  triggers, before /trader-timemachine.
argument-hint: "[--stop or-mid|or-low|price:X|pct:X|atr:K] [--target 2R|1R|price:X]"
---

# trader-exit

The Exit Planner decides how a trade ends before it starts. The stop is where
the setup has failed. The target is where you take what you came for. Both
are set BEFORE the entry, written down and handed on, so nobody has to decide
anything in the middle of a fast move. A stop is a plan, not a guarantee: a
gap, a circuit limit or a fast market can fill past it. Day trading loses
most people money, and a neat plan does not change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan                          # trader/entry.json: midpoint stop, 2R target
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop or-mid --target 1R --write
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop atr:1.5           # needs the session's bars (Yahoo or --offline)
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol RELIANCE --entry 1412.50 --or-high 1409.80 --or-low 1398.20
python3 "${CLAUDE_SKILL_DIR}/exit.py" show                          # the current trader/plan.json
```

Other flags: `--flat-by 15:05`, `--json`, `--now "2026-10-19 10:00"` (India
time, IST), `--entry PRICE` with a waiting entry.json to plan ahead, `--setup
level`, `--date`, `--offline FILE` (a saved Yahoo chart JSON for an ATR stop).
Symbols are NSE symbols (`RELIANCE`, `M&M`); a BSE listing is refused.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/entry.json` | the trigger from trader-entry: symbol, entry price, time, bar, opening range |
| `trader/config.json` | `flat_by` (15:05) and `min_reward_risk` (1.0: a note here, trader-risk enforces it) |
| Yahoo chart API | only for an `atr:K` stop: the session's bars up to the entry bar, free and unofficial |
| `trader/plan.json` | with `--write`: entry, stop and its rule, target and its R, risk a share, reward:risk, the tick, flat by. trader-timemachine, trader-budget and trader-alerts read it |

## Stops and targets

| stop | where it goes |
| --- | --- |
| `or-mid` | the opening-range midpoint (the default for an opening-range breakout) |
| `or-low` | the opening-range low: wider, so fewer shares for the same max loss |
| `price:X` | your price |
| `pct:X` | X percent under the entry |
| `atr:K` | K times the 14-bar ATR under the entry, on the session's bars up to the entry bar. With fewer than 14 bars by then it averages all of them and says so |

A level setup has no opening range to lean on, so it needs a stop you name.

- **The tick grid.** NSE prices move in ticks set by price: 0.01 under Rs 250,
  0.05 up to 1,000, 0.10 up to 5,000, 0.50 up to 10,000, 1.00 up to 20,000 and
  5.00 above (NSE circular, from 2025-04-15). The plan uses the entry's tick
  for every price and writes it to `plan.json`. NSE reviews each stock's tick
  monthly on the last month's close, so a stock near a band edge can differ:
  if Kite rejects a price, check the tick in the order window.
- **Rounding.** The stop rounds AWAY from the entry onto the tick (down, for a
  long). Risk = entry minus stop. Target = entry + R x risk, rounded half up
  to the tick. `2R` by default; `1R`, `1.5R` or `price:X` also work.
- **Refused, with the reason.** A stop at or above the entry. A target at or
  below it. A risk under 0.05% of the price or two ticks, whichever is bigger:
  too tight to survive the spread, normal wiggle would stop you out. An
  entry.json that has not triggered (unless you give `--entry`). A short: this
  pack plans longs only.
- **Flat by.** 15:05 IST by default (or `flat_by` in the config). Out by then,
  win or lose, no overnight hold. From 2026-08-03 to 2026-12-06 F&O stocks stop
  continuous trading at 15:15 for the closing auction, and Zerodha squares off
  their MIS positions at about 15:12; other stocks get squared off at about
  15:25, for Rs 50 + GST an order. A flat-by later than that gets a note. A
  flat-by at or after the session's close (15:30) is refused, and so is an
  entry at or after the flat-by time.

## Steps

1. Run it right after `/trader-entry` triggers. Before that, `--entry PRICE`
   plans ahead at the level you expect.
2. Use the default stop for an opening-range breakout unless the user names
   another. A level setup needs one: ask.
3. Run `exit.py plan --write`. Read back the entry, the stop and its rule,
   the target and its rule, the risk a share, reward:risk and flat by.
4. If it refuses, say the reason and the fix (a wider stop, a farther
   target). Never loosen anything on your own.
5. Say once that the stop is a plan, not a guarantee.
6. Hand off to `/trader-timemachine` to replay the plan, then `/trader-budget`
   to size it to the max loss per trade. Offer `/trader-alerts` for the stop,
   the target and the flat-by time. Claude can keep `entry.py watch` running
   in the background or on a `/loop` until a setup triggers, then come here.

## Example: the DEMO story

This is synthetic test data (DEMO is a made-up NSE stock, from the pack's
test fixtures). Monday 2026-10-19: the entry is 1366.40 at 10:00 AM (the 9:55
bar, bar 8) and the opening range is 1351.30 to 1363.80. `exit.py plan --stop
or-mid --target 1R --write`:

```
Exit plan: DEMO long on the NSE, Mon 2026-10-19, opening-range breakout on 5-minute bars
  Entry         1366.40 at 10:00 AM (bar 8)
  Stop          1357.50  opening-range midpoint: (1363.80 + 1351.30) / 2 = 1357.55, rounded down to the 0.10 tick
  Target        1375.30  1R (one times the risk)
  Risk a share  Rs 8.90 (0.65% of the entry)
  Reward:risk   1.0
  Flat by       15:05 IST, win or lose: no overnight hold
```

The default 2R target would be 1366.40 + 2 x 8.90 = 1384.20. A stop at
1366.00 is refused: 0.40 under the entry is less than 0.05% of the price
(0.68), too tight to survive the spread. trader-budget then sizes the plan to
the max loss per trade, charges included.

## Rules

- The stop and the target are set before the entry, never after.
- Never move a stop further from the entry once the trade is on.
- Plain numbers only: entry, stop, target, risk a share, reward:risk. It
  never prints a P&L or a return.
- Nothing here promises winning trades or money. A 1R target is not a
  prediction that the price gets there.
- Long side only. This pack plans longs only.

## What it never does

It never places an order, never sizes a position, never widens a stop to
make a plan pass, and never calls a stop a guarantee.

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
