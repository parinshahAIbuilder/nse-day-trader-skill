---
name: nse-opportunities
description: >-
  Daily Opportunity Scan for NSE. Reads the daily charts of a whole list
  (a bundled liquid 50, NSE's own Nifty 50 / 100 / 200 / F&O list, or your
  watchlist), tests six technical setups on each (20-day breakout on volume,
  pullback to EMA20 in an uptrend, MACD cross, Bollinger squeeze breakout,
  holding near the 52-week high, oversold bounce), back-tests every setup on
  that stock's own last year, scores and ranks what matched, and gives each
  one an entry, a stop, a 2R target, a size at your max loss with delivery
  charges, and a chart. Optional intraday view (opening range, VWAP) for the
  top names. Writes trader/picks.json and an HTML report with charts (and
  PNG screenshots). Use when the user says "what's setting up today", "find
  me opportunities", "daily scan", "short-term trades", "swing ideas",
  "scan Nifty", "show me the charts", "morning report", or at the start of a
  trading day before /nse-news and /nse-entry.
argument-hint: "[--index nifty50 | --symbols A,B] [--top N] [--html] [--intraday]"
---

# nse-opportunities

The daily scan does in a minute what a trader does in an hour of flipping
charts: read every chart on the list, check it against a fixed set of rules,
test each rule on that stock's own history, and put what matched in order,
with levels. It is a shortlist to look at, not a list of stocks to buy.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/picks.py" scan --html                     # bundled liquid 50
python3 "${CLAUDE_SKILL_DIR}/picks.py" scan --index nifty100 --top 10 --html --png
python3 "${CLAUDE_SKILL_DIR}/picks.py" scan --symbols RELIANCE,TCS,INFY,SBIN --intraday
python3 "${CLAUDE_SKILL_DIR}/picks.py" scan --watchlist trader/watchlist.txt --json
```

Flags: `--top N` (10), `--config trader/config.json` (max loss and max order
for sizing), `--out trader/picks.json`, `--html-out PATH`, `--png` (needs
Playwright), `--now "2026-10-26 16:00"` (IST) to pin the clock, `--offline
FILE` (a bundle of Yahoo charts), `--pause` between requests (0.4 s).

## How it decides

Completed daily bars only. During the session today's bar is left out; after
15:30 IST it counts. Indicators: EMA 20 / 50 / 200, RSI 14, MACD 12/26/9,
ATR 14, Bollinger 20/2, 20-day volume and traded value, 52-week high, and the
20-day return against the Nifty 50.

| setup | the rule |
| --- | --- |
| 20-day breakout on volume | close above the highest high of the 20 sessions before, volume at least 1.5x average |
| Pullback to EMA20 in an uptrend | close > EMA50 > EMA200, touched EMA20 in the last 3 bars, closed above it, RSI 40 to 65 |
| MACD bullish cross | MACD crossed above its signal in the last 2 bars, close above EMA50 |
| Bollinger squeeze breakout | band width in its lowest 15 % of 120 sessions, close above the upper band |
| Holding near the 52-week high | within 3 % of it, above EMA20, RSI under 75 |
| Oversold bounce | RSI under 30 in the last 3 bars, closed above yesterday's high (counter-trend) |

**The plan** for every match: a buy-stop one tick above today's high (it
only fills if the stock trades higher next session), a stop at the higher of
the 5-bar swing low and entry minus 1.5 ATR, a target at 2R, and a time stop
of 5 sessions. Prices are on NSE ticks.

**The back-test** replays the same setup and plan on every past day it fired
on that stock in the last year: filled or not, target first, stop first, or
out after 5 sessions, and the average R. A bar that touches both counts as
the stop.

**The score (0 to 100)** = the setup's base points + trend alignment +
strength against the Nifty 50 + volume + the back-test record, minus
penalties for thin trading (under ₹20 crore a day), a wide ATR (over 5 %) or
a stretched RSI (over 75). It orders the list; it is not a probability.

**The size** uses `max_loss_per_trade_inr` and `max_order_inr` from
`trader/config.json` with delivery (CNC) charges: STT 0.1 % both sides, stamp
0.015 % on the buy, NSE and SEBI fees, the DP charge and GST.

## Steps

1. Run the scan after 15:30 IST (for the next session) or before 9:15.
   Say what was read: how many charts, how many failed, the date of the last bar.
2. Show the top list: symbol, score, setup, entry, stop, target, the past
   record. Lead with the one-line reason for each, then the caveats in
   `notes` (thin, stretched, lagging the index, no history).
3. With `--html`, publish or send the report. It has the table, a card per
   pick with its 90-session chart, the levels and the sizing.
4. For the one the user wants to look at: `/nse-exchange check SYMBOL`
   (board meeting today? results? surveillance? circuit band?), then
   `/nse-news`. A results date inside the holding period changes everything.
5. Then the normal path: `/nse-entry` (or a buy-stop at the entry),
   `/nse-exit`, `/nse-timemachine`, `/nse-budget`, `/nse-risk`, `/nse-broker`.

Every trading day: a scheduled task at 16:00 IST running
`picks.py scan --index nifty100 --html` and sending the report.

## Say it straight

- "Matched the rules", "ranked", "set up". Never "buy", "sure shot", "target
  will hit", "guaranteed", or a profit promise.
- Quote the back-test honestly, including the bad ones: "this setup hit its
  target 2 times in 9 on this stock and lost on average".
- Data is Yahoo's daily bars, free and delayed; the broker's price fills.
- Say the date of the last bar. A scan from yesterday is about yesterday.

## What it never does

- Never places, sizes for real or checks an order: that is nse-budget, nse-risk, nse-broker.
- Never calls a pick a recommendation. This pack is not a SEBI-registered
  investment adviser or research analyst, and the list is not research advice.
- Never ranks a stock on the user's `pit_restricted` list higher, or at all
  for trading: nse-risk blocks it.

## Safety

Education and tooling, not investment advice. A back-test is history, not a
forecast: it ignores news, gaps through the stop, slippage and taxes. Most
individual traders who trade short term lose money.
