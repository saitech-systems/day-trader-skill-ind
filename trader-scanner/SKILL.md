---
name: trader-scanner
description: >-
  Chart Scanner. Scans hundreds of NSE charts for setups: opening-range
  breakouts, flags, oversold bounces. Builds the list from NSE's own files
  (the Nifty 500 by default, or another Nifty index, the F&O stocks, or every
  EQ-series stock), runs a pre-market scan of daily closes (20-day
  closing-high breakout, bull flag, reclaimed the 20-day average, oversold,
  oversold bounce) and an intraday opening-range breakout scan on 5-minute
  bars from the 9:15 open, India time, then writes trader/watchlist.json for
  trader-news and trader-entry. Live data from your own Kite Connect session
  when you are logged in (read only), else free Yahoo data (RELIANCE.NS), bar
  by bar.
  Use when the user says "scan the Nifty", "scan the market", "find me
  setups", "what is breaking out", "opening range breakouts", "ORB scan",
  "scan the F&O stocks", "scan for bull flags", "oversold stocks",
  "pre-market scan", "build my watchlist", "what should I look at today", or
  at the start of a trading day before /trader-news.
argument-hint: "[universe | daily | orb] [--at HH:MM]"
---

# trader-scanner

The Chart Scanner is the first step of a day trade. It reads hundreds of
free NSE charts and lists the ones that match a rule. That is all it does. A
scan is a list of charts that match a rule, not a list of stocks to buy, and
a setup on a chart is not a prediction. Most people who day trade lose money
(SEBI's 2024 study: 7 in 10 individual intraday traders in the cash segment
lost money in FY23); the scanner does not change that. It only saves you
from flipping through 500 charts by hand.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe                          # Nifty 500 -> trader/universe.txt
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe --index nifty100         # or --fno, or --all
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily                             # before the open -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols RELIANCE,TCS,M&M  # or --watchlist FILE, --limit 200
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb                               # after 9:35 AM IST -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --at 10:15                    # cut the clock at 10:15 AM IST
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --watchlist trader/watchlist.json --json
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --source kite                 # insist on Kite (or --source yahoo)
```

Flags: `--min-price 20` (rupees) and `--min-volume` (daily 2,00,000 shares,
orb 0), `--or-minutes 15`, `--max-confirm 200` (charts with real highs and
lows to fetch), `--source auto|kite|yahoo`,
`--now "2026-10-19 10:15"` (India time) to pin the clock, `--offline PATH` to
read saved files, `--no-write`, `--show N`, `--json`. Symbols are plain NSE
symbols (RELIANCE, M&M, BAJAJ-AUTO); `RELIANCE.NS` and `NSE:RELIANCE` work
too, a BSE code is refused. Exit code 0 when the scan found something, 1
when it found nothing or could not run (the reason is one plain line).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/universe.txt` | written by `universe`: one `SYMBOL\|Name` a line, read by `daily` and `orb` |
| `trader/watchlist.json` | written by `daily` and `orb`: `mode`, `as_of`, `universe`, `scanned`, `failed`, `exchange`, `hits[]`. trader-news and trader-entry read `hits[].symbol` (plain NSE symbols) |
| `trader/config.json` | read by `orb`: `no_entries_after` (default 14:45); by both: `data_source` (auto) |
| `trader/state/kite-session.json` | your Kite session, written by `trader-broker/kite.py login`; read, never written here |

## The universe

`universe` downloads one of NSE's own list files from
nsearchives.nseindia.com (with a browser User-Agent; NSE blocks some
scripts, and then the tool says so and you can save the CSV from a browser
and run `universe --offline FOLDER`):

| flag | file | keeps |
| --- | --- | --- |
| (default) | `content/indices/ind_nifty500list.csv` | the Nifty 500, series EQ |
| `--index nifty50` (`nifty100`, `nifty200`, `midcap150`, `smallcap250`) | the matching `ind_nifty...list.csv` | that index, series EQ |
| `--fno` | `content/fo/fo_mktlots.csv` | stocks with futures and options; index derivatives (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50) are out |
| `--all` | `content/equities/EQUITY_L.csv` | every EQ-series stock |

Out, with the reason counted: series BE and BZ (trade for trade: delivery
only, no intraday), SM and ST (the SME platform), anything that is not a
plain NSE symbol (letters, digits, `&` and `-`, like M&M or BAJAJ-AUTO), and
a symbol listed twice. NSE pads its cells and headers with spaces; the tool
strips them. The Nifty 500 is the default because it is liquid and a whole
scan is about 25 spark requests; `--all` is about 2,000 stocks, many of them
thin.

## The daily scan (before the open)

Yahoo's spark endpoint returns closes for 20 symbols a request, so the Nifty
500 takes 25 requests. Liquidity comes from the spark meta: last price at
least `--min-price` (Rs 20) and last session's volume at least
`--min-volume` (2,00,000 shares). Run it before the 9:15 open or after the
3:30 PM close: during the session the volume is the day's so far, and
today's daily bar is left out until 3:30 PM.

The setups are computed from daily closes only (spark has no highs or lows):

| setup | the rule on closes |
| --- | --- |
| `breakout_20d` | the last close is above every close of the 20 sessions before it |
| `bull_flag` | up more than 10 % over the 10 sessions before the last 5, then the last 5 closes inside a 5 % range |
| `reclaim_ma20` | the close before was under its 20-day average, the last close is above it |
| `oversold` | RSI 14 (Wilder) under 30 |
| `oversold_bounce` | RSI 14 was under 30 yesterday and today closed up |

A closing-high breakout fires more often than an intraday-high one. Hits are
listed most traded first (price times volume).

## The opening-range breakout scan (during the session)

The opening range is the first `--or-minutes` (15) of the session: the 9:15,
9:20 and 9:25 five-minute bars (the 9:00 to 9:08 pre-open call auction is not
part of it). A hit is the first completed 5-minute bar to CLOSE above the
range high, at that bar's close time; the earliest is 9:35 AM.

1. Stage 1, every symbol: spark closes of today's 5-minute bars, 20 a
   request. A chart goes on only if a completed bar after the range closed at
   or above the highest close of the range. That is a necessary condition
   (the range high is at least every range close), so no breakout is lost.
2. Stage 2, each candidate: the v8 chart with real highs and lows, one
   request each. Range high = the highest high of the range bars, range low =
   the lowest low. On a strong morning most charts are candidates, so stage 2
   checks at most `--max-confirm` (200) of them, the most traded first (price
   times volume so far), and prints PARTIAL with the count it skipped.
   `--max-confirm 0` checks them all.

A chart is usable only when every 5-minute bar from the open to the cutoff
is there; a missing bar means a halt, a stock stuck at its circuit limit, or
no trades. Null closes are dropped, a bar off the 5-minute grid (the live
tick, like 10:49:45) is dropped, and a bar counts only once it is complete
(start + 5 minutes). Before 9:35 AM, on a weekend or an NSE holiday, after
the 3:30 PM close, or after `no_entries_after` (an expired setup) it prints
one plain line and writes nothing.

The NSE calendar holds the 2026 holidays and the special sessions: the
Sunday Budget session (1 Feb 2026, normal hours) and Muhurat trading
(Sunday 8 Nov 2026, 2:20 to 3:30 PM), whose range starts at its own open, so
the default 14:45 entry cutoff leaves Muhurat a short window. For another
year it says the holiday list is unknown: check nseindia.com. From 3 Aug to
6 Dec 2026, F&O stocks stop continuous trading at 3:15 PM for the closing
auction; the default cutoff is well before that.

## Data source

`--source auto` (the default, or `"data_source"` in `trader/config.json`)
reads your own Kite Connect session when `trader-broker/kite.py` logged in
today (the Connect plan carries the market data), else free Yahoo data with a
one-line note; `--source kite` insists, `--source yahoo` never asks Kite. Kite
is read only here: quotes and candles, never an order. `daily` asks one
`/quote` batch (500 symbols a call) for the last price, then about 120 days
of day candles per symbol, 0.35 s apart (Kite allows 3 requests a second), so
the Nifty 500 takes about 3 minutes; a stock under `--min-price` on its quote
is not fetched, and the volume floor reads the last complete day. `orb` asks
one `/quote` batch for the price and volume floors (the live quote), then
the day's 5-minute candles for the rest, the most traded first, at most
`--max-confirm` of them (PARTIAL when it stops short); the hit rule is the
same. A refused or expired session stops the scan in one plain line: log in
again. The header says which source it used ("Kite, live" or "Yahoo, free,
can lag"), and so does `source` in the watchlist.

## Data and limits

- Yahoo's chart and spark endpoints are free, unofficial and can lag. Bars are
  5-minute bars, bar by bar, never tick by tick. query2 first, query1 on a 429
  or 5xx, one retry after a 5-second wait on a 429. Symbols go out as
  `RELIANCE.NS` (`M%26M.NS` for M&M).
- Partial results are said as partial: "Fetched 480 of 500 charts (20
  failed or had no data)". Never round a partial scan up to "the whole market".
- NSE holidays and special sessions for 2026 (the NSE holiday list, read
  2026-10-23).

## Steps

1. Once a week or so: `scan.py universe` (the Nifty 500), or `--fno` if the
   user trades the F&O names.
2. Before the 9:15 open: `scan.py daily`. Tell the user the counts (fetched,
   liquid, scanned, hits) and the top few by setup.
3. After 9:35 AM IST (10:15 is a common time): `scan.py orb`. Tell the user
   how many usable charts broke their range and when, and that a later hit is
   closer to the price now.
4. The user picks one chart. Hand it on: `/trader-news SYMBOL` (what is moving
   it), then `/trader-entry SYMBOL` (the entry bar by bar). Never pick for them.

## Example (synthetic test data)

Monday 2026-10-19, scan at 10:15 AM over the test wall
(`tests/fixtures/trader-scanner/wall5m-2026-10-19-synthetic.json.gz`: 33
made-up NSE symbols, built to plan, not market data):

```
python3 scan.py orb --offline wall5m-2026-10-19-synthetic.json.gz --now "2026-10-19 10:15" --min-price 0
Opening-range breakout scan, Mon 2026-10-19 10:15 AM IST: 15-minute range (9:15 AM to 9:30 AM), 5-minute bars (Yahoo, free, can lag), NSE.
Fetched 32 of 33 charts (1 failed or had no data); 28 usable; 15 candidates, 15 confirmed with real highs and lows.
13 of 28 charts closed above their opening-range high by 10:15 AM:
   9:35 AM  SYNA           Rs 414.60  range 409.70 to 413.35  +0.30% over
  ...
  10:00 AM  DEMO         Rs 1,366.40  range 1351.30 to 1363.80  +0.19% over
```

With the default Rs 20 floor it is 12 (PENNY at Rs 12.50 drops out). DEMO's
range is the 9:15 to 9:25 bars, high 1363.80, low 1351.30; the 9:35 bar
wicked above the high and the 9:40 bar closed exactly at it, neither counts;
the 9:55 bar closed at 1366.40, 0.19 % over the high, at 10:00 AM. The user
picks DEMO and Claude hands it to `/trader-news DEMO`, then
`/trader-entry DEMO`. On the same charts served as Kite answers
(`--source kite`), the scan finds the same 13.

The pre-market scan of the test daily charts (closes through Fri 16 Oct)
tags BRKOUT a 20-day breakout, FLAGGY a bull flag, RECLAIM a reclaim of the
20-day average, WEAKLING oversold and BOUNCE an oversold bounce; THIN (50,000
shares a day) and CHEAP (about Rs 16) fail the liquidity filter.

## Rules

- Say what was scanned: which list, how many symbols, how many charts came
  back, how many were usable, and the time of the data (IST).
- Say "matched a rule", never "a buy", "a winner" or "a good trade".
- Never print a P&L, a return or a profit figure.
- A hit is stale once the price has moved on. Re-run before acting on it.

## What it never does

It never places, sizes or checks an order, never says which stock to buy,
never calls data real time, and never promises that a setup works.

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
