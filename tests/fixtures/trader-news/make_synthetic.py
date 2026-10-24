#!/usr/bin/env python3
"""Builds trader-news' SYNTHETIC fixtures. Not real news.

    python3 tests/fixtures/trader-news/make_synthetic.py

Two made-up NSE companies, DEMO (Demo Industries) and SAMPLE (Sample Motors),
on the morning of Monday 2026-10-19 (India time), in the exact shapes the live
sources answer with:

  yahoo-DEMO.xml, yahoo-SAMPLE.xml        Yahoo Finance headline RSS 2.0
  nse-announcements-DEMO.json, ...SAMPLE  NSE corporate-announcements JSON (a list)
  nse-announcements-BLOCKED.json          the web page NSE's bot protection sends
                                          instead of data (HTTP 403)
  chart-DEMO.json                         a copy of tests/fixtures/shared/
                                          DEMO-2026-10-19-5m-synthetic.json
  chart-SAMPLE.json                       a one-bar Yahoo chart, 3.45 % down
  kite-quote-SAMPLE-synthetic.json        the same SAMPLE price as Kite Connect's
                                          /quote answers it (DEMO's is in
                                          tests/fixtures/shared/kite/)
"""

import datetime
import json
import os
import shutil
from email.utils import format_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SHARED = os.path.join(os.path.dirname(HERE), "shared", "DEMO-2026-10-19-5m-synthetic.json")
IST = datetime.timedelta(hours=5, minutes=30)

DEMO_RSS = [  # (India time, title, description, guid)
    ("2026-10-16 18:10", "Demo Industries board to consider an interim dividend next week",
     "The board of Demo Industries will also take up the September quarter numbers.", "demo-0001"),
    ("2026-10-19 07:45", "GIFT Nifty signals a firm start; Sensex and Nifty futures edge higher",
     "Asian markets were mixed while crude held steady and the rupee opened flat.", "mkt-0002"),
    ("2026-10-19 08:04", "Demo Industries to announce Q2 FY27 results on Thursday: what to watch",
     "Demo Industries (DEMO) reports on 22 October; margins and other income are in focus.", "demo-0003"),
    ("2026-10-19 08:30", "Demo Industries in talks to acquire a stake in a battery maker, say people familiar",
     "The deal could value the battery business at about Rs 2,000 crore.", "demo-0004"),
    ("2026-10-19 09:05", "Brokerages raise Demo Industries target price ahead of results, see 12% upside",
     "Two brokerages raised their target price on NSE: DEMO before the quarter.", "demo-0005"),
    ("2026-10-19 09:20", "Stock market today: Nifty opens higher; Demo Industries, Sample Motors among top gainers",
     "Banks and autos led the early gains on the NSE.", "mkt-0006"),
    ("2026-10-19 09:48", "Demo Industries CFO steps down; company names an interim finance chief",
     "The resignation takes effect at the end of the month.", "demo-0007"),
    ("2026-10-19 10:05", "Demo Industries shares rise after CRISIL reaffirms its credit rating",
     "CRISIL reaffirmed AA+/Stable on the company's long-term debt.", "demo-0008"),
    ("2026-10-19 10:32", "Demo Industries hits the day's high as the broader market extends gains",
     "DEMO.NS traded near its high for the day.", "demo-0009"),
]
SAMPLE_RSS = [
    ("2026-10-19 08:15", "Sample Motors October sales preview: brokerages expect 8% growth",
     "Analysts expect scooter volumes to lead.", "sample-0001"),
    ("2026-10-19 09:20", "Stock market today: Nifty opens higher; Demo Industries, Sample Motors among top gainers",
     "Banks and autos led the early gains on the NSE.", "mkt-0006"),
    ("2026-10-19 09:40", "Sample Motors recalls 12,000 scooters over a brake fault",
     "The recall covers models sold since March.", "sample-0003"),
    ("2026-10-19 10:10", "SEBI issues a show-cause notice to a Sample Motors promoter entity",
     "The regulator questioned related-party disclosures.", "sample-0004"),
]

DEMO_NSE = [  # (seq_id, India time, desc, text)
    ("700000105", "2026-07-23 18:31:12", "Financial Result Updates",
     "Demo Industries Limited has informed the Exchange about Financial Results for the period ended "
     "June 30, 2026."),
    ("700000351", "2026-10-14 16:20:00", "Analysts/Institutional Investor Meet/Con. Call Updates",
     "Demo Industries Limited has informed the Exchange about the schedule of analyst and institutional "
     "investor meetings."),
    ("700000377", "2026-10-16 17:45:03", "Board Meeting Intimation",
     "Demo Industries Limited has informed the Exchange about Board Meeting to be held on 22-Oct-2026 to "
     "consider and approve the Unaudited Financial Results for the quarter ended September 30, 2026."),
    ("700000402", "2026-10-19 09:02:11", "Credit Rating",
     "Demo Industries Limited has informed the Exchange about Credit Rating: CRISIL reaffirmed AA+/Stable."),
    ("700000415", "2026-10-19 09:41:30",
     "Change in Directors/ Key Managerial Personnel/ Auditor/ Compliance Officer/ Share Transfer Agent",
     "Demo Industries Limited has informed the Exchange about the resignation of Mr. A. Kumar as Chief "
     "Financial Officer (CFO)."),
    ("700000431", "2026-10-19 10:40:00", "Updates",
     "Demo Industries Limited has submitted a clarification on a news item in the media about the "
     "acquisition of a stake in a battery maker."),
]

BLOCKED_PAGE = """<HTML><HEAD>
<TITLE>Access Denied</TITLE>
</HEAD><BODY>
<H1>Access Denied</H1>

You don't have permission to access "http&#58;&#47;&#47;www&#46;nseindia&#46;com&#47;api&#47;corporate&#45;announcements&#63;" on this server.<P>
Reference&#32;&#35;18&#46;5e3c1d17&#46;1792386421&#46;3a7f0b2
</BODY>
</HTML>
"""


def utc(ist_text):
    local = datetime.datetime.strptime(ist_text, "%Y-%m-%d %H:%M")
    return (local - IST).replace(tzinfo=datetime.timezone.utc)


def rss(symbol, rows):
    items = []
    for when, title, desc, guid in sorted(rows, key=lambda r: r[0], reverse=True):
        slug = "-".join("".join(c if c.isalnum() else " " for c in title.lower()).split()[:8])
        items.append(
            "        <item>\n"
            "            <description>%s</description>\n"
            "            <guid isPermaLink=\"false\">%s</guid>\n"
            "            <link>https://finance.yahoo.com/news/%s-%s.html?.tsrc=rss</link>\n"
            "            <pubDate>%s</pubDate>\n"
            "            <title>%s</title>\n"
            "        </item>\n" % (desc, guid, slug, guid, format_datetime(utc(when)), title))
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
            "<rss version=\"2.0\">\n"
            "    <channel>\n"
            "        <copyright>Synthetic test data, not Yahoo content.</copyright>\n"
            "        <description>Latest Financial News for %s.NS</description>\n"
            "        <language>en-IN</language>\n"
            "        <lastBuildDate>Mon, 19 Oct 2026 05:30:00 +0000</lastBuildDate>\n"
            "        <link>https://finance.yahoo.com/quote/%s.NS</link>\n"
            "        <title>Yahoo! Finance: %s.NS News</title>\n"
            "%s"
            "    </channel>\n"
            "</rss>\n" % (symbol, symbol, symbol, "".join(items)))


def nse(symbol, name, isin, rows):
    out = []
    for seq, when, desc, text in sorted(rows, key=lambda r: r[1], reverse=True):
        d = datetime.datetime.strptime(when, "%Y-%m-%d %H:%M:%S")
        stamp = d.strftime("%d%m%Y%H%M%S")
        out.append({"symbol": symbol, "desc": desc, "dt": stamp,
                    "attchmntFile": "https://nsearchives.nseindia.com/corporate/%s_%s_SYNTHETIC.pdf" % (symbol, stamp),
                    "sm_name": name, "sm_isin": isin, "an_dt": d.strftime("%d-%b-%Y %H:%M:%S"),
                    "sort_date": when, "seq_id": seq, "smIndustry": "Auto Components",
                    "orgid": None, "attchmntText": text, "bflag": None, "old_new": None, "csvName": None,
                    "exchdisstime": (d + datetime.timedelta(seconds=6)).strftime("%d-%b-%Y %H:%M:%S"),
                    "difference": "00:00:06"})
    return out


def write(name, text):
    with open(os.path.join(HERE, name), "w", encoding="utf-8") as fh:
        fh.write(text)


def main():
    write("yahoo-DEMO.xml", rss("DEMO", DEMO_RSS))
    write("yahoo-SAMPLE.xml", rss("SAMPLE", SAMPLE_RSS))
    write("nse-announcements-DEMO.json",
          json.dumps(nse("DEMO", "Demo Industries Limited", "INE000D01019", DEMO_NSE), indent=2) + "\n")
    write("nse-announcements-SAMPLE.json", "[]\n")
    write("nse-announcements-BLOCKED.json", BLOCKED_PAGE)
    shutil.copyfile(SHARED, os.path.join(HERE, "chart-DEMO.json"))
    at = int((datetime.datetime(2026, 10, 19, 10, 49, 45) - IST - datetime.datetime(1970, 1, 1)).total_seconds())
    sample = {"chart": {"result": [{
        "meta": {"currency": "INR", "symbol": "SAMPLE.NS", "exchangeName": "NSI", "fullExchangeName": "NSE",
                 "instrumentType": "EQUITY", "regularMarketTime": at, "gmtoffset": 19800, "timezone": "IST",
                 "exchangeTimezoneName": "Asia/Kolkata", "regularMarketPrice": 412.35,
                 "chartPreviousClose": 427.1, "previousClose": 427.1, "dataGranularity": "5m", "range": "1d"},
        "timestamp": [at - 285],
        "indicators": {"quote": [{"open": [413.0], "high": [413.5], "low": [411.9], "close": [412.35],
                                  "volume": [88000]}]}}], "error": None}}
    write("chart-SAMPLE.json", json.dumps(sample, separators=(",", ":")) + "\n")
    kite = {"status": "success", "data": {"NSE:SAMPLE": {
        "instrument_token": 9000003, "timestamp": "2026-10-19 10:49:45", "last_trade_time": "2026-10-19 10:49:45",
        "last_price": 412.35, "last_quantity": 10, "buy_quantity": 41200, "sell_quantity": 52300,
        "volume": 1288400, "average_price": 415.02, "oi": 0, "oi_day_high": 0, "oi_day_low": 0,
        "net_change": -14.75, "lower_circuit_limit": 341.7, "upper_circuit_limit": 512.5,
        "ohlc": {"open": 424.0, "high": 425.1, "low": 410.6, "close": 427.1}}}}
    write("kite-quote-SAMPLE-synthetic.json", json.dumps(kite, separators=(",", ":")) + "\n")
    print("wrote trader-news fixtures to %s" % HERE)


if __name__ == "__main__":
    main()
