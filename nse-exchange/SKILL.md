---
name: nse-exchange
description: >-
  NSE Exchange Data. Reads NSE India's own website data: whether the market
  is open and the Nifty 50, the official trading-holiday list (so every skill
  in the pack knows the calendar for any year NSE has published), a stock's
  NSE quote with its circuit band, series and surveillance (ASM / GSM)
  status, corporate announcements filed with NSE (results, board meetings,
  ratings, pledges, fund raises), upcoming board meetings, and today's bulk
  and block deals. `check SYMBOL` puts it all in one place before a trade,
  and nse-risk reads what it saves. Use when the user says "is the market
  open", "NSE holidays", "NSE quote", "circuit limit", "is it in ASM",
  "any announcements", "when are results", "board meeting", "bulk deals",
  "block deals", "check this stock on NSE", or before /nse-entry.
argument-hint: "[status | holidays | quote | announcements | events | deals | check] [SYMBOL]"
---

# nse-exchange

The other skills read charts. This one reads the exchange: the facts that
make a good chart a bad trade. A board meeting for results this afternoon, a
stock in surveillance with a 5 % band, a trade-for-trade series that cannot
be day traded, a promoter pledge filed last night.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/nse_api.py" status
python3 "${CLAUDE_SKILL_DIR}/nse_api.py" holidays --write
python3 "${CLAUDE_SKILL_DIR}/nse_api.py" quote RELIANCE TCS --write
python3 "${CLAUDE_SKILL_DIR}/nse_api.py" announcements RELIANCE --days 3
python3 "${CLAUDE_SKILL_DIR}/nse_api.py" events RELIANCE
python3 "${CLAUDE_SKILL_DIR}/nse_api.py" deals --symbol RELIANCE
python3 "${CLAUDE_SKILL_DIR}/nse_api.py" check RELIANCE --write
```

Flags: `--json`, `--offline DIR` (saved answers), `--now "2026-10-26 10:00"`
(IST), `--pause` (seconds between requests, default 1).

## What it reads and writes

| command | NSE source | writes |
| --- | --- | --- |
| `status` | `/api/marketStatus` | |
| `holidays` | `/api/holiday-master?type=trading` (CM segment) | `trader/nse_holidays.json`: nse-entry, nse-exit, nse-risk, nse-alerts and nse-scanner add it to their built-in 2026 list |
| `quote` | `/api/quote-equity?symbol=` | `trader/prices.json` with circuit band, series and surveillance per symbol (nse-budget and nse-risk use prices under 15 minutes old) |
| `announcements` | `/api/corporate-announcements?index=equities&symbol=` | |
| `events` | `/api/event-calendar?index=equities&symbol=` | |
| `deals` | `nsearchives.nseindia.com/content/equities/bulk.csv`, `block.csv` | |
| `check` | all of the above for one stock | `trader/state/nse_check.json` |

## What nse-risk does with it

- A stock not in the **EQ** series (BE, BZ: trade-for-trade): **BLOCK** for a day trade.
- **Surveillance** (ASM / GSM), a **board meeting today**: warnings the user hears.
- A target above today's **upper circuit**, or a stop below the **lower
  circuit**: warnings (it cannot fill there today; at a lower circuit there
  may be no buyers to fill a stop).

## Steps

1. Once a month, or when a year turns: `holidays --write`.
2. Before a trade: `check SYMBOL --write`. Read the flags out. Open any
   announcement tagged results, fund raise, promoter or legal / regulatory
   and read the PDF before saying what it means.
3. During the session: `quote SYMBOL --write` refreshes the price for sizing
   and the risk check.

## Fair use of NSE's website

These are the JSON endpoints behind nseindia.com's own pages, not a licensed
data feed. The tool opens the home page once for a session cookie, sends one
request at a time with a pause, and never loops fast. NSE can change or block
them without notice; when that happens the tool says so and nothing else
breaks. NSE's website terms limit automated use and redistribution: use this
for your own reading, not to republish data. For licensed or real-time data,
use NSE's data products or your broker's API (Kite quotes, for example).

## What it never does

- Never places orders, never logs in, never needs a key.
- Never treats an announcement headline as the filing: read the PDF.
- Never tells the user to trade on an announcement. If the user has
  unpublished information about a company, the stock is off limits (SEBI PIT).

## Safety

Education and tooling, not investment advice. Exchange data is shown as NSE
published it, delayed; the broker's price is the one that fills.
