---
name: nse-budget
description: >-
  Budget Manager. Sizes every position to your budget and your max loss per
  trade. Reads the trade plan from nse-exit (entry, stop, target), finds
  the whole shares that fit inside four limits (max loss per trade, position
  cap, max order, cash after your buffer), says which limit decided it, and
  writes ONE bracket order (entry, stop-loss, take-profit) to
  trader/orders.json (nse-broker turns it into Kite order calls). A stop
  too far away for your max loss is a plain refusal, not a smaller order.
  Nothing is placed. Use when the user says "how many shares", "size this
  trade", "how much should I buy", "I have ₹1,00,000", "I only want to risk ₹1,000
  a trade", "set my rules", "keep 10% in cash", or after /nse-exit writes
  a plan.
argument-hint: "[budget in rupees]"
---

# nse-budget

The Budget Manager turns a plan ("buy RELIANCE at 1456.70, stop 1433.30") into an
exact order ("buy 40 shares"), inside rules the user sets once. The stop
decides the size: the further away the stop, the fewer shares. It writes a
proposal only. nse-risk checks it and nse-broker asks the user yes or
no. Day trading loses most people money. Sizing to a max loss keeps each
loss small; it does not make a trade win.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py"                        # trader/plan.json + trader/config.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --write                # write trader/orders.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit --write        # a limit order at the plan's entry
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit 364.00 --json  # a limit order at 364.00
python3 "${CLAUDE_SKILL_DIR}/size.py" --weights HDFCBANK=40,ITC=60 --config split.json   # weights mode, below
```

`--now "2026-10-19 10:00"` pins the clock (IST). A plan for
another day is refused: make a new one with nse-exit.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/config.json` | the rules (start from `config.example.json` in this folder) |
| `trader/plan.json` | the trade: entry, stop, target, setup, flat-by time (nse-exit writes it) |
| `trader/prices.json` | optional: a fresh price above the plan's entry means a bigger risk (nse-alerts writes it) |
| `trader/portfolio.json` | optional: its cash caps the size (nse-broker keeps it) |
| `trader/orders.json` | the one proposed bracket order it writes with `--write` |

## Step 1: set the rules once

If `trader/config.json` does not exist, copy `config.example.json` into
`trader/config.json` and ask the user for each value in plain words:

- **budget**: how much money this is for. Only money they can afford to lose.
- **cash_buffer_pct**: how much always stays in cash (default 10).
- **max_loss_per_trade_inr**: the most one trade may lose if the stop fills at the stop (default 50).
- **max_position_pct**, **max_order_inr**, **min_order_inr**: the biggest position (75% of the
  budget), the biggest order (₹80,000) and the smallest order (₹5).
- The day limits nse-risk enforces: **max_daily_loss_inr** (150), **max_trades_per_day** (3),
  **min_reward_risk** (1.0), **require_stop** (true), **no_entries_before** / **no_entries_after**
  ("09:30" / "15:30") and **flat_by** ("15:10"), all IST.
- **blocked**: tickers they never want to trade.
- Keep **paper** true until they have watched it work on a paper account.

## Step 2: size the trade

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py" --write
```

Whole shares only (NSE equity trades in units of one share). The size is
the smallest of four limits:

| limit | shares |
| --- | --- |
| max loss | `max_loss_per_trade_inr` / risk a share (entry minus stop), then trimmed until loss at the stop **plus estimated charges** fits |
| position cap | budget x `max_position_pct` / entry |
| max order | `max_order_inr` / entry |
| cash | (budget minus the cash buffer) / entry, never more than the cash in `trader/portfolio.json` |

The entry it sizes at: the limit price with `--limit`. For a market order, the
plan's entry, or the price in `trader/prices.json` if it is fresh (under 15
minutes old) and higher. A price that ran up since the plan means a bigger
loss at the same stop, so fewer shares. A lower price never shrinks the risk.

It refuses, and writes no order, when: the size is zero shares, the order is
under the minimum, reward:risk is under `min_reward_risk`, the stop is not
below the entry, the target is not above it (the move already happened), the
plan is for another day, the ticker is blocked, or the plan is a short. Say
the reason to the user in one line.

## Charges

Intraday (MIS) equity on NSE pays, per round trip: brokerage (default ₹20 or
0.03 % per executed order, whichever is lower), STT 0.025 % on the sell, NSE
transaction charges 0.00297 % both sides, SEBI fee ₹10 a crore, stamp duty
0.003 % on the buy, and 18 % GST on brokerage, exchange and SEBI charges.
These are the defaults in `"charges"` in `config.example.json`; change them to
your broker's schedule. They are estimates: the contract note is the truth.

## Step 3: show the user

Show the four limits, which one decided the size, the shares, the rupees
and the max loss at the stop. Say plainly that a stop is an order, not a
promise: in a fast market it can fill below the stop. Then hand over to
`/nse-risk`.

## Example

₹1,00,000 budget, ₹1,000 max loss a trade, the DEMO plan (entry 1,456.70, stop 1,433.30):

```
Limits (whole shares; the smallest one wins):
  max loss      ₹1,000 max loss / 23.40 risk a share, less charges at the stop  40 shares  <- binds
  position cap  75% of ₹100,000 = ₹75,000 / 1456.70                         51 shares
  max order     ₹80,000 / 1456.70                                           54 shares
  cash          budget ₹100,000 minus the ₹10,000 cash buffer = ₹90,000 / 1456.70  61 shares

Size: 40 shares of DEMO = ₹58,268. Max loss at the stop: 40 x 23.40 = ₹936.
Charges (estimate): ₹61.11 if stopped out, ₹62.84 at the target. Worst case at the stop, all in: ₹997.11.
```

By price risk alone it would be 42 shares; the charges take it to 40.

## Weights mode

`size.py --weights RELIANCE=40,TCS=60 --config split.json` splits a budget
across names by weight, in whole shares, for a delivery (CNC) portfolio
rather than a day trade. `targets.example.json` shows the shape.

## Rules

- Proposals only. This skill never calls a broker tool.
- Never size money the user has not said is for this. Ask for the budget and the max loss.
- Every number comes from the config, the plan or the prices. Nothing is guessed.
- No projections: never say what a trade will make. A target is a plan, not a forecast.
- Free Yahoo prices are unofficial and can lag. The broker's price is the one that fills.

## What it never does

It never places, approves or schedules an order, never raises a limit on its
own, never shrinks a stop to fit more shares, and never sizes options,
margin or short positions.

## Safety

- Education and tooling, not investment advice; this pack is not a SEBI-registered adviser. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except nse-broker, and only when all three hold: nse-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
