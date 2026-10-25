---
name: nse-broker
description: >-
  Order Executor for NSE. Turns an approved trade into Zerodha Kite order
  calls (intraday MIS: a BUY, then an SL-M stop once it fills), paper trading
  by default, never without your yes. Shows each order as a plain ticket with
  the stop, the target, the max loss and the estimated charges, records your
  yes word for word, then Claude's own Allow once / Deny card asks again.
  Builds the sell ticket that squares off a day trade by your flat-by time,
  records fills, and logs each trade's max loss so nse-risk can hold the
  daily limit. Covers connecting Zerodha's Kite MCP server (hosted read-only,
  or self-hosted with your own Kite Connect key), Interactive Brokers India,
  and setting order tools to "Needs approval". Use when the user says "place
  the order", "buy it", "execute", "send it to Zerodha", "square off",
  "close my position", "get me out", "connect Kite", "set up paper trading",
  or after /nse-risk passes.
argument-hint: "[order number]"
---

# nse-broker

The Order Executor is the only skill in this pack that can lead to a real
order, so it is the strictest. An order reaches the exchange only after three
separate gates, every time, for every order:

1. **nse-risk passed this exact order today** (matched by fingerprint).
2. **The user typed an explicit yes for this one order** in the chat.
3. **The user clicked Allow once** on Claude's own permission card, which
   appears because the order tool is set to "Needs approval".

Two yeses from the user, one PASS from the rules. No yes, no order.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show                     # every ticket, READY or not
python3 "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "yes"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --status placed --broker-id ID --filled-qty 40 --filled-price 1457.10
python3 "${CLAUDE_SKILL_DIR}/ticket.py" fill --symbol RELIANCE --side sell --qty 40 --price 1480.10
python3 "${CLAUDE_SKILL_DIR}/ticket.py" flatten --symbol RELIANCE --qty 40
python3 "${CLAUDE_SKILL_DIR}/ticket.py" log
```

`ticket.py` never talks to a broker. It renders tickets, checks that a reply
is a real yes for that order, builds the square-off ticket, and keeps the log
in `trader/state/broker.json`. `--now "2026-10-19 10:00"` pins the clock (IST).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed trade (nse-budget writes it; `flatten` replaces it) |
| `trader/state/risk.json` | nse-risk's verdicts; only a PASS from today is READY |
| `trader/state/broker.json` | every yes, no, placed, failed and filled, with each entry's max loss |
| `trader/portfolio.json` | cash and shares, updated by `fill` and by `result --filled-qty` |
| `trader/plan.json` | the flat-by time for `flatten` |

## How a trade becomes Kite orders

Kite has no bracket orders for equity, so the ticket prints the trade as
Kite Connect `place_order` calls, in order:

1. **now**: `BUY`, `MARKET` (or `LIMIT`), `product: MIS`, `exchange: NSE`,
   `variety: regular`, `validity: DAY`.
2. **after the BUY fills**: `SELL`, `SL-M`, `trigger_price` = the plan's stop,
   same quantity, MIS.

The **target is not a resting order**. Two live exits on the same MIS shares
(an SL-M and a LIMIT sell) can both fill in a fast market and leave you
short. nse-alerts rings at the target; then cancel the SL-M and sell with a
LIMIT order (or `ticket.py flatten`).

MIS positions are squared off by the broker itself around 15:15 to 15:20 IST,
with an extra fee. `flat_by` defaults to 15:10 so you are out first.

## Paper mode (the default)

`"paper": true` in `trader/config.json`. Zerodha has no paper account, so
paper here means **no broker call at all**: the ticket shows the Kite JSON, the
user says yes, and Claude records a paper fill with `ticket.py fill` at the
price the alert or chart shows. Run paper for weeks before going live.

## Setup A: Zerodha Kite MCP

Zerodha publishes a Kite MCP server: github.com/zerodha/kite-mcp-server.

- **Hosted** (`https://mcp.kite.trade/mcp`): sign in with your Kite login in
  the browser window it opens. The hosted version leaves out trading
  operations, so it is for reading: holdings, positions, orders, margins,
  quotes. Use it to check fills and positions; place the orders yourself in
  Kite from the ticket.
- **Self-hosted** with your own Kite Connect app (paid API subscription):
  this is the version that can expose `place_order`, `modify_order` and
  `cancel_order`. Run it on your own computer and add it to Claude Desktop
  (Settings > Developer > Edit Config; `claude_desktop_config.example.json`
  here shows the shape). Put the API key and secret in its environment, never
  in the chat.

Then set the tool permissions (Customize > Connectors, open kite):

- `place_order`, `modify_order`, `cancel_order`, and any GTT tool that places
  or changes anything: **Needs approval**. Never Always allow.
- read-only tools (profile, margins, holdings, positions, orders, quotes):
  Always allow is fine.

In Claude Code, keep order tools on ask in `.claude/settings.json`:
`"permissions": {"ask": ["mcp__kite__place_order", "mcp__kite__modify_order", "mcp__kite__cancel_order"]}`.

**Keys never go in the chat.** Never ask for them, never read config files
back into the conversation. If the user pastes a key or a request token,
tell them to delete that message and regenerate it in the Kite developer console.

## Setup B: Interactive Brokers India

The Interactive Brokers connector in Claude's directory drafts trade
instructions that the user approves inside IBKR. Set `"broker": "ibkr"` in
`trader/config.json`; the final yes then also happens in IBKR.

## Steps: the entry

1. `ticket.py show`. Only READY tickets go to the user. A ticket that is NOT
   READY or BLOCKED goes back to `/nse-risk`.
2. Show ONE ticket and ask: "Place this order? Reply yes or no." One order
   per question. Never offer "yes to all".
3. Run `ticket.py approve --index N --reply "<the user's reply, word for word>"`.
   If it prints "Not recorded", the reply was not an explicit yes for this
   order. Ask again. Never turn a reply into a yes yourself.
4. Only after "Recorded your yes": in paper mode, record the paper fill. Live,
   call `place_order` with exactly the first JSON the ticket printed. Claude's
   card appears: Allow once or Deny. When the BUY shows as COMPLETE (read the
   order book), place the SL-M from the ticket the same way.
5. Record what happened: `ticket.py result --index N --status placed
   --broker-id <order_id> --filled-qty Q --filled-price P`, or `--status
   denied`, or `--status failed` with `--note`. A failed order is not retried
   without a new check and a new yes.

## Steps: while it is open, and out by the close

- nse-alerts watches the stop, the target and the flat-by time
  (`alerts.py add --from-plan`). The SL-M at Kite does the stopping.
- When the stop fills, or you sell at the target, record it:
  `ticket.py fill --symbol RELIANCE --side sell --qty 40 --price 1480.10`.
- By `flat_by` (15:10 IST), anything still open gets squared off:
  1. Read the open MIS position (positions, read only).
  2. Cancel the pending SL-M first (`cancel_order`, Needs approval), or the
     square-off and the stop could both sell.
  3. `ticket.py flatten --symbol RELIANCE --qty 40` writes a market sell.
  4. `/nse-risk` (a sell that only closes what you hold is never blocked),
     `ticket.py show`, the user's yes, then `place_order`, then `result`.

## Example

```
ORDER 1 of 1, PAPER (no broker call, recorded only), Zerodha Kite
  BUY 40 DEMO, market, good for today
  stop-loss 1433.30
  take-profit 1480.10
  max loss about ₹936 if the stop fills at 1433.30 (it can fill worse in a fast market)
  charges about ₹61.11 if stopped out (estimate; your contract note rules)
  READY: nse-risk PASS, waiting for your yes
  Kite place_order, in this order:
    now:                {"exchange": "NSE", "tradingsymbol": "DEMO", "quantity": 40,
                         "product": "MIS", "transaction_type": "BUY", "order_type": "MARKET", ...}
    after the BUY fills: {"transaction_type": "SELL", "order_type": "SL-M", "trigger_price": 1433.3, ...}
  Target 1480.10: nse-alerts rings, then a LIMIT SELL (or ticket.py flatten) takes it.
```

## What it never does

- Never places an order without all three gates, and never two orders on one yes.
- Never places orders from a scheduled or background task: nobody is there to say yes.
- Never asks for, reads, stores or repeats API keys, request tokens, PINs or TOTPs.
- Never switches `"paper"` to live. The user does that themselves.
- Never places F&O, MTF or short-sell orders unless the config allows it and the user confirms.
- Never trades a stock on the user's `pit_restricted` list (SEBI insider rules): nse-risk blocks it.
- Never promises a fill price: a market order and an SL-M fill at whatever the market gives.

## Safety

- Education and tooling, not investment advice. This pack is not a SEBI-registered
  investment adviser or research analyst. A PASS is a rules check, not a recommendation.
- Paper trading by default. Going live is the user's own decision, in their own config.
- SEBI's studies of intraday traders find most of them lose money. Size small.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
