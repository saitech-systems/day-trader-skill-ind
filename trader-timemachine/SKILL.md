---
name: trader-timemachine
description: >-
  Time Machine. Replays every possible outcome of an intraday trade on an NSE
  stock before you risk a rupee: 10,000 simulated sessions plus every past
  time the setup fired. Takes the plan from trader-exit (entry, stop, target,
  flat-by time), rebuilds the rest of the day 10,000 times from the stock's
  own 5-minute bars (Yahoo, SYMBOL.NS) over the last 60 days, in India time,
  and re-runs the same rule on every past session. Prints the odds of target
  first, stop first, or neither by your flat-by time, where the price sits
  then, the average R, the history, and one honest verdict, then hands off to
  /trader-budget. Use when the user says "run the time machine", "replay this
  trade", "what are the odds on RELIANCE", "simulate this trade", "monte
  carlo", "backtest this ORB", "how often does this hit the target", "is this
  a coin flip", or after /trader-exit writes a plan, before /trader-budget.
argument-hint: "[path to plan.json, or NSE symbol]"
---

# trader-timemachine

The Time Machine sits between the plan and the money. trader-exit has set
the entry, the stop, the target and the flat-by time. Before anything is
sized or sent, this skill asks one question: if today plays out like the
stock's own last 60 days, how often does this plan hit the target first, the
stop first, or neither by the time you planned to be out? It answers with
10,000 replays and with the real record of the same rule on every past
session. It is a simulation built from the stock's own recent bars, not a
prediction. Most intraday traders lose money (SEBI found 7 in 10 individual
intraday traders in the cash segment lost money in FY23), and a good-looking
replay does not change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run                    # trader/plan.json, 10,000 replays, seed 19
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --write            # also save trader/timemachine.json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --until 15:30      # replay to the close instead of your flat-by
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --symbol RELIANCE --date 2026-10-19 \
    --entry 1366.40 --stop 1357.50 --target 1375.30 --entry-time "10:00 AM"
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" chart --from           # ASCII fan chart of the saved run
```

Other flags: `--replays N`, `--seed N` (default 19), `--window N` (default 2
bars), `--offline FILE` (a saved Yahoo chart JSON, `.json` or `.json.gz`),
`--now "2026-10-19 10:15"` (India time) to pin the clock. Symbols are NSE
symbols (`RELIANCE`, `M&M`, `BAJAJ-AUTO`); `RELIANCE.NS` works too, a BSE
`.BO` listing does not.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the plan from trader-exit: symbol, date, entry, entry_time / entry_bar, stop, stop_rule, target, target_r, opening range, flat_by |
| Kite Connect (read only) | after the daily login, one request: the same 60 sessions of 5-minute candles from your own Kite app, live and official |
| Yahoo chart API | otherwise one request: 60 days of 5-minute bars (`SYMBOL.NS`, `range=60d&interval=5m`), free and unofficial, can lag |
| `trader/timemachine.json` | with `--write`: `{plan, mc, history}` plus the verdict. `mc` holds the odds, percentiles, the fan-chart bands, 160 sample paths and a 24-bin histogram of the price at the end |

## Data source

`--source auto` (the default, or `"data_source"` in `trader/config.json`)
reads the same 60 sessions of 5-minute candles from the user's own Kite Connect app when they have logged in today
(`python3 trader-broker/kite.py login`, then `kite.py session`; Zerodha ends
every session at 6:00 AM), and from free Yahoo bars otherwise, saying so once.
`--source kite` insists on Kite, `--source yahoo` skips it, `--offline` wins
over both. Kite is read only here: it never places anything. The header says
which source it used ("Kite, live" or "Yahoo, free, can lag"). The API key,
secret and access token stay in the environment and in
`trader/state/kite-session.json`: never ask for them, never print them, never
paste them in the chat.

On the synthetic DEMO data both sources give the same replays bit for bit;
the report ends with which one it used (`Bars: Kite, live.`).

## How it works

1. **The end of the replay.** By default the plan's `flat_by` (15:05 in the
   example config: out before Zerodha squares off MIS positions itself), else
   the 15:30 close. `--until` sets it by hand. NSE's session is 9:15 to 15:30
   India time, 75 five-minute bars, no daylight saving.
2. **The pool.** It keeps every session before the plan's date that has
   EVERY five-minute bar from 9:15 through that end. Sessions with a gap
   before it (a halt, a missing bar) and today are left out. A gap after the
   end does not matter: F&O stocks stopped continuous trading at 15:15 for the
   closing auction from 2026-08-03 to 2026-12-06, and a 15:05 replay never
   needs those bars. Every bar becomes four ratios to the bar before it:
   open, high, low and close over the previous close.
3. **One replay.** Start at the entry price. For each bar left before the
   end, pick a past bar from the same time of day (plus or minus 2 bars) at
   random, and scale it to the current price. Bar by bar, it checks the stop
   first, then the target. A bar that touches both counts as the stop.
4. **10,000 replays** give four odds. Target first. Stop first. Neither,
   which means the trade was still open at the end. Green, which means target
   first or above the entry at the end. R is +1 at a 1R target, -1 at the
   stop, and otherwise the distance from the entry at the end divided by the
   risk. A gap through the stop still counts as -1R here. A real fill can be
   worse, and charges (brokerage, STT, GST) are not in the R.
5. **The history.** The plan's own rule (the same opening range, stop rule
   and target R) runs again on every one of those past sessions, with stops
   and targets on the NSE tick like trader-exit's. That is what really
   happened, not a simulation.
6. **The verdict** is one line from the numbers. It looks only at the
   replays that hit one side, and asks how often the target came first. A
   bigger target needs to win less often to break even: 1 in (1 + R). That
   is 50 % at 1R, 33 % at 2R, 25 % at 3R. Less than 5 points from break-even:
   "Close to a coin flip" at 1R, "Close to break-even" at any other R. 5 to
   15 points: "Leans toward the target" or "Leans toward the stop". 15 or
   more: "Strongly toward" one of them. So a 2R plan with target first
   30.6 % and stop first 54.0 % is close to break-even (it needs 33 % of the
   84.7 % that hit a side, and got 36 %), not a loser.
7. **Below the verdict**, when it applies: a line when more than 40 % of the
   replays hit neither (the price at the end decides those), and the
   average R when its sign goes against the lean. Those can only point the
   other way because of the replays that hit neither.

**The seed.** The draws come from Python's `random.Random(seed)`, seed 19 by
default. Same plan, same data, same seed: the same numbers, on any machine.
Say the seed with every result so the user can repeat it.

**5-minute bars only.** Yahoo keeps 60 days of 5-min bars but only about 7
days of 1-min bars, too few sessions for a fair pool. It refuses, in one
plain line, when there are fewer than 20 usable sessions, when the entry bar
is not in the data (wrong date, not closed yet, a future day), when the
plan's day is not a normal NSE session (a holiday like Dussehra, a weekend,
or a special session like Muhurat trading that opens at 14:20), or when the
plan does not make sense (a stop above a buy's entry, an entry off the
5-minute grid). A live in-progress bar is never used.

## Steps

1. Make sure `trader/plan.json` is today's plan from `/trader-exit`. If there
   is none, run that first, or pass the plan as flags (then give `--until`
   your flat-by time).
2. Run `timemachine.py run --write`. The replays end at the plan's flat-by
   time, so "neither" means still open when you would get out.
3. Tell the user, in this order: the four odds, the median and the 5 to 95
   range of the price at the end, the average R, the history line, then the
   verdict line and any line under it exactly as printed. Say the seed.
4. If the verdict is a coin flip or close to break-even, say so plainly. Do
   not dress it up. If the history and the replays disagree, say that too.
5. Say once that this is built from the last 60 days and is not a prediction.
   Results, news, circuit limits and halts can do things those 60 days never
   did. Check `/trader-news` for the stock before going on.
6. If the user still wants the trade, hand off to `/trader-budget` to size it.

## Example (synthetic test data)

The tests run on a made-up NSE stock, DEMO, with seeded random 5-minute bars
(`tests/fixtures/shared/`); the numbers below show the output, not anyone's
odds. Monday 2026-10-19: the 15-minute opening range is 1351.30 to 1363.80.
The 9:55 bar closes above it: entry 1366.40 at 10:00 AM (bar 8), stop
1357.50 (the range midpoint, rounded down to the 0.10 tick), target 1375.30
(1R, risk 8.90 a share), flat by 15:05.

```
10,000 replays of the rest of the session, 10:00 AM to 3:05 PM, your flat-by time (61 five-minute bars), seed 19.
  target first   53.6 %
  stop first     45.1 %
  neither        1.4 %    still open at 3:05 PM
  green          54.3 %   target first, or above the entry at 3:05 PM
  Price at 3:05 PM: median 1369.73, 90 % of replays between 1343.24 and 1397.60.
  Average R: +0.086
History: the same rule on each of those 59 real sessions fired 47 times:
23 target, 21 stop, 3 neither. Average R +0.025.
Close to a coin flip: target first 53.6 % vs stop first 45.1 %.
```

At 1R the target has to come first in half of the replays that hit one
side. It did in 54.3 %: close to a coin flip, and the history agrees. That is
the honest answer for this setup, and it is what the user should hear before
`/trader-budget` sizes it. Charges are on top: a round trip of 54 shares at
these prices costs about Rs 73 at Zerodha's intraday rates, so a coin flip at
1R loses a little on average.

## Rules

- Every number comes from the run. Never round a coin flip up into an edge.
- The verdict line and the lines under it are printed by the tool. Repeat
  them, do not rewrite them. Never judge a 2R or 3R plan by target first vs
  stop first alone: the verdict already weighs the target's R.
- Simulated odds are not predictions and not promises. Say it once, plainly.
- Free Yahoo data is unofficial and can lag. Bars are 5-minute bars, checked
  bar by bar, never tick by tick and never real time.
- Always say the seed, so any run can be repeated.
- One plan per run. A changed entry, stop, target, flat-by or date is a new run.

## What it never does

It never places an order, never sizes a position, never changes the plan,
and never calls a result a sure thing. It never prints a P&L or a profit.
It never uses a bar that has not closed yet, and never mixes today's bars
into the pool.

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
