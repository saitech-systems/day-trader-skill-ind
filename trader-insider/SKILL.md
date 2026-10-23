---
name: trader-insider
description: >-
  The Insider Tracker. Watches the trades company insiders must disclose under
  SEBI's insider trading rules (PIT): promoters, the promoter group,
  directors, key managerial personnel and designated employees buying,
  selling or pledging shares, read from NSE's disclosures as soon as they
  post. Also reads NSE's bulk and block deals (big trades by named clients),
  alerts once per new row, and turns each into plain words (who, category,
  buy, sell or pledge, market or off market, shares, rupees, holding before
  and after, trade and disclosure dates, how many trading days it took).
  Highlights promoter and director open-market trades and flags rows in
  stocks you hold. SAST takeover filings and BSE-only stocks have a manual
  route. Use when the user says "promoter buying", "insider trades in
  RELIANCE", "did the promoters sell", "insider trading disclosures", "bulk
  deals today", "block deals", "who bought DEMO in a bulk deal", "SAST",
  "pledged shares", "alert me when insiders trade", or "run the insider skill".
argument-hint: "[pit | deals | new] [--symbol SYMBOL]"
---

# trader-insider

People inside a listed company have to report their trades in its shares. Under
SEBI's insider trading rules (PIT), a promoter, director, key managerial person
or designated employee tells the company within 2 trading days of a trade, and
the company tells the exchange within 2 trading days after that. NSE publishes
every disclosure, and every trading day it also publishes the bulk and block
deals: big trades by named clients. This skill reads both, notices what is new,
and says it in plain words.

It is fast. It is not early. By the time anyone can read a disclosure the trade
is up to 4 trading days old, often more, and a bulk deal is printed after the
session. There is no inside information here: only public disclosures, read
quickly. A promoter's buy is not a buy signal. Say that every time.

Tool in this folder (standard library Python, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/insider.py" --help
```

## What it reads and writes

- Reads NSE's insider trading disclosures,
  `https://www.nseindia.com/api/corporates-pit?index=equities&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY`
  (the page: https://www.nseindia.com/companies-listing/corporate-filings-insider-trading).
- Reads today's deals from NSE's daily files,
  `https://nsearchives.nseindia.com/content/equities/bulk.csv` and `block.csv`, and
  older ones from `https://www.nseindia.com/api/historicalOR/bulk-block-short-deals`
  (the page: https://www.nseindia.com/report-detail/display-bulk-and-block-deals).
- Writes `trader/state/insider.json` (the rows already shown, so each alerts
  once; entries older than 90 days are forgotten).
- Reads `trader/portfolio.json` (if it exists) to flag rows in what you hold.

www.nseindia.com sits behind bot protection that blocks many scripts. The tool
opens the home page first for cookies, sends browser headers and waits a
second between calls. If NSE still refuses, it prints one line naming the page
to open: ask the user to open it in their browser and paste the table, and
write it up in the same shape. The daily CSV files need no cookies. The field
names of the insider answer were the best known ones when this was written
(no live answer could be read), so every field is read defensively: if a row
comes back blank, read the disclosure on the page.

## The rules

- **Quote the disclosure, not a tracker.** Every row comes from NSE, with the
  disclosure's link (its XBRL file when there is one). Third-party apps and
  social posts are fine for browsing but drop rows and mix up dates. NSE wins.
- **Category matters.** A promoter or director buying in the open market is
  the row people mean by "insider buying". An ESOP allotment, an employee's
  sale to pay tax, a gift or an inter-se transfer between promoters is not a
  view on the price. The tool marks open-market trades by a promoter, the
  promoter group, a director or KMP with `**`.
- **A pledge is not a trade.** Pledged shares are loan collateral. A promoter
  pledging more can matter (if the lender sells, the stock can fall), but say
  "pledged", never "sold". A pledge invoked is the lender taking the shares.
- **Say the delay.** Each row shows how many trading days passed between the
  trade and NSE's disclosure. Over 4 is flagged SLOW, with who was slow: the
  insider (over 2 to tell the company) or the company (over 2 to tell NSE).
- **Rupees are averages.** The price a share is the value over the shares, an
  average across the trade dates, not a fill price.
- **Bulk and block deals are big trades, not insider trades.** A bulk deal is
  one client trading more than 0.5% of a company's listed shares in a day,
  disclosed by NSE that evening. A block deal is a single large trade (at
  least Rs 10 crore under SEBI's 2023 framework) in the separate block-deal
  windows before the open and in the early afternoon. Both name the client;
  neither says why.

## Step 1: insider trades

```bash
python3 "${CLAUDE_SKILL_DIR}/insider.py" pit                                # the last 7 days
python3 "${CLAUDE_SKILL_DIR}/insider.py" pit --symbol RELIANCE --days 90    # one company
python3 "${CLAUDE_SKILL_DIR}/insider.py" pit --category insiders --market-only
python3 "${CLAUDE_SKILL_DIR}/insider.py" pit --category promoters --action pledge
python3 "${CLAUDE_SKILL_DIR}/insider.py" pit --holdings                     # stocks you hold
```

`--category` takes promoter, promoter-group, promoters (both), director, kmp,
employee, relative, insiders (promoters, directors and KMP) or all, and
repeats. `--from 2026-10-01 --to 2026-10-19` sets the window (a year at most).

## Step 2: bulk and block deals

```bash
python3 "${CLAUDE_SKILL_DIR}/insider.py" deals                     # the latest session, both kinds
python3 "${CLAUDE_SKILL_DIR}/insider.py" deals --kind block --min-value-cr 50
python3 "${CLAUDE_SKILL_DIR}/insider.py" deals --symbol DEMO --from 2026-10-01 --to 2026-10-19
```

Without dates it reads NSE's daily files; with dates, NSE's history.

## Step 3: watch for new ones

```bash
python3 "${CLAUDE_SKILL_DIR}/insider.py" new
python3 "${CLAUDE_SKILL_DIR}/insider.py" new --symbol DEMO --all
```

Shows only rows not seen at the last check and records them in the state file.
Exit code 1 means nothing new. By default it shows the notable rows: open-market
trades by key people, promoter pledges, every block deal, bulk deals of
Rs 10 crore or more (`--min-value-cr`), and anything in a stock you hold or
name with `--symbol`. `--all` shows everything. Disclosures post through the
day and evening and the deal files after the session, so checking every hour on
trading days, plus once in the evening, is plenty: `/loop 1h` or a scheduled task.

## Example (synthetic test data)

The tests' made-up company DEMO, checked on Monday 2026-10-19:

```
NEW insider trade **
    DEMO: Ravi Kumar Sample (director) sold 8,000 shares, market sale, Rs 1,08,92,000 (1.09 crore),
    about 1361.50 a share, holding 0.85% -> 0.83%, traded 2026-10-15, disclosed 2026-10-19 10:05,
    2 trading days after the trade
NEW bulk deal
    BULK 2026-10-19 DEMO: EXAMPLE GROWTH FUND LLP bought 6,50,000 shares at 1362.15
    = Rs 88,53,97,500 (88.54 crore)
```

On the Friday before, the promoter Asha Demo disclosed buying 25,000 shares in the market
(Rs 3.40 crore, holding 41.20% to 41.25%), and a KMP at another made-up company
disclosed a sale 9 trading days late. Then: "These are public disclosures. They
say what a director sold last Thursday and what a fund bought today, not what
anyone will do next."

If the user wants to act on it, it goes the long way: a disclosure is days old,
so it is a reason to look at a chart, not a setup. `/trader-scanner` and
`/trader-entry` decide if there is a trade today; then `/trader-budget` sizes
it, `/trader-risk` checks it, and `/trader-broker` places it only after their yes.

## SAST and BSE: the manual route

- **SAST (takeover code) filings.** When anyone crosses 5% of a company, or a
  5% holder's stake moves by 2% or more, or a promoter pledges shares, the
  filing goes under SEBI's takeover regulations (SAST), not PIT. This tool does
  not read them. The user opens Corporate Filings on nseindia.com (the
  takeover regulation pages) or the company's page on bseindia.com, and pastes
  the table; write it up in the same shape, with the link and the dates.
- **BSE-only stocks.** This pack trades NSE symbols. For a company listed only
  on BSE, the user reads its disclosures on bseindia.com and pastes them.

## What it never does

- Never says or implies an insider has inside information, or that copying
  promoters or big clients beats the market. No return claims.
- Never calls a pledge a sale, an ESOP a market buy, or a bulk deal an insider trade.
- Never turns a disclosure into an order: it reads and reports.
- Never automates a login, a captcha or a site's terms, and never hammers
  NSE: one call per source, a pause between calls, no retry loops.

## Safety

- Education and tooling, not financial advice. Neither this pack nor Claude is a SEBI-registered
  investment adviser or research analyst. Nothing in this pack is a recommendation to buy or sell.
- Paper trading by default (`"paper": true` in `trader/config.json`): nothing reaches Zerodha.
- Nothing places an order except `/trader-broker`, and only after `/trader-risk` passes that exact
  order AND the user types an explicit yes for it in the chat, AND the Kite order tools are set to
  "Needs approval" in Claude (Customize > Connectors > tool permissions) so Claude's own "Allow once /
  Deny" card appears too. Never "Always allow" an order tool.
- No F&O, options, MTF or margin, short selling or leverage unless the config allows it and the user
  confirms.
- Insider disclosures and bulk and block deals are PUBLIC records, posted days after the trade or after
  the session. This skill reads them fast; it has no inside information, and it says so.
