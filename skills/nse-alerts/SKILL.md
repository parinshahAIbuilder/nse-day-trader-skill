---
name: nse-alerts
description: >-
  Price Alerts. Rings when a price, a stop or the opening bell hits. Turns
  the trade plan into three alerts in one go (the stop, the target and "flat
  by close" at your flat-by time), plus above / below / stop / target levels,
  percent moves from the previous close, any time of day, and the NSE
  opening bell, each ringing once so you are not spammed. Knows NSE hours
  with the 2026 NSE trading holidays, works out IST itself, fetches free
  delayed quotes (Yahoo chart JSON
  CSV) one ticker at a time, and writes trader/prices.json for sizing and the
  risk check. Use when the user says "alert me when", "tell me if RELIANCE hits",
  "watch my stop", "watch my trade", "remind me to close by 3:10", "is the
  market open", "when does the market open", "get me a price", or right
  after /nse-exit writes a plan.
argument-hint: "[ticker] [price]"
---

# nse-alerts

Price Alerts watches the numbers so the user does not have to stare at a
chart. An alert is information, never an order: when a stop alert rings,
nothing is sold by this skill. With a bracket order the stop-loss and the
take-profit already sit at the broker and do the work; the alerts tell the
user what happened and when it is time to be flat.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock                          # NSE open or closed, next open
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan                # stop, target, flat by close from trader/plan.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --opening-bell
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --stop 1433.30
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --target 1480.10
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --at 15:10 --label "flat by close" --before-close
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol ITC --pct 3
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check                          # exit 0 = something rang
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote RELIANCE --write             # -> trader/prices.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote --holdings --write       # portfolio + plan tickers
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
python3 "${CLAUDE_SKILL_DIR}/alerts.py" reset demo-plan-stop
```

`--now "2026-10-19 15:10"` pins the clock (IST); `--offline DIR`
reads saved quote files instead of the network.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the trade from nse-exit, for `add --from-plan` |
| `trader/alerts.json` | the rules (`alerts.example.json` in this folder) |
| `trader/state/alerts.json` | which rules rang and when, so each rings once |
| `trader/prices.json` | the latest quotes, merged in, for nse-budget and nse-risk |
| `trader/portfolio.json` | the tickers held, for `quote --holdings` |

## Rule types

| type | rings when | rings again |
| --- | --- | --- |
| `above` / `target` | price at or above `price` | after `reset`, or on its own if `"rearm": true` and the price fell back |
| `below` / `stop` | price at or below `price` | same |
| `pct_move` | moved `pct` percent from the previous close (or `ref`), optional `direction` up or down | once per day at most |
| `time` | the IST clock passed `at` ("15:10") on a trading day | once per trading day |
| `opening_bell` | the NSE is open | once per trading day |

A rule with `"date"` is watched on that IST date only; `--from-plan`
rules carry the plan's date, so yesterday's stop never rings today. A `time`
rule with `"before_close": true` is the "flat by close" alert; NSE has no
early closes, so it rings at its time every trading day.

## Quotes

- `yahoo` (default): `query2.finance.yahoo.com/v8/finance/chart/<TICKER>`, an
  unofficial free JSON endpoint, one request per ticker, half a second apart.
  On a 429 it waits and tries once more; on a 429 or a 5xx after that it
  tries `query1`. It identifies itself as `nse-day-trader-skill/1.0`.
- `stooq` is a US-only fallback and has no NSE quotes; use `yahoo`.
- Free quotes are unofficial, delayed and can lag. They are fine for alerts
  and for checking a size, not for timing an order. The broker's price is the
  one that fills. Each price in `trader/prices.json` keeps its quote time, and
  nse-risk only uses one under 15 minutes old.
- If both are down, read the price from the broker connector (Kite quotes / LTP,
  IBKR `get_price_snapshot`) and write it into
  `trader/prices.json` as `{"as_of": "...Z", "prices": {"RELIANCE": 1468.40}}`.

## The NSE clock

Normal market 9:15 a.m. to 3:30 p.m. IST (pre-open call auction 9:00 to
9:08), closed on weekends and on these 2026 equity holidays (NSE's list):
Jan 15 (Maharashtra municipal elections), Jan 26, Mar 3 (Holi), Mar 26
(Ram Navami), Mar 31 (Mahavir Jayanti), Apr 3 (Good Friday), Apr 14
(Ambedkar Jayanti), May 1 (Maharashtra Day), May 28 (Bakri Id), Jun 26
(Muharram), Sep 14 (Ganesh Chaturthi), Oct 2, Oct 20 (Dussehra), Nov 10
(Diwali-Balipratipada), Nov 24 (Guru Nanak Jayanti), Dec 25.

Muhurat trading (Sunday 8 November 2026) is a special evening session that
NSE announces by circular: the tools treat that Sunday as closed, so set an
`at` alert by hand if you trade it. IST has no daylight saving. For 2027 the
tools say the holiday list is unknown until NSE publishes it.

## Steps

1. Right after nse-exit writes the plan, run `add --from-plan`. Confirm
   the three alerts back to the user: the stop, the target and the flat-by time.
2. Run `check` while the trade is open: every few minutes from a Claude
   scheduled task or `/loop`. Each run fetches only the tickers in today's
   rules and rings each alert once. A check reads the latest delayed quote;
   it is not a live feed, and a price can pass a level between two checks.
3. Tell the user what rang in one line each. A stop or target alert means the
   bracket order has probably filled: check `get_open_position` and record the
   fill with nse-broker. "Flat by close" means close anything still open:
   nse-broker `flatten`, then nse-risk, then the user's yes. Never act
   for them.

## Example

`alerts.py add --from-plan` after the DEMO plan writes three rules: stop
1,433.30, target 1,480.10 and "flat by close" at 15:10, each for that date
only. `alerts.py check` rings each one once. `alerts.py clock --now
"2026-10-20 10:00"` says: NSE closed, NSE holiday: Dussehra. Next open
2026-10-21 09:15 IST.

## Rules

- An alert is a fact about a price or the clock. Say it as a fact.
- Never present a quote as live. Free quotes are delayed and unofficial.
- Confirm every new rule back to the user: ticker, type, level, day.

## What it never does

It never places, cancels or changes an order, never claims a quote is
live, and never predicts where a price is going.

## Safety

- Education and tooling, not investment advice; this pack is not a SEBI-registered adviser. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except nse-broker, and only when all three hold: nse-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
