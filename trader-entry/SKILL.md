---
name: trader-entry
description: >-
  Entry Timer. Watches an NSE setup bar by bar and tells you the moment to get
  in. Reads free Yahoo 1-minute or 5-minute bars for NSE stocks (RELIANCE.NS
  and so on), builds the opening range (the first 15 minutes from the 9:15 IST
  open by default) and calls the entry on the first completed bar that CLOSES
  above the range high, or above a level you give it (like a 20-day high from
  the scanner), with an optional volume check. Says waiting and how far,
  triggered with the time and the price, expired, or not open yet, knows NSE
  holidays and special sessions like Muhurat trading, and writes
  trader/entry.json for /trader-exit. Long side only. It never places an
  order. Use when the user says "tell me when to get in", "watch RELIANCE",
  "has HDFCBANK broken out", "did it break the opening range", "ORB entry",
  "entry signal", "time my entry", "check my watchlist", or after
  /trader-scanner and /trader-news, before /trader-exit.
argument-hint: "[SYMBOL] [--setup orb|level] [--interval 5m|1m]"
---

# trader-entry

The Entry Timer turns a setup into one moment: the bar that confirms it. It
reads bars, 1-minute or 5-minute, and waits for each one to finish. A price
that pokes above the high in the middle of a bar is not an entry. A bar that
CLOSES above it is. Then it says the time and the price, writes them down for
the Exit Planner, and stops. It never buys anything. Day trading loses most
people money (SEBI's study found 7 in 10 individual intraday traders lost
money in FY23), and a clean signal does not change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE              # 15-minute opening range, 5-minute bars
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --write      # also save trader/entry.json
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --setup level --level 1412.50
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --interval 1m --min-volume-x 1.5
python3 "${CLAUDE_SKILL_DIR}/entry.py" watch RELIANCE --until 14:45  # one line per closed bar, stops on the trigger
python3 "${CLAUDE_SKILL_DIR}/entry.py" watchlist                   # every hit in trader/watchlist.json
```

Symbols are NSE symbols: `RELIANCE`, `M&M`, `BAJAJ-AUTO`. `RELIANCE.NS` and
`NSE:RELIANCE` work too; a BSE listing (`.BO`) is refused. Other flags:
`--or-minutes N` (default 15), `--json`, `--offline FILE` (a saved Yahoo chart
JSON, `.json` or `.json.gz`, or a folder of `<SYMBOL>.json`), `--now
"2026-10-19 10:00"` (India time, IST) to replay a day. `watch` with `--now` or
`--offline` runs on a simulated clock and replays the day bar by bar without
waiting.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/watchlist.json` | the scanner's hits (`hits[].symbol`), for `watchlist` |
| `trader/config.json` | `no_entries_before` (09:30) and `no_entries_after` (14:45), India time. Optional |
| Kite Connect (read only) | today's candles from your own Kite app after the daily login: live and official |
| Yahoo chart API | otherwise, today's bars for `SYMBOL.NS`, `range=1d&interval=5m` (or `1m`): free, unofficial, can lag |
| `trader/entry.json` | with `check --write`, or when `watch` triggers: symbol, exchange, setup, status, time, bar, price, level, opening range |

## Data source

`--source auto` (the default, or `"data_source"` in `trader/config.json`)
reads today's candles from the user's own Kite Connect app when they have logged in today
(`/trader-broker`: `kite.py login`, then `kite.py session`; Zerodha ends
every session at 6:00 AM), and from free Yahoo bars otherwise, saying so once.
`--source kite` insists on Kite, `--source yahoo` skips it, `--offline` wins
over both. Kite is read only here: it never places anything. The header says
which source it used ("Kite, live" or "Yahoo, free, can lag"). The API key,
secret and access token stay in the environment and in
`trader/state/kite-session.json`: never ask for them, never print them, never
paste them in the chat.

## The rule

- **The session.** NSE's normal market runs 9:15 to 15:30 IST, Monday to
  Friday, with no daylight saving. NSE holidays and weekends are closed. A
  special session (a Budget Sunday, Muhurat trading on Diwali) counts as a
  trading day with its own hours.
- **Opening range (orb).** The high and low of the first 15 minutes: the
  9:15, 9:20 and 9:25 five-minute bars, or fifteen 1-minute bars. On a special
  session the range starts at that session's open (14:20 on Muhurat day). The
  level to beat is the range high.
- **Level.** A price you give, like a 20-day high from `/trader-scanner`.
- **The trigger** is the first completed bar after the range that CLOSES
  above the level. A wick above is not a trigger. A close exactly at the
  level is not above it.
- **The time** is that bar's close time (the 9:55 bar closes at 10:00 AM).
  The price is its close, to the paisa.
- **Only closed bars count.** A bar is closed when the clock passes its start
  plus 5 (or 1) minutes and Yahoo has caught up to that moment (it waits a
  minute at most). Yahoo's live tick, like 10:49:45, is never a bar.
- **Volume, if you want it.** `--min-volume-x 1.5` also needs the trigger
  bar's volume at 1.5 times the range bars' average. The 9:15 bar carries the
  pre-open auction's trades, so 1.0 is already strict.
- **Time limits.** No entries before 9:30 or after 14:45 (config), and never
  after the session's close. 14:45 leaves time to get out by the 15:05 flat-by,
  before the closing auction and Zerodha's MIS auto square-off.
- **What it says.** Waiting (and how far the last close is from the level),
  triggered, expired, or no data. Before 9:15: not open yet. Before 9:30: the
  range is still forming. Weekends and NSE holidays: the market is closed, and
  the next open. The holiday list covers 2026; for later dates it says to
  check nseindia.com.

## Steps

1. Get the symbol from the user, or run `entry.py watchlist` after
   `/trader-scanner`. Check `/trader-news` for it first.
2. Run `entry.py check SYMBOL`. Tell the user the status in one line:
   waiting and how far, triggered at what time and price, or expired.
3. To wait for it, Claude can run `entry.py watch SYMBOL --until 14:45` in
   the background, or `entry.py check SYMBOL --write` on a `/loop` every 5
   minutes. `watch` wakes just after each bar closes, prints one line per
   bar, and exits 0 on the trigger (it writes `trader/entry.json`) or 1 when
   the setup expires. Keep the computer awake while it watches: a sleeping
   laptop pauses it, and it catches up, late, when it wakes.
4. When it triggers, say the time and the price, and that nothing was placed.
   If the trigger is more than a bar old, say that too: a late entry is a
   different trade.
5. Hand off to `/trader-exit` to set the stop and the target BEFORE anything
   is bought.

## Example: the DEMO story

This is synthetic test data (DEMO is a made-up NSE stock, from the pack's
test fixtures), so the numbers are easy to follow. Monday 2026-10-19,
5-minute bars. The opening range is 1351.30 to 1363.80. The 9:35 bar wicks to
1364.50 and the 9:40 bar closes exactly at 1363.80: neither is a trigger. At
9:57 the 9:55 bar is still open, so it waits:

```
WAITING  last close 1362.90 (9:50 bar), 0.90 under the range high 1363.80 (-0.07%).
The 9:55 bar is still forming (1366.40 so far, closes 10:00 AM); it does not count until it closes.
```

At 10:00 the bar has closed:

```
DEMO  Mon 2026-10-19 10:00 IST  opening-range breakout (15-minute range) on 5-minute bars (Yahoo, free, can lag)
Opening range 9:15-9:30: high 1363.80, low 1351.30 (3 of 3 bars).
TRIGGERED 10:00 AM at 1366.40: the 9:55 bar (bar 8) closed above the range high 1363.80 (+0.19%).
This is the moment to get in, if you take the trade. Nothing was placed.
Next: set the stop and target BEFORE you buy: /trader-exit (exit.py plan --from trader/entry.json).
```

With `--min-volume-x 1` the 9:55 bar is too thin (0.84x the range's volume)
and the trigger moves to 10:30 AM at 1371.60 (the 10:25 bar, 1.16x).

## Rules

- Bar by bar, on free Yahoo 1-minute or 5-minute bars. They are unofficial
  and can lag. Say so when a minute matters.
- A signal is information, not an order, and not a promise. Nothing here
  promises winning trades or money.
- Long side only. This pack plans longs only. If the user wants a short, say
  that.
- One trigger per setup per day: the first close above. Later closes do not
  reset it.
- Never call an entry early because a bar looks like it will close above.
- Times are India time (IST). Never convert them to another zone for the user.

## What it never does

It never places an order, never sizes a position, never sets the stop (that
is `/trader-exit`), never moves the level after the fact, and never counts a
bar that has not closed. It never runs `/trader-broker` from a background
watch: nobody is there to say yes.

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
