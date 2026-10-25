---
name: nse-news
description: >-
  News Monitor for NSE stocks. Tracks breaking news on every stock you watch
  and flags what moves it: Yahoo Finance headlines per ticker (RELIANCE.NS
  and so on) and how far each stock is from yesterday's close, plus a
  checklist for the NSE corporate announcements page (results dates, board
  meetings, block deals, pledges). Shows only what is new since the last
  check, newest first, in IST, tags each item (earnings, analyst, deal,
  capital, promoter, regulatory, circuit, macro...) and never repeats one.
  Use when the user says "any news on RELIANCE", "why is this moving",
  "what's the news on my watchlist", "morning briefing", "pre-market news",
  "monitor the news", "did anything break", "check the announcements", or
  after /nse-scanner finds setups.
argument-hint: "[tickers]"
---

# nse-news

A chart can look perfect and still be about to gap on a results date, a
promoter pledge, an SEBI order or a block deal. Before a setup becomes a
trade, this skill reads the headlines for every stock on the watchlist and
tells you only what is new, next to how far each stock has moved today.

Tool in this folder (standard library Python, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py"                              # watchlist + plan + portfolio
python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers RELIANCE TCS --hours 12
python3 "${CLAUDE_SKILL_DIR}/news.py" --json
```

## What it reads and writes

| file / feed | what |
| --- | --- |
| `--tickers`, else `trader/watchlist.json` | the scanner's hits (nse-scanner) |
| `trader/plan.json` | the trade you are planning (nse-exit) |
| `trader/portfolio.json` | what you hold (nse-broker records fills there) |
| Yahoo Finance headline RSS | `https://feeds.finance.yahoo.com/rss/2.0/headline?s=RELIANCE.NS&region=IN&lang=en-IN` |
| Yahoo Finance chart endpoint | the last price against yesterday's close (unofficial, can lag) |
| `trader/state/news.json` | what has been shown (kept 30 days) |

A plain NSE symbol gets `.NS` added for Yahoo; give `.BO` for a BSE listing.

## Exchange filings: the manual check

NSE's corporate announcements (nseindia.com > Companies > Corporate
announcements, and the BSE equivalent) carry the filings that move a stock
first: board meeting intimations, financial results, outcomes of board
meetings, credit ratings, pledges, SAST disclosures, trading window closures.
The NSE site needs a browser session, so the script does not read it. When a
trade is on, open the company's announcements page in the browser and check:

1. Is a **board meeting for results** inside the trade? (Results are usually
   announced during or just after market hours.)
2. Any **outcome of board meeting**, fund raise (QIP, preferential, rights),
   or **credit rating** action today?
3. Any **promoter pledge** or SAST filing, or **bulk / block deal** today?
4. Is the stock in **ASM / GSM** surveillance or a **price band** that can
   lock it at the circuit?

## The rules

- **Headlines are leads, not facts.** Before telling the user something
  happened, open the story or the filing and read it. Quote the source and
  link it. Never summarise a paywalled story you could not read: say so.
- **Filings beat headlines.** If an exchange filing and a headline disagree,
  the filing is what the company told the exchange.
- **Say what it touches.** Each item lists its tickers and tags: earnings,
  analyst, legal, deal, capital, promoter, people, risk, halt (circuit, ASM,
  GSM, suspension), regulatory (SEBI, RBI, CCI, USFDA...), macro (RBI policy,
  CPI, FII / DII flows, Nifty, the Budget).
- **MOVING** marks a stock 3 % or more from yesterday's close (`--move-pct`).
  A move with no headline is worth saying out loud too.
- **No predictions.** Report what was published and how far the price is from
  yesterday's close. Never say what the stock will do next.

## Steps

1. Run the check: `python3 "${CLAUDE_SKILL_DIR}/news.py"`. Exit code 1 means
   nothing new. `--all` shows items already seen, `--json` gives everything
   machine readable, `--dry-run` leaves the state alone.
2. Read the items that matter first: anything tagged earnings, halt, legal,
   regulatory, promoter or risk, and every MOVING ticker.
3. Do the exchange-filings check above for the stock you plan to trade.
4. Write a short brief, per ticker: what happened, the source and link, the
   move, and one line on why it matters to the setup (for example "results
   are due this week: an intraday trade must be out before the close").
5. If a story changes the picture, say so before `/nse-entry` and
   `/nse-exit`. Never act on a headline alone.

During the session: `/loop 15m /nse-news`, or a scheduled task each morning
before 9:00 IST.

## What it never does

- Never invents or embellishes a story, a quote or a number.
- Never trades on news, and never tells the user to buy or sell because of a headline.
- Never hammers a server: one request per feed per ticker, with a pause between calls.
- Never treats unpublished information as news. If the user has inside
  knowledge of a company (UPSI), the stock does not get traded: that is
  SEBI's insider trading rule, and nse-risk's `pit_restricted` list enforces it.

## Safety

Education and tooling, not investment advice. This skill only reads and
reports. Nothing places an order except `/nse-broker`, after `/nse-risk`
passes and the user types an explicit yes for that order.
