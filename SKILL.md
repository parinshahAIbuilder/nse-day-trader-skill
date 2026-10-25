---
name: nse-day-trader
description: >-
  NSE Day Trader: nine skills for intraday trading on the National Stock
  Exchange of India. Scans NSE charts for opening-range breakouts and daily
  setups, tracks news, times the entry bar by bar on the IST clock, sets
  stop and target on NSE ticks, replays the rest of the session 10,000 times,
  sizes in rupees after STT and charges, blocks SEBI insider-trading
  conflicts, and turns an approved trade into Zerodha Kite order calls.
  Paper by default, never without your yes. Use when the user says "scan
  NSE", "find intraday setups", "did RELIANCE break out", "set my stop",
  "how many shares", "risk check", "place it on Zerodha", "square off", or
  names any nse-* step.
---

# NSE Day Trader

One trade runs through the folders in this order. Each step reads what the
last one wrote in `trader/` in the user's project, and each folder has its
own SKILL.md with the full instructions. Read the one for the step at hand
before running its script.

```
nse-scanner -> nse-news -> nse-entry -> nse-exit -> nse-timemachine -> nse-budget -> nse-risk -> nse-broker
nse-alerts: stop, target, flat by 15:10 IST    (while the trade is open)
```

| folder | script | job |
| --- | --- | --- |
| `nse-scanner/` | `scan.py universe | daily | orb` | NSE EQ-series universe, pre-market setups, opening-range breakouts |
| `nse-news/` | `news.py --tickers ...` | new headlines per stock, tagged, plus the exchange-filings checklist |
| `nse-entry/` | `entry.py check SYMBOL --write` | triggered / waiting / expired, on 5-minute bars |
| `nse-exit/` | `exit.py plan --stop or-mid --target 1R --write` | stop and target on NSE ticks, flat by 15:10 |
| `nse-timemachine/` | `timemachine.py run --write` | 10,000 replays plus every past day the rule fired |
| `nse-budget/` | `size.py --write` | shares to your rupee budget and max loss, net of charges |
| `nse-risk/` | `check.py` | PASS or BLOCK: limits, window, SEBI PIT list |
| `nse-broker/` | `ticket.py show` | Kite order calls, the user's yes, paper by default |
| `nse-alerts/` | `alerts.py add --from-plan`, `check`, `clock` | price and time alerts on the NSE calendar |

Setup once: copy `nse-budget/config.example.json` to `trader/config.json`
and set the budget, limits and `pit_restricted` (companies where the user is
a designated person or insider). Python 3.8+, standard library only. Prices
come from Yahoo Finance (`SYMBOL.NS`), free and unofficial.

## Always

- Say the time and source of the data. Free quotes can lag.
- A scan hit or a trigger is "matched a rule", never "a buy".
- Every order needs nse-risk PASS today, the user's explicit yes for that one
  order, and Claude's Allow once card. Never more than one order per yes.
- Never trade a stock on `pit_restricted`. Never ask for broker keys, tokens,
  PINs or TOTPs.
- Education and tooling, not investment advice; this pack is not a
  SEBI-registered investment adviser or research analyst.

Built on Jake Schincariol's MIT-licensed Day Trader skill for US markets.
