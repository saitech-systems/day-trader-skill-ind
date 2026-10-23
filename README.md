# The Day Trader skill: India edition

11 Claude skills for day trading Indian stocks on the NSE: scan the Nifty 500, track news and exchange filings, time every entry and exit, and replay every outcome before you risk a rupee.

Free, MIT, no signup. The tools are plain Python that read free public data:
Yahoo Finance charts for NSE stocks (`RELIANCE.NS`), NSE's index lists, company
announcements, results, insider-trading disclosures and bulk and block deals.
Orders go to Zerodha through its Kite MCP server, and only after your yes.

One scans the Nifty 500 for setups. Another tracks the headlines and the NSE
filings on every stock you watch. One watches a setup bar by bar and tells you
the moment to get in, and another sets your stop and your target before you
do. The Time Machine replays the rest of the session 10,000 times from the
stock's own recent bars, plus every past day the same setup fired, and tells
you the honest odds. The Budget Manager counts Zerodha's charges (brokerage,
STT, stamp duty, GST) inside your max loss, because on a small intraday trade
they are a real slice of it.

And no trade goes through until you say yes. Nothing in this pack can place
an order on its own. Paper trading is the default.

> **Where this comes from.** This is the India edition of Jake Schincariol's
> [day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill) (MIT),
> which was built for US stocks. Everything US-only is gone: the NYSE clock,
> SEC filings, the congressional-trades tracker, Alpaca and the US
> pattern-day-trader rule. In their place: the NSE calendar in India time, NSE
> symbols and tick sizes, NSE filings, SEBI insider disclosures, Zerodha Kite
> and Indian intraday charges.

## Install

Paste this into Claude:

```
https://github.com/saitech-systems/day-trader-skill-ind

install skill
```

Or as a plugin, in Claude Code:

```
/plugin marketplace add saitech-systems/day-trader-skill-ind
/plugin install day-trader-skill-ind@day-trader-skill-ind
```

Claude Code namespaces plugin skills, so installed as a plugin they show up as
`/day-trader-skill-ind:trader-scanner` and so on. Copy the folders instead if
you want plain `/trader-scanner`:

```bash
git clone https://github.com/saitech-systems/day-trader-skill-ind.git
cp -r day-trader-skill-ind/trader-* ~/.claude/skills/
```

Project-local instead of global: copy the same folders into your repo's
`.claude/skills/`. No Claude Code at all? Paste any single `SKILL.md` at the
top of a chat and it runs as a mode. You lose the Python tools, but the method
works.

The tools need Python 3.8 or newer. Nothing to pip install, no API key for the
data. Placing real orders needs a Zerodha account and the Kite MCP server: see
`/trader-broker`.

## The eleven

| command | job | what it does |
| --- | --- | --- |
| `/trader-scanner` | Chart Scanner | Scans the Nifty 500 (or Nifty 50, the F&O list, every NSE stock) for setups: opening-range breakouts, flags, oversold bounces. |
| `/trader-news` | News Monitor | Tracks the headlines and the NSE company announcements on every stock you watch and flags what moves it. |
| `/trader-entry` | Entry Timer | Watches a setup bar by bar and tells you the moment to get in. |
| `/trader-exit` | Exit Planner | Sets your stop and your target on the NSE tick before you enter, so you know when to get out. |
| `/trader-timemachine` | Time Machine | Replays every possible outcome of a trade before you risk a rupee: 10,000 simulated sessions plus every past time the setup fired. |
| `/trader-risk` | Risk Manager | Checks every order against your limits and stops anything over them. |
| `/trader-budget` | Budget Manager | Sizes every position to your budget and your max loss per trade, charges included. |
| `/trader-broker` | Order Executor | Sends approved orders to Zerodha through Kite, paper trading by default, never without your yes. |
| `/trader-alerts` | Price Alerts | Rings when a price, a stop, your flat-by time or the 9:15 opening bell hits. |
| `/trader-analyst` | Earnings Analyst | Reads the quarterly results a company filed with the NSE and sums up what changed. |
| `/trader-insider` | Insider Tracker | Reads SEBI insider-trading disclosures (promoters, directors) and NSE bulk and block deals as they post. |

## How to use it

Everything lands in a `trader/` folder in your project, and each skill reads
what the last one wrote:

```
scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> broker   (one trade)
alerts: stop, target, flat by 15:05                                           (while it is open)
news / analyst / insider                                                       (on a schedule)
```

An example, end to end. It runs on the **synthetic test data** in
`tests/fixtures/shared/`: a made-up NSE stock, DEMO, on Monday 19 October 2026
(see [Tests](#tests) for why). The numbers are what the tools print on it.

1. **`/trader-scanner`** after 9:35 AM IST: DEMO closed above its 15-minute
   opening range. The range is the 9:15, 9:20 and 9:25 bars: 1351.30 to 1363.80.
2. **`/trader-news`**: the morning's headlines and any NSE announcement for
   DEMO, in India time, and how far it is from Friday's close (1352.40).
3. **`/trader-entry`** watched DEMO bar by bar. The 9:35 bar poked above the
   high and fell back; the 9:40 bar closed exactly at it. Neither counts. At
   10:00 AM it said triggered: the 9:55 bar closed above the range, 1366.40.
4. **`/trader-exit`** set the plan before the entry: stop at the range's
   midpoint rounded down to the 0.10 tick, 1357.50, so 8.90 a share at risk;
   target one times that risk, 1375.30; flat by 15:05 either way.
5. **`/trader-timemachine`** replayed the rest of the day 10,000 times from
   DEMO's own 5-minute bars at that time of day over the previous 59 sessions
   (seed 19), and re-ran the same rule on those 59 sessions. Its verdict is in
   one line, and that is the point of looking before you risk a rupee.
6. **`/trader-budget`** with a Rs 1,00,000 account and a Rs 1,000 max loss per
   trade: the 75 % position cap binds at 54 shares (Rs 73,785.60). If the stop
   fills at 1357.50, the loss is 54 x 8.90 = Rs 480.60 plus about Rs 73.07 of
   charges: Rs 553.67. It writes a LIMIT buy for 54 DEMO at 1366.40 (MIS,
   intraday) and the stop-loss order that goes in once it fills: SL, trigger
   1357.50, limit 1354.10.
7. **`/trader-risk`** checks it against your limits: the max loss with charges,
   the daily loss limit, trades per day, reward to risk, the entry window, the
   tick, position size and cash. PASS or BLOCK, with the reason.
8. **`/trader-broker`** shows you the ticket in plain words. You type yes for
   that exact order. On paper (the default) nothing goes to Zerodha. Live,
   Claude calls Kite's `place_order` for the entry, then for the stop-loss once
   the entry fills, and Claude's own "Allow once / Deny" card asks you each time.
9. **`/trader-alerts`** rings on the stop, the target and at 15:05 to get out
   of whatever is still open, well before Zerodha's own auto square-off.

## The tools

All standard-library Python. The data tools read public endpoints and are
polite about it.

```bash
python3 trader-scanner/scan.py universe                                # the Nifty 500, from NSE
python3 trader-scanner/scan.py universe --fno                          # or the F&O stocks, --all for every NSE stock
python3 trader-scanner/scan.py daily                                   # pre-market setups on daily closes
python3 trader-scanner/scan.py orb --at 10:00                          # opening-range breakouts so far
python3 trader-news/news.py --tickers RELIANCE TCS --hours 12          # headlines, NSE announcements, today's move
python3 trader-entry/entry.py check RELIANCE --setup orb               # waiting / triggered / expired
python3 trader-entry/entry.py watch RELIANCE --every 30                # bar by bar until it triggers
python3 trader-exit/exit.py plan --stop or-mid --target 1R --write     # -> trader/plan.json
python3 trader-timemachine/timemachine.py run --write                  # 10,000 replays + the history
python3 trader-budget/size.py --write                                  # -> trader/orders.json
python3 trader-risk/check.py                                           # PASS / BLOCK per order
python3 trader-broker/ticket.py show                                   # the tickets, READY or not
python3 trader-alerts/alerts.py clock                                  # NSE open or closed, next open
python3 trader-alerts/alerts.py add --from-plan                        # stop, target, flat by 15:05
python3 trader-alerts/alerts.py check                                  # rings what hit, once
python3 trader-analyst/facts.py RELIANCE                               # quarters, growth, EPS
python3 trader-insider/insider.py pit --days 7                         # insider trades this week
python3 trader-insider/insider.py deals                                # today's bulk and block deals
```

Times are India time (IST) everywhere, and `--now "2026-10-19 10:15"` pins the
clock for a replay. Symbols are plain NSE symbols (`RELIANCE`, `M&M`,
`BAJAJ-AUTO`); the tools add Yahoo's `.NS` themselves.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The tests run offline, on fixtures. **The market data in them is synthetic.**
When this edition was written its build machine could not reach Yahoo Finance
or the NSE, so instead of passing off made-up numbers as real ones, the
fixtures say what they are: `tests/fixtures/make_synthetic.py` builds 60
sessions of 5-minute bars for a made-up stock, DEMO, from a seeded random walk
(same seed, same files, on any machine), and every file built that way has
`synthetic` in its name. The NSE files (index lists, announcements, results,
insider disclosures, deals) follow the shapes of real NSE responses, with
made-up rows. They check the method and every number in the example above.
They do not prove the live endpoints still answer the way they did: run the
tools against the live sites before you rely on them.

## Fine print

**Not financial advice.** This is software that reads public data and does
arithmetic. Neither it nor Claude is a SEBI-registered investment adviser or
research analyst. It does not know your situation and it cannot see the
future. SEBI's own study found that 7 in 10 individual intraday traders in the
cash segment lost money in FY23. You can lose money. Decide for yourself, and
talk to a SEBI-registered adviser if you need advice.

**No trade goes through until you say yes.** Only `/trader-broker` can lead to
an order, and only after `/trader-risk` passes that exact order and you type
yes for it in the chat. Set your Kite order tools to "Needs approval" in Claude
(Customize, Connectors, tool permissions), never "Always allow", so Claude's
own "Allow once / Deny" card is a second check. No F&O, options, MTF or margin,
short selling or leverage unless you turn them on.

**Paper first.** `"paper": true` is the default in `config.example.json`, and
on paper no Kite order tool is called at all: the pack keeps its own simulated
ledger. Zerodha's hosted Kite MCP server is read only (prices, positions,
orders). Placing orders through Claude needs the self-hosted server with your
own Kite Connect app, and under SEBI's retail-algo rules Zerodha only accepts
API orders from the static IP registered on that app. Keys live in your Claude
Desktop config or environment variables, never in the chat.

**Intraday on the NSE.** The normal market runs 9:15 AM to 3:30 PM IST. From
3 August to 6 December 2026, F&O stocks stop continuous trading at 3:15 PM for
the closing auction; Zerodha squares off open MIS positions on its own a few
minutes before that (and charges for it). That is why the default flat-by time
is 15:05. The holiday list in the tools covers 2026; NSE publishes the next
year's list in December, so check nseindia.com for dates after that.

**Charges are part of the loss.** Brokerage, STT, the exchange transaction
charge, the SEBI fee, stamp duty and GST are estimated from Zerodha's
published intraday rates and counted inside your max loss per trade. The
contract note is the real number.

**The Time Machine is a simulation, not a prediction.** It is built from the
stock's own last 60 days of 5-minute bars. The future can do things those 60
days never did: a results day, a circuit, a block deal, a headline. Treat its
odds as a reality check, never as a promise.

**A stop is a plan, not a guarantee.** A stop-loss order can fill well past its
trigger in a fast market or a gap, and a stop-limit order may not fill at all
if the price jumps past its limit. The max loss shown is the loss at the stop.

**The data is free and unofficial.** Yahoo Finance's chart endpoint is not an
official API: it can lag, change or rate-limit you. The NSE website blocks
many scripts; when it refuses, the tools say so and name the page to open in
your browser. Check anything that matters against Kite before you act on it.

**Insider trades are public, not inside information.** Insiders must disclose
their trades to the company within two trading days, and the company to the
exchange within two more (SEBI's insider-trading rules). The insider tracker
reads those public disclosures fast. A promoter buying is not a buy signal.

**Taxes.** Intraday equity gains are treated as speculative business income in
India and taxed at your slab rate; speculative losses can only be set off
against speculative income. Nothing here is tax advice.

## Files

```
trader-scanner/     SKILL.md, scan.py
trader-news/        SKILL.md, news.py
trader-entry/       SKILL.md, entry.py
trader-exit/        SKILL.md, exit.py
trader-timemachine/ SKILL.md, timemachine.py
trader-risk/        SKILL.md, check.py
trader-budget/      SKILL.md, size.py, config.example.json, targets.example.json
trader-broker/      SKILL.md, ticket.py, claude_desktop_config.example.json
trader-alerts/      SKILL.md, alerts.py, alerts.example.json
trader-analyst/     SKILL.md, facts.py, metrics.example.json
trader-insider/     SKILL.md, insider.py
tests/              unittest suite + offline fixtures (synthetic market data)
```

MIT. India edition by [Saitech Systems](https://github.com/saitech-systems),
built on the original by [Jake Schincariol](https://opusjake.ai).
