---
name: trader-news
description: >-
  News Monitor. Tracks breaking news on every NSE stock you watch and flags
  what moves it: Yahoo Finance India headlines per symbol, each company's
  corporate announcements to the NSE (financial results, board meeting
  outcomes, CFO, director or auditor changes and resignations, credit
  ratings, defaults and insolvency, pledges, clarifications on price
  movement, acquisitions) and how far each stock is from the previous close.
  Shows only what is new since the last check, newest first, in India time
  (IST), tags each item (earnings, analyst, deal, halt, regulatory, macro...)
  and never repeats one. Use when the user says "any news on Reliance",
  "why is TCS moving", "what's the news on my watchlist", "morning briefing",
  "pre-market news", "monitor the news", "did anything break", "check the
  NSE announcements", "any corporate announcements", "is there a results
  date", or after /trader-scanner finds setups.
argument-hint: "[NSE symbols]"
---

# trader-news

A chart can look perfect and still be about to gap on a results date, a
surveillance action or a promoter pledge. Before a setup becomes a trade, this
skill reads the two feeds that carry those first, for every stock on the
watchlist, and tells you only what is new. It also prints how far each stock
has moved today, so a headline and a move sit side by side.

Tool in this folder (standard library Python, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py"                         # watchlist + plan + portfolio
python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers RELIANCE TCS --hours 12
python3 "${CLAUDE_SKILL_DIR}/news.py" --no-nse --json
```

Symbols are NSE symbols: `RELIANCE`, `M&M`, `BAJAJ-AUTO` (`RELIANCE.NS` and
`NSE:RELIANCE` work too). A BSE-only listing is refused. `--now "2026-10-19
10:15"` pins the clock in India time; `--offline DIR` reads saved files.

## What it reads and writes

| file / feed | what |
| --- | --- |
| `--tickers`, else `trader/watchlist.json` | the scanner's hits (trader-scanner) |
| `trader/plan.json` | the trade you are planning (trader-exit) |
| `trader/portfolio.json` | what you hold (trader-broker records fills there) |
| Yahoo Finance headline RSS | `https://feeds.finance.yahoo.com/rss/2.0/headline?s=RELIANCE.NS&region=IN&lang=en-IN` |
| NSE corporate announcements | `https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=RELIANCE&from_date=...&to_date=...`: what the company told the exchange |
| Kite Connect quote (your API, read only) | the move: live last price against the previous close, one request for every symbol |
| Yahoo Finance chart endpoint | the move without a Kite session (unofficial, can lag) |
| `trader/config.json` | `data_source` (auto, kite or yahoo) for the move |
| `trader/state/news.json` | what has been shown (kept 30 days) |

nseindia.com sits behind bot protection. The tool visits the home page first
for its cookies, sends browser headers and waits a second between NSE calls.
If NSE still refuses (HTTP 401 or 403, or a web page instead of data), the
tool says so once in a `note:` line, names the announcements page to open in a
browser (`nseindia.com/companies-listing/corporate-filings-announcements`),
skips NSE for the rest of the run and carries on with the headlines. It never
retries in a loop. When that happens, open the page yourself (or ask the user
to) before saying there were no filings.

**Data source for the move.** `--source auto|kite|yahoo`, else `data_source`
in `trader/config.json`, else `auto`: Kite when today's Kite Connect session
is saved (trader-broker's `kite.py` logs in; Zerodha ends it at 6:00 AM IST),
else Yahoo with a `note:` that there is no Kite session. With Kite, the price
lines sit under "Prices: Kite, live." and come from ONE read-only request to
`api.kite.trade/quote` for every symbol. A refused session is one `note:`
line and the headlines carry on. Headlines and NSE announcements do not
change with the source. Never ask for or repeat the Kite key, secret or token.

## The rules

- **Headlines are leads, not facts.** Before telling the user something
  happened, open the story or the filing and read it. Quote the source and
  link it. Never summarise a paywalled story you could not read: say so.
- **Filings beat headlines.** If an NSE announcement and a headline
  disagree, the announcement is what the company told the exchange under
  SEBI's disclosure rules.
- **Say what it touches.** Every item lists the symbols it touches (by name,
  `(DEMO)`, `NSE: DEMO` or `DEMO.NS`) and its tags: earnings, analyst, legal,
  deal, capital, people, risk, halt, regulatory, macro. Filings marked `!`
  carry an important category: financial results, a board meeting outcome, a
  director, officer or auditor change or resignation, a credit rating, a
  default or insolvency, fraud, a pledge of shares, a clarification on price
  or volume, or an acquisition or merger. A generic "Updates" filing is read
  by its text.
- **MOVING** marks a stock 3 % or more from the previous close (`--move-pct`).
  A move with no headline is worth saying out loud too, and so is a stock
  near its circuit limit.
- **No predictions.** Report what was published and how far the price is from
  the previous close. Never say what the stock will do next.

## Steps

1. Run the check: `python3 "${CLAUDE_SKILL_DIR}/news.py"`. Exit code 1 means
   nothing new. `--all` shows items already seen, `--json` gives everything
   machine readable, `--dry-run` leaves the state alone.
2. Read the items that matter: every `!` filing, anything tagged earnings,
   halt, legal, regulatory or risk, and every MOVING symbol first.
3. Write a short brief, per symbol: what happened, the source and link, the
   move, and one line on why it matters to the setup (for example "results
   are on Thursday: an overnight gap can jump past any stop").
4. If a story changes the picture, say so before `/trader-entry` and
   `/trader-exit`: a results date inside the trade, a surveillance action,
   an offering, a resignation. Never act on a headline alone.

During the session: `/loop 15m /trader-news`, or a scheduled task each
morning before the 9:15 open.

## Example (synthetic test data)

Monday 2026-10-19, DEMO (a made-up NSE stock) broke its opening range at
10:00 AM IST. The check at 10:15 over the last six hours finds seven
headlines and two NSE filings, newest first. It leads with "Demo Industries
shares rise after CRISIL reaffirms its credit rating" (10:05) and the CFO's
resignation, both as a headline (9:48) and as an `!` NSE filing (9:41,
"director, officer or auditor change"). Further down: the credit rating
filing (`!`, 9:02), a stake purchase in talks (8:30, deal) and "Demo
Industries to announce Q2 FY27 results on Thursday" (8:04, earnings). The
brief: "DEMO reports on Thursday 22 October, so it can gap overnight; today's
trade is in and out before the close. The CFO has resigned: the filing is
on NSE." The price line reads
`DEMO       1369.85 +1.29% vs the previous close 1352.40 at 10:49 IST`.
When a symbol has no news, say so rather than padding the brief.

## What it never does

- Never invents or embellishes a story, a quote or a number.
- Never trades on news, and never tells the user to buy or sell because of a
  headline.
- Never hammers a server: one request per feed per symbol, a pause between
  calls (a second between NSE calls), and no retries after NSE refuses.

## Safety

Education and tooling, not financial advice. Neither this pack nor Claude is
a SEBI-registered investment adviser or research analyst. This skill only
reads and reports. Nothing places an order except `/trader-broker`, after
`/trader-risk` passes and the user types an explicit yes for that order.
