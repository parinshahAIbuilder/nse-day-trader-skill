# NSE Day Trader skill

Nine Claude skills for intraday trading on the **National Stock Exchange of
India**: scan charts for setups, track the news, time every entry and exit on
the IST clock, replay 10,000 sessions before you risk a rupee, size every
trade in rupees **after STT and charges**, block trades that breach **SEBI's
insider-trading rules**, and turn an approved trade into **Zerodha Kite**
order calls.

Paper trading is the default, and no order goes anywhere until you say yes.

> Built on Jake Schincariol's MIT-licensed
> [Day Trader skill](https://github.com/Jakeschincariol/day-trader-skill) for
> US markets. This edition re-engineers it for India: NSE session and
> holidays, `.NS` symbols, NSE tick sizes, the EQ-series universe, rupee
> sizing net of Indian intraday charges, MIS square-off timing, a SEBI PIT
> guard and Kite order shapes. The US-only skills (SEC earnings analyst,
> Congress trade tracker) are not included.

## What changes for India

| | US original | NSE edition |
| --- | --- | --- |
| Session | 9:30 to 16:00 ET, DST | 9:15 to 15:30 IST, no DST |
| Holidays | NYSE | NSE equity 2026 (Dussehra, Diwali-Balipratipada, ...) |
| Symbols | TSLA | RELIANCE, M&M, BAJAJ-AUTO (`.NS` added for Yahoo, `.BO` kept) |
| Universe | Nasdaq Trader lists | NSE `EQUITY_L.csv`, EQ series only (BE / BZ cannot be day traded) |
| Prices | to the cent | NSE tick bands: ₹0.01 / 0.05 / 0.10 / 0.50 / 1 / 5 |
| Sizing | dollars, max loss at the stop | rupees, max loss **including** brokerage, STT, NSE, SEBI, stamp, GST |
| Defaults | flat by 15:55 | entries 9:30 to 14:45, flat by 15:10 (before MIS auto square-off) |
| Compliance | FINRA PDT | **SEBI PIT**: your own company's stock is always blocked |
| Broker | Alpaca bracket | Zerodha Kite: MIS BUY, then SL-M stop; target by alert |

## The nine

| command | job |
| --- | --- |
| `/nse-scanner` | Scans NSE charts: opening-range breakouts, 20-day breakouts, flags, oversold bounces |
| `/nse-news` | New headlines per stock, tagged (results, promoter, SEBI, circuit, RBI...), plus the exchange-filings checklist |
| `/nse-entry` | Watches a setup bar by bar and says the moment the breakout bar closes |
| `/nse-exit` | Sets stop and target on NSE ticks before you enter; flat by 15:10 |
| `/nse-timemachine` | Replays the rest of the day 10,000 times from the stock's own bars, plus every past day the rule fired |
| `/nse-budget` | Sizes to your rupee budget and max loss, net of charges |
| `/nse-risk` | PASS or BLOCK against your limits, the entry window and the SEBI PIT list |
| `/nse-broker` | Shows the Kite order calls, records your yes, paper by default |
| `/nse-alerts` | Rings on the stop, the target, flat-by time and the 9:15 bell, on the NSE calendar |

```
scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> broker   (one trade)
alerts: stop, target, flat by 15:10                                           (while it is open)
```

## Install

Claude Code, as a plugin:

```
/plugin marketplace add parinshahAIbuilder/nse-day-trader-skill
/plugin install nse-day-trader-skill@nse-day-trader-skill
```

Or copy the folders:

```bash
git clone https://github.com/parinshahAIbuilder/nse-day-trader-skill.git
cp -r nse-day-trader-skill/nse-* ~/.claude/skills/
```

Python 3.8 or newer, standard library only. Copy
`nse-budget/config.example.json` to `trader/config.json` in your project and
set your budget, limits and `pit_restricted` list.

## Example (from the tests)

The offline fixtures are **synthetic**: real 5-minute bars re-timed to the
NSE session, symbol DEMO. Monday 19 Oct 2026, 10:00 IST:

1. Entry: range 9:15 to 9:30 was 1,417.88 to 1,448.72; the 9:55 bar closed at
   1,456.70, triggered.
2. Exit: stop 1,433.30 (range midpoint, on the tick), target 1,480.10 (1R),
   risk ₹23.40 a share, flat by 15:10.
3. Time Machine: target first 28.8 %, stop first 23.8 %, neither 47.5 %. On
   57 past sessions the rule fired 32 times: 13 target, 15 stop. A coin flip.
4. Budget (₹1,00,000, ₹1,000 max loss): 40 shares, ₹58,268. Charges about
   ₹61 if stopped out, so ₹997 all in. By price alone it would have been 42.
5. Risk: PASS. With DEMO on your `pit_restricted` list: BLOCK, SEBI PIT.
6. Broker: Kite `place_order` BUY 40 MIS MARKET, then SL-M SELL at 1,433.30.

```bash
python3 -m unittest discover -s tests -v
```

## Fine print

**Not investment advice.** This is software that reads public data and does
arithmetic. It is not a SEBI-registered investment adviser or research
analyst, and it does not know your situation. SEBI's own studies of
individual intraday traders find that most of them lose money. Decide for
yourself and talk to a SEBI-registered adviser if you need advice.

**Insiders.** If you are a designated person or insider of a listed company,
put its symbol in `pit_restricted`. A day trade in it is a contra trade, and
any trade needs an open trading window and pre-clearance. The tool will not
help you trade it.

**Paper first.** `"paper": true` is the default. Zerodha has no paper account,
so paper here means no broker call at all. Zerodha's hosted Kite MCP leaves
out trading tools; order placement needs a self-hosted Kite MCP with your own
Kite Connect key. Set every order tool to "Needs approval".

**The data is free and unofficial.** Yahoo Finance's chart endpoint can lag,
change or rate-limit you, and reads 5-minute bars, not ticks. Check anything
that matters against your broker before you act on it.

**Charges are estimates.** Defaults follow a discount broker's intraday
schedule (₹20 or 0.03 % brokerage, STT 0.025 % on the sell, NSE 0.00297 %,
SEBI ₹10 a crore, stamp 0.003 % on the buy, GST 18 %). Your contract note rules.

**Calendar.** NSE's 2026 holidays are built in. Add 2027 when NSE publishes it.
Muhurat trading is a special session the tools treat as closed.

**Taxes.** Intraday equity gains are speculative business income in India.
Nothing here is tax advice.

## Credits and licence

MIT. Original Day Trader skill © 2026 Jake Schincariol. NSE / India edition
© 2026 Parin Shah.
