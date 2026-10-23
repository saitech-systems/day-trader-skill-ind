---
name: trader-analyst
description: >-
  The Earnings Analyst. Reads an NSE-listed company's reported quarterly
  results straight from NSE (the results comparison behind nseindia.com's
  financial results page, the standalone and consolidated results filings
  with their XBRL links, and the board meetings called to approve results)
  and sums up what changed: revenue, profit before tax, net profit, basic and
  diluted EPS, margins, quarter by quarter, each against the same quarter a
  year earlier, with the last four quarters. Flags other income and
  exceptional items, handles banks, names the next results date. Reads an
  earnings call transcript when you paste one. Use when the user says "how
  were Reliance's results", "analyse the Q2 results", "what changed this
  quarter", "is revenue growing", "check the quarterly numbers", "compare
  margins", "when are TCS results", "is there a board meeting for results",
  "sum up the con call", or before trading a stock around its results.
argument-hint: "[NSE symbols]"
---

# trader-analyst

Results move Indian stocks more than anything else on the calendar, and the
press release is written to put the best number first. This skill reads the
numbers the company filed with the NSE, lines them up quarter by quarter, and
says plainly what changed. For a day trader it answers two questions before a
trade: what did the last results say, and is the next board meeting inside
this trade?

Tool in this folder (standard library Python, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/facts.py" --help
```

## What it reads and writes

- Reads nseindia.com's own JSON (no key):
  - `api/results-comparision?symbol=RELIANCE` (NSE's spelling): the last few
    quarters' profit and loss, in Rs lakh, printed in Rs crore. These are the
    **standalone** numbers: the company on its own, without subsidiaries.
  - `api/corporates-financial-results?index=equities&period=Quarterly&symbol=RELIANCE`:
    the results filings, standalone and consolidated, audited or not, with
    their XBRL links.
  - `api/corporate-board-meetings?index=equities&symbol=RELIANCE`: board
    meetings, past and announced. The next one called to approve results is
    the next results date.
- nseindia.com sits behind bot protection: the tool visits the home page
  first for cookies, sends browser headers and waits a second between calls.
  If NSE refuses anyway it says so plainly and names the page to open in a
  browser (`nseindia.com/companies-listing/corporate-filings-financial-results`).
- Writes `trader/metrics.json` with `--metrics`: one row per company (last
  four quarters' revenue, net profit and diluted EPS, the latest margins and
  growth, the next results date), for your notes or a spreadsheet.
  `metrics.example.json` in this folder is a run on the synthetic DEMO and
  DEMOBANK test fixtures, not real companies.
- Earnings calls: the tool does not fetch transcripts. If the user pastes one
  (or the company's investor page has it), read it and sum up what management
  said changed: guidance, margins, demand, anything they declined to answer.

## The rules

- **Filed numbers first.** The table comes from what the company filed with
  the NSE. Use the press release and the investor presentation for context
  (guidance, segment colour), and say which numbers came from where.
- **Standalone or consolidated: say which.** The table is standalone. For a
  company with big subsidiaries the group (consolidated) numbers can tell a
  different story: the tool prints the consolidated filing's XBRL link; open
  it before summarising a group.
- **Compare like with like.** Growth is against the same quarter a year
  earlier (Q1 FY27 against Q1 FY26). Quarter-on-quarter changes are shown,
  but many businesses are seasonal. India's financial year runs April to
  March: the quarter ending June 30 is Q1.
- **Say when the data lags.** NSE's results comparison can trail a filing.
  When the newest filing is later than the newest quarter in the table, the
  tool prints a NOTE with the link: read the filing itself before
  summarising.
- **Flag one-offs.** Other income of a quarter of profit before tax or more
  (interest, dividends, treasury gains), exceptional items, and a net profit
  above profit before tax (a tax credit) are all called out. Banks report
  differently: their revenue is total income, the operating margin is blank,
  not zero, and gross NPA is shown when NSE has it.

## Steps

1. Run it for the companies in question (or the symbols on
   `trader/watchlist.json`):

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/facts.py" RELIANCE
   python3 "${CLAUDE_SKILL_DIR}/facts.py" RELIANCE TCS INFY --metrics trader/metrics.json
   ```

   `--quarters 4` shows fewer rows, `--json` everything machine-readable,
   `--no-filings` skips the filings list and the board meetings, `--now
   "2026-10-19 10:15"` pins the date in India time.
2. Open the consolidated filing (the XBRL link) and the results press release
   when the numbers need context, and read them.
3. Write the summary: one line on the quarter (revenue, net profit, EPS,
   growth), one on margins, one on anything unusual (other income, an
   exceptional item, data that lags, standalone against consolidated), and the
   links. Plain words, real numbers, no adjectives the numbers do not support.
4. If the next results board meeting falls inside the planned trade (an
   overnight hold, or results due before the open), say it before
   `/trader-exit` sets the stop: a stop does not protect against a gap, the
   stock can open past it.

## Example (synthetic test data)

`facts.py DEMO --now "2026-10-19 10:15"` on the made-up DEMO fixtures:
quarter ending 2026-06-30 (Q1 FY27, unaudited), revenue Rs 10,215.0 crore,
+12.0% against a year earlier, net profit Rs 1,305.0 crore (+35.4%), diluted
EPS Rs 19.50 (+35.3%); profit before tax margin 17.1%, operating margin 13.7%,
net margin 12.8%; last four quarters Rs 40,182.0 crore revenue and Rs 66.14
EPS. The tool flags that other income is 26.8% of profit before tax, so part
of the jump came from outside the business. The table is standalone; the
consolidated filing of 2026-07-23 is linked. Next results: a board meeting on
Thursday 2026-10-22, in 3 days. So the summary ends: "Results are three days
away. A day trade that is flat by 15:05 is not exposed to them; anything held
overnight into Thursday is."

For the made-up LAGCO the tool noted that NSE's results comparison stopped at
2026-03-31 while the June quarter was already filed on 2026-07-29, so the
summary read that filing directly.

## What it never does

- Never says a stock is cheap, expensive, a buy or a sell. It reports what
  the company filed.
- Never invents a number the filings do not have, or passes standalone
  numbers off as the group's.
- Never hammers nseindia.com: a second between calls, and no retries after
  NSE refuses.

## Safety

Education and tooling, not financial advice. Neither this pack nor Claude is
a SEBI-registered investment adviser or research analyst. This skill only
reads filings. Nothing places an order except `/trader-broker`, after
`/trader-risk` passes and the user types an explicit yes for that order.
