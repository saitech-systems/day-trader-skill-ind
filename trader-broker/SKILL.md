---
name: trader-broker
description: >-
  Order Executor. Takes approved NSE orders to Zerodha, paper trading by
  default, never without your yes. Shows each order as a plain ticket (the
  LIMIT buy, its SL stop-loss placed only after the buy fills, the target
  that is watched, the max loss with charges) and records your yes word for
  word. Live, you place that exact order yourself in Kite, and the pack reads
  it back with your own Kite Connect API key, read only: a daily Kite login,
  quotes, margins, positions, the order book and a portfolio sync. It never
  places, changes or cancels an order through the API (an optional Kite MCP
  route puts Claude's Allow once card first). Builds the ticket that closes a
  day trade by your flat-by time and logs each trade's max loss for
  trader-risk. Use when the user says "place the order", "buy it", "log in
  to Kite", "connect my Kite API", "did my order fill", "match my order",
  "sync my positions", "what are my margins", "square off", "get me out",
  "set up paper trading", or after /trader-risk passes.
argument-hint: "[order number]"
---

# trader-broker

The Order Executor is the only skill in this pack that can lead to a real
order, so it is the strictest. An order reaches the NSE only after three
separate gates, every time, for every order:

1. **trader-risk passed this exact order today** (matched by fingerprint).
2. **The user typed an explicit yes for this one order** in the chat.
3. **The user places it themselves in Kite** (the default, `"order_route":
   "manual"`), or, on the optional Kite MCP route, **clicks Allow once** on
   Claude's own permission card, which appears because the Kite order tools are
   set to "Needs approval".

Two yeses from the user, one PASS from the rules. No yes, no order. The
user's own Kite Connect API key is used **read only**: it reads prices, the
account and the order book, and never sends an order.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show                     # every ticket, READY or not
python3 "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "yes"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" match --index 1          # the order you placed in Kite, from its order book
python3 "${CLAUDE_SKILL_DIR}/ticket.py" match --index 1 --leg stop
python3 "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --status placed --filled-qty 54 --filled-price 1366.40   # paper
python3 "${CLAUDE_SKILL_DIR}/ticket.py" fill --symbol DEMO --side sell --qty 54 --price 1375.30
python3 "${CLAUDE_SKILL_DIR}/ticket.py" flatten --symbol DEMO --qty 54 --price 1371.20
python3 "${CLAUDE_SKILL_DIR}/ticket.py" log

python3 "${CLAUDE_SKILL_DIR}/kite.py" login                      # the daily Kite login link
python3 "${CLAUDE_SKILL_DIR}/kite.py" session --url "<redirect>" # the USER runs this one (with ! in Claude Code)
python3 "${CLAUDE_SKILL_DIR}/kite.py" status --check
python3 "${CLAUDE_SKILL_DIR}/kite.py" quote DEMO --write         # live prices -> trader/prices.json
python3 "${CLAUDE_SKILL_DIR}/kite.py" orders --write             # the order book -> trader/state/kite-orders.json
python3 "${CLAUDE_SKILL_DIR}/kite.py" positions                  # and: margins, trades, instruments DEMO
python3 "${CLAUDE_SKILL_DIR}/kite.py" sync --write               # positions and cash -> trader/portfolio.json
python3 "${CLAUDE_SKILL_DIR}/kite.py" candles DEMO --days 60 --out DEMO.json.gz   # for any tool's --offline
```

`ticket.py` never talks to a broker. It renders tickets, checks that a reply
is a real yes for that order, matches what you placed against Kite's order
book, builds the flatten ticket, and keeps the log in
`trader/state/broker.json`. `kite.py` reads Kite and writes local files only.
`--now "2026-10-19 10:00"` pins the clock (India time) for both.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget writes it; `flatten` replaces it) |
| `trader/state/risk.json` | trader-risk's verdicts; only a PASS from today is READY |
| `trader/state/broker.json` | every yes, no, placed, failed, stop-loss leg and fill, with each entry's max loss and Zerodha's order id |
| `trader/portfolio.json` | cash and shares, updated by `fill`, `result --filled-qty`, `match` and `kite.py sync` (charges are not deducted: the contract note has them) |
| `trader/plan.json` | the flat-by time for `flatten` |
| `trader/prices.json` | a fresh price for `flatten`'s limit; `kite.py quote --write` fills it |
| `trader/state/kite-session.json` | today's Kite session, written by `kite.py session`, readable by the user only; ends 06:00 IST |
| `trader/state/kite-orders.json` | Kite's order book, written by `kite.py orders --write`, read by `match` |
| `trader/state/kite-instruments-NSE.json` | Kite's NSE equity list (token, tick size, lot), cached for the day |

## How a day trade goes to Zerodha

Zerodha has no bracket orders. trader-budget writes one entry as two orders
under one yes:

1. the **buy**: LIMIT, product MIS (intraday), exchange NSE, validity DAY;
2. **after it fills** (COMPLETE in Kite's order book), the **stop-loss**: an SL
   sell for the same shares, trigger at the plan's stop, limit a little under.
   Never before the fill: a triggered sell with no shares would open a short.

The target is not sent: a target limit and a stop for the same shares could
both fill and leave a short. trader-alerts watches the target and the stop,
and `flatten` exits. LIMIT and SL are the defaults because Zerodha wants market
protection on MARKET and SL-M orders sent through its API.

## Paper first (the default)

With `"paper": true` nothing reaches Zerodha and Claude calls no Kite order
tool. After the user's yes, `ticket.py approve` says so, and Claude records the
paper order and its fill at the price the market gives (`kite.py quote DEMO`,
or `alerts.py quote DEMO --write`): a LIMIT buy fills once the price trades at
or under its limit. `result --leg stop --status placed` records the paper
stop-loss, and trader-alerts watches it. Indian brokers do not offer a paper
account, so this ledger is the paper account. `match` refuses on paper: there
is nothing at Zerodha to match.

## Setup A: your Kite Connect API key (read only)

This is how the pack reads live data and your account. It never places an
order with it.

1. The user makes an app on developers.kite.trade. The **Connect** plan carries
   the market data (live quotes and historical candles); the free Personal plan
   has none, and then the data tools stay on free Yahoo data. The redirect URL
   can be `http://127.0.0.1`: nothing has to answer there.
2. The user puts the app's API key and API secret in their environment, in the
   shell profile or the environment Claude Code or Claude Desktop starts with:
   `export KITE_API_KEY=...` and `export KITE_API_SECRET=...`. **Keys never go
   in the chat**, and never in a file in the project. Never ask for them, never
   read them back, and if the user pastes a key or secret anyway, tell them to
   delete that message and regenerate it on developers.kite.trade.
3. Once a day (Zerodha ends every session at 06:00 IST), the login:
   - `kite.py login` prints the link. The user opens it and logs in to Kite;
     the password and TOTP go to Zerodha, never to Claude.
   - Kite sends the browser to the redirect URL with
     `?request_token=...&action=login&status=success`. The page may not load;
     the user copies the whole address.
   - **The user runs** `python3 "${CLAUDE_SKILL_DIR}/kite.py" session --url "<that
     address>"` themselves (in Claude Code, after a `!`, so it runs in their
     shell). The request token works once, for a few minutes, and is useless
     without the secret. The tool exchanges it (the one request in the pack
     that is not a read), saves the session to
     `trader/state/kite-session.json` (mode 600) and says who is logged in and
     until when. It never prints the token.
4. The user adds `trader/` to the project's `.gitignore`.
5. `kite.py status --check` confirms it with Kite. From then until 06:00 IST
   the data skills read live Kite data (`"data_source": "auto"`), and
   `kite.py quote`, `positions`, `margins`, `orders`, `trades` and `sync` work.

The static IP that SEBI's retail-algo rules ask for is for orders sent through
the API. Reading data and the account does not need it.

## Setup B (optional): Zerodha's Kite MCP server

Zerodha's own server is github.com/zerodha/kite-mcp-server (MIT, checked
against its source on 2026-10-23).

- **Hosted, read only, no keys**: `https://mcp.kite.trade/mcp`. Add it in
  Claude (Customize > Connectors > Add custom connector) or in Claude Desktop's
  config with `mcp-remote` (the `kite-hosted` block in
  `claude_desktop_config.example.json` here). Its `login` tool gives a Kite
  link; it leaves out the order tools. Useful for `get_ltp`, `get_positions`
  and `get_orders` inside the chat.
- **Self-hosted, so Claude can place orders** (`"order_route": "kite_mcp"` in
  the config): build it (Go 1.21 or newer: `git clone
  https://github.com/zerodha/kite-mcp-server && cd kite-mcp-server && go build
  -o kite-mcp-server`), add the `kite` block from
  `claude_desktop_config.example.json` with the full path to the binary, and
  have the user type their key and secret into that config file only:

```json
{
  "mcpServers": {
    "kite": {
      "command": "/full/path/to/kite-mcp-server",
      "env": {
        "APP_MODE": "stdio",
        "KITE_API_KEY": "type your Kite Connect API key here, in this file only",
        "KITE_API_SECRET": "type your Kite Connect API secret here, in this file only",
        "EXCLUDED_TOOLS": "place_gtt_order,modify_gtt_order,delete_gtt_order"
      }
    }
  }
}
```

  Restart Claude Desktop, run its `login` tool once a day, and **set the tool
  permissions** (Customize > Connectors, open kite): `place_order`,
  `modify_order` and `cancel_order` **Needs approval**, never Always allow;
  the GTT tools **Blocked**; read-only tools may be Always allow. In Claude
  Code: `"permissions": {"ask": ["mcp__kite__place_order",
  "mcp__kite__modify_order", "mcp__kite__cancel_order"]}`, never an order tool
  in `allow`. Before going live this way, check on developers.kite.trade: under
  SEBI's retail-algo framework Zerodha rejects API orders that do not come from
  the static IP registered on the Kite Connect app, and MARKET and SL-M API
  orders need market protection, which this MCP server does not send.

## Steps: the entry

1. `ticket.py show`. Only READY tickets go to the user. A ticket that is NOT
   READY or BLOCKED goes back to `/trader-risk`.
2. Show ONE ticket and ask: "Place this order? Reply yes or no." One order
   per question. Never offer "yes to all".
3. Run `ticket.py approve --index N --reply "<the user's reply, word for word>"`.
   If it prints "Not recorded", the reply was not an explicit yes for this
   order (for example "sure", "haan", "yes to all", or a yes that names a
   different stock or amount). Ask again. Never turn a reply into a yes yourself.
4. Paper: record the paper order and its fill with `result`, as above.
5. Live, manual (the default): the user places **exactly** the order the
   ticket shows in Kite (the app or kite.zerodha.com): same symbol, side,
   quantity, product, order type and price. Claude does not place it. Then
   `kite.py orders --write` and `ticket.py match --index N`:
   - COMPLETE: recorded as placed, and the fill (Kite's filled quantity and
     average price) goes into `trader/portfolio.json`;
   - OPEN: recorded as placed; run both again once it is COMPLETE;
   - REJECTED or CANCELLED with nothing filled: recorded as failed with Kite's
     reason. A failed order is not retried without a new check and a new yes.
   An order placed before the yes, or with any other quantity or price, is not
   this ticket and is never matched. Two that fit: `--order-id ID` picks one.
6. Once the buy is COMPLETE, the user places the stop-loss the ticket shows
   the same way (the same yes covers it), then `kite.py orders --write` and
   `ticket.py match --index N --leg stop`. If it is REJECTED, say so at once:
   the shares have no stop at Zerodha.
7. Live, Kite MCP route: only after "Recorded your yes", call `place_order`
   with exactly the JSON the ticket printed (Claude's card: Allow once or
   Deny), record it with `result --broker-id`, and after the fill place the
   stop-loss JSON the same way and `result --leg stop`.

## Steps: while it is open, and out by the close

- trader-alerts watches the stop, the target and the flat-by time
  (`alerts.py add --from-plan`). The SL order at Zerodha does the stopping.
- When the stop-loss fills, `kite.py orders --write` and `ticket.py match
  --index N --leg stop` record the sell (or `ticket.py fill` by hand).
- At the target, or by `flat_by` (15:05 IST), anything still open gets closed:
  1. `kite.py positions` (or `kite.py sync --write`) shows the shares held.
  2. `ticket.py flatten --symbol DEMO --qty 54` writes a sell for those shares
     to `trader/orders.json`: a LIMIT at the last price minus
     `exit_buffer_pct` (0.5%), rounded down to the tick, from `--price` or a
     fresh `trader/prices.json` (`kite.py quote DEMO --write`); with no fresh
     price, a MARKET sell with a warning.
  3. `/trader-risk`. A sell that only closes what the user holds is never
     blocked by the daily limits or the entry window.
  4. `ticket.py show`, then the user's yes for that one order, as above.
  5. The SL order still holds those shares at Zerodha. The user cancels it in
     Kite first (Orders: the TRIGGER PENDING sell), then places the sell, then
     `kite.py orders --write` and `ticket.py match --index 1`. On the MCP
     route: `get_orders`, `cancel_order` (Needs approval), `place_order`.
- Zerodha's own MIS auto square-off comes later (about 15:12 for F&O stocks in
  the closing-auction period from 3 Aug to 6 Dec 2026, 15:25 otherwise) and
  costs Rs 50 + GST an order. Flat by 15:05 beats it.

## Example (synthetic test data)

DEMO is a made-up NSE stock. On a live account, manual route, the ticket reads:

```
ORDER 1 of 1, LIVE ACCOUNT, REAL MONEY, Zerodha (Kite)
  BUY 54 DEMO, limit 1366.40, MIS intraday, NSE, good for today
  then stop-loss: SELL 54 DEMO, SL trigger 1357.50 limit 1354.10, MIS intraday, NSE, good for today, placed after the buy fills
  target 1375.30 (watched by trader-alerts, not sent to Zerodha)
  max loss about Rs 553.67 if the stop fills at 1357.50, incl. about Rs 73.07 charges (it can fill worse, or not at all, in a fast market)
  READY: trader-risk PASS, waiting for your yes
  After your yes, YOU place this exact order in Kite (the app or kite.zerodha.com); Claude
  does not place it:
    BUY 54 DEMO, limit 1366.40, MIS intraday, NSE, good for today
  ...
  Then: kite.py orders --write, and ticket.py match --index 1 (and --leg stop) records them.
```

The user replies "yes" and places the buy in Kite at 10:01. Then:

```
$ kite.py orders --write
10:01:05 COMPLETE         BUY 54 DEMO LIMIT 1366.40 MIS, filled 54 at 1366.40, id 261019000123456
$ ticket.py match --index 1
Matched Kite order 261019000123456: COMPLETE, BUY 54 DEMO, limit 1366.40, MIS intraday, NSE, good for today.
Recorded order 1 as placed (broker id 261019000123456).
Recorded the fill: BUY 54 DEMO at 1366.40, into trader/portfolio.json.
Now place its stop-loss yourself in Kite: SELL 54 DEMO, SL trigger 1357.50 limit 1354.10, MIS intraday, ...
```

On the default paper account the same ticket says "PAPER account (nothing is
sent to Zerodha)" and nothing reaches Kite.

## What it never does

- Never places an order without all three gates, and never two orders on one yes (the entry's own
  stop-loss is part of that order, never another symbol or another trade).
- Never places, changes or cancels an order through the user's Kite Connect API: `kite.py` has no code
  that could. Its one request that is not a read is the daily login exchange.
- Never places the stop-loss before the buy fills, and never sends the target as an order.
- Never places orders from a scheduled or background task: nobody is there to say yes.
- Never calls a Kite order tool on a paper account, and never matches a paper order to Zerodha.
- Never asks for, reads, stores or repeats API keys, secrets, passwords, TOTP codes, access tokens or
  request tokens; the session file is written by the user's own `kite.py session` run. Never opens
  `trader/state/kite-session.json` and never prints the environment (`env`, `echo $KITE_API_SECRET`).
- Never switches `"paper"` to false. The user does that themselves.
- Never uses GTT, F&O, MTF, cover orders or AMO on its own.
- Never promises a fill price: a LIMIT may not fill, and an SL stop can fill worse or not at all.

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
- Kite API keys and secrets live in environment variables or the Claude Desktop config, never in the chat.
