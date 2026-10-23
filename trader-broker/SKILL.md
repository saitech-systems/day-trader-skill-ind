---
name: trader-broker
description: >-
  Order Executor. Sends approved NSE orders to Zerodha through the Kite MCP
  server, paper trading by default, never without your yes. Shows each order
  as a plain ticket (the LIMIT buy, its SL stop-loss sell placed only after
  the buy fills, the target that is watched and not sent, and the max loss at
  the stop with charges) plus the exact JSON for Kite's place_order, records
  your yes word for word, then Claude's own Allow once / Deny card asks again
  before each Kite call. In paper mode no Kite tool is called at all: it keeps
  a paper ledger. Builds the sell ticket that closes a day trade by your
  flat-by time (cancel the stop-loss first), records fills, and logs each
  trade's max loss so trader-risk can hold the daily limit. Covers connecting
  Zerodha's official Kite MCP server (hosted and read-only, or self-hosted with
  your own Kite Connect app to place orders; keys never in chat, static IP and
  market-protection caveats) and setting order tools to "Needs approval". Use
  when the user says "place the order", "buy it", "execute", "send it to
  Zerodha", "close my position", "square off", "flatten", "get me out",
  "connect Kite", "connect Zerodha", "set up paper trading", or after
  /trader-risk passes.
argument-hint: "[order number]"
---

# trader-broker

The Order Executor is the only skill in this pack that can lead to a real
order, so it is the strictest. An order reaches the NSE only after three
separate gates, every time, for every order:

1. **trader-risk passed this exact order today** (matched by fingerprint).
2. **The user typed an explicit yes for this one order** in the chat.
3. **The user clicked Allow once** on Claude's own permission card, which
   appears because the Kite order tools are set to "Needs approval".

Two yeses from the user, one PASS from the rules. No yes, no order.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show                     # every ticket, READY or not
python3 "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "yes"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --status placed --broker-id ID --filled-qty 54 --filled-price 1366.40
python3 "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --leg stop --status placed --broker-id ID2
python3 "${CLAUDE_SKILL_DIR}/ticket.py" fill --symbol DEMO --side sell --qty 54 --price 1375.30
python3 "${CLAUDE_SKILL_DIR}/ticket.py" flatten --symbol DEMO --qty 54 --price 1371.20
python3 "${CLAUDE_SKILL_DIR}/ticket.py" log
```

`ticket.py` never talks to a broker. It renders tickets, checks that a reply
is a real yes for that order, builds the flatten ticket, and keeps the log in
`trader/state/broker.json`. `--now "2026-10-19 10:00"` pins the clock (India time).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget writes it; `flatten` replaces it) |
| `trader/state/risk.json` | trader-risk's verdicts; only a PASS from today is READY |
| `trader/state/broker.json` | every yes, no, placed, failed, stop-loss leg and fill, with each entry's max loss |
| `trader/portfolio.json` | cash and shares, updated by `fill` and by `result --filled-qty` (charges are not deducted: the contract note has them) |
| `trader/plan.json` | the flat-by time for `flatten` |
| `trader/prices.json` | a fresh price for `flatten`'s limit |

## How a day trade goes to Zerodha

Zerodha has no bracket orders. trader-budget writes one entry as two Kite
`place_order` calls under one yes:

1. the **buy**: LIMIT, product MIS (intraday), exchange NSE, validity DAY;
2. **after it fills** (get_orders shows COMPLETE), the **stop-loss**: an SL
   sell for the same shares, trigger at the plan's stop, limit a little under.
   Never before the fill: a triggered sell with no shares would open a short.

The target is not sent: a target limit and a stop for the same shares could
both fill and leave a short. trader-alerts watches the target and the stop,
and `flatten` exits. Kite's MCP `place_order` takes numbers (not strings) and
cannot send the market protection Zerodha wants on MARKET and SL-M API orders,
so LIMIT and SL are the defaults.

## Paper first (the default)

With `"paper": true` Claude calls **no** Kite tool for an order. After the
user's yes, `ticket.py approve` says so, and Claude records the paper order
and its fill at the price the market gives (Kite `get_ltp` from the hosted
server, or `alerts.py quote DEMO --write`): a LIMIT buy fills once the price
trades at or under its limit. `result --leg stop --status placed` records the
paper stop-loss, and trader-alerts watches it. Indian brokers do not offer a
paper account, so this ledger is the paper account.

## Setup A: the hosted Kite MCP (read-only, no keys)

Zerodha's own server is github.com/zerodha/kite-mcp-server (MIT, checked
against its source on 2026-10-23). The hosted copy at
`https://mcp.kite.trade/mcp` needs no API key: the `login` tool gives a Kite
login link and the session lasts the day. **It leaves out the order tools**
("potentially destructive trading operations"). Use it for prices and
positions: `get_ltp`, `get_quotes`, `get_ohlc`, `get_positions`,
`get_holdings`, `get_orders`, `get_trades`, `get_order_history`,
`get_margins`, `get_profile`, `search_instruments`, `get_historical_data`.

Add it in Claude (Customize > Connectors > Add custom connector, URL
`https://mcp.kite.trade/mcp`), or in Claude Desktop's config with
`mcp-remote` (the `kite-hosted` block in `claude_desktop_config.example.json`
here). With only the hosted server, a live order is placed by the user in the
Kite app by hand, exactly as the ticket shows, and Claude records it.

## Setup B: self-hosted Kite MCP (to place orders)

1. The user creates a Kite Connect app at developers.kite.trade and gets an
   API key and secret.
2. The user builds the server (Go 1.21 or newer): `git clone
   https://github.com/zerodha/kite-mcp-server && cd kite-mcp-server && go
   build -o kite-mcp-server`.
3. In Claude Desktop: Settings > Developer > Edit Config. That opens
   `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/`,
   Windows: `%APPDATA%\Claude\`). The user adds the `kite` block from
   `claude_desktop_config.example.json` here, with the full path to the binary,
   and types their own key and secret into the file:

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

   **Keys never go in the chat.** Never ask for them, never read this file
   back into the conversation, and if the user pastes a key or secret anyway,
   tell them to delete that message and regenerate it on developers.kite.trade.
   `EXCLUDED_TOOLS` is optional; it hides the GTT tools this pack does not use.
4. Restart Claude Desktop, then run the `login` tool and open its link to
   sign in to Kite (once a day: Kite sessions expire every morning).
5. **Set the tool permissions.** Customize > Connectors (Settings > Connectors
   in older versions), open kite, and for each tool pick Always allow, Needs
   approval or Blocked:
   - `place_order`: **Needs approval**. Never Always allow.
   - `modify_order`, `cancel_order`: Needs approval.
   - `place_gtt_order`, `modify_gtt_order`, `delete_gtt_order`: **Blocked**
     unless the user has a reason and says so.
   - read-only tools (`get_ltp`, `get_quotes`, `get_positions`, `get_holdings`,
     `get_orders`, `get_order_history`, `get_margins`, `get_profile`): Always
     allow is fine.
6. Check it: ask Claude for the Kite profile and margins (read only).

Two caveats to say before going live, and to check on developers.kite.trade
before relying on them: under SEBI's retail-algo framework Zerodha rejects API
orders that do not come from the static IP registered on the Kite Connect app,
and MARKET and SL-M API orders need market protection, which this MCP server
does not send.

In Claude Code instead of Claude Desktop, keep the order tools on ask in
`.claude/settings.json`: `"permissions": {"ask": ["mcp__kite__place_order",
"mcp__kite__modify_order", "mcp__kite__cancel_order"]}`, and never put an
order tool in `allow`.

## Steps: the entry

1. `ticket.py show`. Only READY tickets go to the user. A ticket that is NOT
   READY or BLOCKED goes back to `/trader-risk`.
2. Show ONE ticket and ask: "Place this order? Reply yes or no." One order
   per question. Never offer "yes to all".
3. Run `ticket.py approve --index N --reply "<the user's reply, word for word>"`.
   If it prints "Not recorded", the reply was not an explicit yes for this
   order (for example "sure", "haan", "yes to all", or a yes that names a
   different stock or amount). Ask again. Never turn a reply into a yes yourself.
4. Paper: call no Kite tool; record the paper order and fill with `result`.
   Live: only after "Recorded your yes", call `place_order` with exactly the
   JSON the ticket printed. Claude's card appears: Allow once or Deny.
5. Record what happened: `ticket.py result --index N --status placed
   --broker-id <order_id>` (plus `--filled-qty Q --filled-price P` from
   `get_orders` or `get_order_history` once it is COMPLETE), or `--status
   denied`, or `--status failed` with `--note`. A failed order is not retried
   without a new check and a new yes. The log keeps the entry's max loss for
   trader-risk's daily limit.
6. Once the buy is COMPLETE, place the stop-loss: `place_order` with exactly
   the stop JSON the ticket printed (the same yes covers it; Claude's card
   asks again), then `ticket.py result --index N --leg stop --status placed
   --broker-id <order_id>`. If the stop-loss fails, say so at once: the
   shares have no stop at Zerodha.

## Steps: while it is open, and out by the close

- trader-alerts watches the stop, the target and the flat-by time
  (`alerts.py add --from-plan`). The SL order at Zerodha does the stopping.
- When the stop-loss fills, record it:
  `ticket.py fill --symbol DEMO --side sell --qty 54 --price 1357.50`.
- At the target, or by `flat_by` (15:05 IST), anything still open gets closed:
  1. Ask Kite how many shares are held: `get_positions` (read only).
  2. `ticket.py flatten --symbol DEMO --qty 54` writes a sell for those
     shares to `trader/orders.json`: a LIMIT at the last price minus
     `exit_buffer_pct` (0.5%), rounded down to the tick, from `--price` or a
     fresh `trader/prices.json`; with no fresh price, a MARKET sell with a
     warning.
  3. `/trader-risk`. A sell that only closes what the user holds is never
     blocked by the daily limits or the entry window.
  4. `ticket.py show`, then the user's yes for that one order, as above.
  5. Live: the SL order still holds those shares at Zerodha. Cancel it first:
     `get_orders` (status TRIGGER PENDING), then `cancel_order` (variety
     regular, its order_id; Needs approval). Then `place_order` with the
     ticket, then `result`.
- Zerodha's own MIS auto square-off comes later (about 15:12 for F&O stocks in
  the closing-auction period from 3 Aug to 6 Dec 2026, 15:25 otherwise) and
  costs Rs 50 + GST an order. Flat by 15:05 beats it.

## Example (synthetic test data)

DEMO is a made-up NSE stock. On a live account the ticket reads:

```
ORDER 1 of 1, LIVE ACCOUNT, REAL MONEY, Zerodha (Kite)
  BUY 54 DEMO, limit 1366.40, MIS intraday, NSE, good for today
  then stop-loss: SELL 54 DEMO, SL trigger 1357.50 limit 1354.10, MIS intraday, NSE, good for today, placed after the buy fills
  target 1375.30 (watched by trader-alerts, not sent to Zerodha)
  max loss about Rs 553.67 if the stop fills at 1357.50, incl. about Rs 73.07 charges (it can fill worse, or not at all, in a fast market)
  why: opening-range breakout on DEMO: 54 shares, bound by your position cap (...)
  READY: trader-risk PASS, waiting for your yes
  After your yes, Claude calls place_order (Kite MCP) with exactly:
    {"variety": "regular", "exchange": "NSE", "tradingsymbol": "DEMO", "transaction_type": "BUY",
     "quantity": 54, "product": "MIS", "order_type": "LIMIT", "price": 1366.4, "validity": "DAY"}
  Once it fills (get_orders shows COMPLETE), Claude calls place_order for the stop-loss with exactly:
    {"variety": "regular", "exchange": "NSE", "tradingsymbol": "DEMO", "transaction_type": "SELL",
     "quantity": 54, "product": "MIS", "order_type": "SL", "price": 1354.1, "trigger_price": 1357.5,
     "validity": "DAY"}
```

The user replies "yes". Claude records it, calls `place_order`, and the card
reads "Claude wants to use place_order" with that JSON and two buttons: Allow
once, Deny. On the default paper account the same ticket says "PAPER account
(nothing is sent to Zerodha)" and no Kite tool is called. The `why` and the
plan stay in the file; they are never sent.

## What it never does

- Never places an order without all three gates, and never two orders on one yes (the entry's own
  stop-loss is part of that order, never another symbol or another trade).
- Never places the stop-loss before the buy fills, and never sends the target as an order.
- Never places orders from a scheduled or background task: nobody is there to say yes.
- Never calls a Kite order tool on a paper account.
- Never asks for, reads, stores or repeats API keys, secrets, passwords, TOTP codes or request tokens.
- Never switches `"paper"` to false. The user does that themselves.
- Never uses GTT, F&O, MTF, cover orders or AMO on its own.
- Never promises a fill price: a LIMIT may not fill, and an SL stop can fill worse or not at all.

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
