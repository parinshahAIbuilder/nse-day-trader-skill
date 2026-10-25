#!/usr/bin/env python3
"""NSE Exchange Data for nse-exchange: read NSE India's own website data. Standard library only.

    python3 nse_api.py status                              # is the NSE open, Nifty 50 last
    python3 nse_api.py holidays --write                    # NSE trading holidays -> trader/nse_holidays.json
    python3 nse_api.py quote RELIANCE TCS --write          # price, circuit band, surveillance -> trader/prices.json
    python3 nse_api.py announcements RELIANCE --days 3     # corporate announcements filed with NSE
    python3 nse_api.py events RELIANCE                     # board meetings coming up (results, fund raise)
    python3 nse_api.py deals --symbol RELIANCE             # today's bulk and block deals
    python3 nse_api.py check RELIANCE --write              # all of the above for one stock -> trader/state/nse_check.json

Sources (NSE's website, the same data the pages show):
  https://www.nseindia.com/api/marketStatus
  https://www.nseindia.com/api/holiday-master?type=trading
  https://www.nseindia.com/api/quote-equity?symbol=<SYMBOL>
  https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=<SYMBOL>
  https://www.nseindia.com/api/event-calendar?index=equities&symbol=<SYMBOL>
  https://nsearchives.nseindia.com/content/equities/bulk.csv and block.csv

The website API is not a licensed data feed. It needs a browser-like session (the tool
opens www.nseindia.com first for cookies), it can change without notice, and NSE limits
automated use: one request at a time, a pause between requests (--pause, default 1 s),
nothing in a tight loop. For licensed or real-time data use NSE's data products or your
broker's API. Personal use, read only.

--offline DIR reads DIR/<name>.json (marketStatus, holiday-master, quote-<SYM>,
announcements-<SYM>, events-<SYM>) and DIR/bulk.csv, DIR/block.csv instead.
--now "2026-10-26 10:00" pins the clock (IST).
"""

import argparse
import csv
import datetime
import gzip
import http.cookiejar
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://www.nseindia.com"
ARCH = "https://nsearchives.nseindia.com/content/equities/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36 nse-day-trader-skill/1.0")
IST = datetime.timedelta(hours=5, minutes=30)
EPOCH = datetime.datetime(1970, 1, 1)
SLEEP = time.sleep

# announcement subjects that move a stock or matter to a day trade
IMPORTANT = [
    ("results", r"financial result|outcome of board meeting|board meeting intimation|results"),
    ("fund raise", r"\bQIP\b|preferential|rights issue|fund rais|allotment|\bNCD\b|debenture"),
    ("rating", r"credit rating"),
    ("promoter", r"pledge|encumbrance|\bSAST\b|acquisition of shares|promoter"),
    ("corporate action", r"dividend|bonus|split|buyback|buy back|record date|demerger|amalgamation|scheme"),
    ("people", r"resignation|appointment|cessation|change in director|key managerial|KMP"),
    ("trading window", r"trading window"),
    ("legal / regulatory", r"\bSEBI\b|litigation|penalty|order|show cause|tax demand|GST|search|investigation"),
    ("deal", r"acquisition|order win|award of|contract|agreement|joint venture|\bMoU\b"),
    ("clarification", r"clarification|price movement|spurt"),
]


class NSEError(Exception):
    pass


# ---------------------------------------------------------------- time

def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)


def to_ist(utc):
    return utc + IST


def parse_now(text):
    if not text:
        return utc_now()
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise NSEError('--now wants an IST time like "2026-10-26 10:00"')
    if dt.tzinfo is None:
        return dt - IST
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_nse_date(text):
    """'21-Oct-2026 22:44:39', '05-Aug-2005', '21-OCT-2026' -> naive IST datetime (or None)."""
    if not text:
        return None
    t = " ".join(str(text).split())
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%d %b %Y %H:%M:%S", "%d-%m-%Y"):
        try:
            return datetime.datetime.strptime(t.title() if "%b" in fmt else t, fmt)
        except ValueError:
            continue
    return None


def iso_utc(ist_dt):
    return (ist_dt - IST).strftime("%Y-%m-%dT%H:%M:%SZ") if ist_dt else None


# ---------------------------------------------------------------- fetching

class Client:
    """A polite NSE website session: opens the home page once for cookies, one request at a time."""

    def __init__(self, offline=None, pause=1.0, timeout=20):
        self.offline = offline
        self.pause = pause
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.primed = False
        self.calls = 0

    def _open(self, url, referer=None, accept="application/json, text/plain, */*"):
        if self.calls and self.pause:
            SLEEP(self.pause)
        self.calls += 1
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Accept": accept, "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip", "Referer": referer or BASE + "/"})
        with self.opener.open(req, timeout=self.timeout) as r:
            data = r.read()
            if r.headers.get("Content-Encoding") == "gzip" or data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
        return data.decode("utf-8", errors="replace")

    def prime(self):
        if not self.primed:
            try:
                self._open(BASE + "/", accept="text/html,application/xhtml+xml")
            except (urllib.error.URLError, OSError) as e:
                raise NSEError("could not open www.nseindia.com for a session: %s" % getattr(e, "reason", e))
            self.primed = True

    def api(self, path, name, referer=None):
        if self.offline:
            return json.loads(_read_offline(self.offline, name + ".json"))
        self.prime()
        url = BASE + path
        for attempt in (1, 2):
            try:
                text = self._open(url, referer)
                return json.loads(text)
            except urllib.error.HTTPError as e:
                if e.code in (401, 403) and attempt == 1:
                    self.primed = False          # cookies expired: open the home page again
                    self.prime()
                    continue
                raise NSEError("NSE answered HTTP %d for %s" % (e.code, path))
            except ValueError:
                if attempt == 1:
                    self.primed = False
                    self.prime()
                    continue
                raise NSEError("NSE sent something that is not JSON for %s (the site may have changed)" % path)
            except (urllib.error.URLError, OSError) as e:
                raise NSEError("could not reach NSE for %s: %s" % (path, getattr(e, "reason", e)))

    def archive(self, fname):
        if self.offline:
            return _read_offline(self.offline, fname)
        try:
            return self._open(ARCH + fname, accept="text/csv,*/*")
        except urllib.error.HTTPError as e:
            raise NSEError("nsearchives answered HTTP %d for %s" % (e.code, fname))
        except (urllib.error.URLError, OSError) as e:
            raise NSEError("could not reach nsearchives for %s: %s" % (fname, getattr(e, "reason", e)))


def _read_offline(folder, name):
    for n in (name, name + ".gz"):
        p = os.path.join(folder, n)
        if os.path.exists(p):
            if p.endswith(".gz"):
                with gzip.open(p, "rt", encoding="utf-8") as fh:
                    return fh.read()
            with open(p, encoding="utf-8") as fh:
                return fh.read()
    raise NSEError("no offline file %s in %s" % (name, folder))


def clean_symbol(s):
    s = str(s).strip().upper()
    return s[:-3] if s.endswith(".NS") else s


# ---------------------------------------------------------------- parsers

def parse_status(doc):
    rows = []
    for m in (doc or {}).get("marketState") or []:
        rows.append({"market": m.get("market"), "status": m.get("marketStatus"), "trade_date": m.get("tradeDate"),
                     "index": m.get("index"), "last": m.get("last"), "change": m.get("variation"),
                     "pct": m.get("percentChange"), "message": m.get("marketStatusMessage")})
    cm = next((r for r in rows if str(r["market"]).lower().startswith("capital")), rows[0] if rows else None)
    return {"capital_market": cm, "markets": rows}


def parse_holidays(doc, segment="CM"):
    """holiday-master -> {YYYY-MM-DD: description}, weekdays and weekends alike (weekends are closed anyway)."""
    out = {}
    for r in (doc or {}).get(segment) or []:
        d = parse_nse_date(r.get("tradingDate"))
        if d:
            out[d.date().isoformat()] = " ".join(str(r.get("description") or "").split())
    if not out:
        raise NSEError("no %s holidays in NSE's answer" % segment)
    return out


def parse_quote(doc, sym):
    if not isinstance(doc, dict) or not doc.get("priceInfo"):
        raise NSEError("no quote for %s (not an NSE symbol, or NSE sent no price)" % sym)
    info, meta = doc.get("info") or {}, doc.get("metadata") or {}
    pi, si = doc.get("priceInfo") or {}, doc.get("securityInfo") or {}
    hl = pi.get("intraDayHighLow") or {}
    surv = si.get("surveillance") or {}
    t = parse_nse_date(meta.get("lastUpdateTime"))

    def f(x):
        try:
            return float(str(x).replace(",", ""))
        except (TypeError, ValueError):
            return None
    q = {
        "symbol": clean_symbol(info.get("symbol") or sym), "name": info.get("companyName"),
        "series": meta.get("series"), "status": meta.get("status") or si.get("tradingStatus"),
        "price": f(pi.get("lastPrice")), "prev_close": f(pi.get("previousClose")), "open": f(pi.get("open")),
        "change": f(pi.get("change")), "pct": f(pi.get("pChange")), "vwap": f(pi.get("vwap")),
        "day_high": f(hl.get("max")), "day_low": f(hl.get("min")),
        "lower_circuit": f(pi.get("lowerCP")), "upper_circuit": f(pi.get("upperCP")),
        "price_band": pi.get("pPriceBand"),
        "surveillance": (surv.get("surv") or None), "surveillance_desc": surv.get("desc"),
        "time": iso_utc(t), "time_ist": t.strftime("%Y-%m-%d %H:%M") if t else None, "source": "nse",
    }
    if q["price"] is None:
        raise NSEError("NSE sent no last price for %s" % sym)
    return q


def tag_subject(text):
    t = text or ""
    return [tag for tag, rx in IMPORTANT if re.search(rx, t, re.I)]


def parse_announcements(doc, sym, since_ist=None):
    rows = doc if isinstance(doc, list) else (doc or {}).get("data") or []
    out = []
    for r in rows:
        d = parse_nse_date(r.get("an_dt") or r.get("sort_date"))
        if since_ist and d and d < since_ist:
            continue
        subject = " ".join(str(r.get("desc") or "").split())
        text = " ".join(str(r.get("attchmntText") or "").split())
        out.append({"id": "nse:%s" % (r.get("seq_id") or ""), "symbol": clean_symbol(r.get("symbol") or sym),
                    "time_ist": d.strftime("%Y-%m-%d %H:%M") if d else r.get("an_dt"), "subject": subject,
                    "text": text, "pdf": r.get("attchmntFile"), "tags": tag_subject(subject + " " + text)})
    out.sort(key=lambda x: x["time_ist"] or "", reverse=True)
    return out


def parse_events(doc, sym, today_ist):
    rows = doc if isinstance(doc, list) else (doc or {}).get("data") or []
    out = []
    for r in rows:
        d = parse_nse_date(r.get("date"))
        if not d or d.date() < today_ist:
            continue
        out.append({"symbol": clean_symbol(r.get("symbol") or sym), "date": d.date().isoformat(),
                    "purpose": " ".join(str(r.get("purpose") or "").split()),
                    "detail": " ".join(str(r.get("bm_desc") or "").split()),
                    "days_away": (d.date() - today_ist).days})
    out.sort(key=lambda x: x["date"])
    return out


def parse_deals(text, kind, symbol=None):
    rdr = csv.reader(io.StringIO(text.lstrip("﻿")))
    try:
        hdr = [h.strip() for h in next(rdr)]
    except StopIteration:
        return []
    low = [h.lower() for h in hdr]

    def col(*names):
        for n in names:
            for i, h in enumerate(low):
                if h.startswith(n):
                    return i
        return None
    ix = {"date": col("date"), "symbol": col("symbol"), "name": col("security name"), "client": col("client"),
          "side": col("buy/sell", "buy / sell"), "qty": col("quantity"), "price": col("trade price", "price")}
    if ix["symbol"] is None:
        raise NSEError("%s.csv does not look like NSE's deals file" % kind)
    out = []
    for row in rdr:
        if not row or len(row) <= ix["symbol"]:
            continue
        g = lambda k: row[ix[k]].strip() if ix[k] is not None and ix[k] < len(row) else ""  # noqa: E731
        s = g("symbol").upper()
        if symbol and s != symbol:
            continue
        try:
            qty = int(float(g("qty").replace(",", "")))
        except ValueError:
            qty = None
        try:
            px = float(g("price").replace(",", ""))
        except ValueError:
            px = None
        out.append({"kind": kind, "date": g("date"), "symbol": s, "name": g("name"), "client": g("client"),
                    "side": g("side").upper(), "qty": qty, "price": px,
                    "value_cr": round(qty * px / 1e7, 2) if qty and px else None})
    return out


# ---------------------------------------------------------------- files

def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def merge_prices(path, quotes, now_utc):
    """Write NSE quotes into trader/prices.json in the shape nse-risk and nse-budget read."""
    old = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                old = json.load(fh)
        except (ValueError, OSError):
            old = {}
    prices, detail = dict(old.get("prices", {})), dict(old.get("detail", {}))
    stamp = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    for q in quotes:
        prices[q["symbol"]] = q["price"]
        detail[q["symbol"]] = {"prev_close": q["prev_close"], "time": q["time"], "source": "nse", "fetched": stamp,
                               "lower_circuit": q["lower_circuit"], "upper_circuit": q["upper_circuit"],
                               "series": q["series"], "surveillance": q["surveillance"]}
    write_json(path, {"as_of": stamp, "source": "nse",
                      "note": "NSE website quotes (delayed, unofficial). Your broker's price is the one that fills.",
                      "prices": prices, "detail": detail})


# ---------------------------------------------------------------- commands

def money(x):
    return "-" if x is None else "₹{:,.2f}".format(x)


def cmd_status(c, a, now):
    s = parse_status(c.api("/api/marketStatus", "marketStatus"))
    if a.json:
        print(json.dumps(s, indent=2))
        return 0
    cm = s["capital_market"]
    if not cm:
        raise NSEError("NSE's market status had no capital market row")
    print("NSE capital market: %s (trade date %s)." % (cm["status"], cm["trade_date"]))
    if cm.get("index"):
        print("%s %s, %s (%s%%)." % (cm["index"], cm["last"], cm["change"], cm["pct"]))
    if cm.get("message"):
        print(cm["message"])
    return 0


def cmd_holidays(c, a, now):
    hol = parse_holidays(c.api("/api/holiday-master?type=trading", "holiday-master"), a.segment)
    years = sorted({int(d[:4]) for d in hol})
    if a.write:
        write_json(a.write, {"source": BASE + "/api/holiday-master?type=trading", "segment": a.segment,
                             "fetched": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "years": years, "holidays": hol})
    if a.json:
        print(json.dumps({"years": years, "holidays": hol}, indent=2))
        return 0
    print("NSE %s trading holidays for %s (%d dates):" % (a.segment, ", ".join(map(str, years)), len(hol)))
    for d, n in sorted(hol.items()):
        wd = datetime.date.fromisoformat(d).strftime("%a")
        print("  %s %s  %s%s" % (d, wd, n, "  (weekend)" if wd in ("Sat", "Sun") else ""))
    if a.write:
        print("Wrote %s. nse-entry, nse-exit, nse-risk, nse-alerts and nse-scanner read it, so the calendar "
              "stays current." % a.write)
    return 0


def get_quote(c, sym):
    sym = clean_symbol(sym)
    path = "/api/quote-equity?symbol=" + urllib.parse.quote(sym)
    return parse_quote(c.api(path, "quote-" + sym, referer=BASE + "/get-quotes/equity?symbol=" + urllib.parse.quote(sym)), sym)


def quote_lines(q):
    out = ["%s  %s  %s (%s%s%%) vs previous close %s, NSE %s IST" % (
        q["symbol"], q["name"] or "", money(q["price"]), "+" if (q["pct"] or 0) >= 0 else "",
        q["pct"] if q["pct"] is not None else "?", money(q["prev_close"]), q["time_ist"] or "?")]
    out.append("  day %s to %s, VWAP %s; circuit band %s to %s (%s%%)" % (
        money(q["day_low"]), money(q["day_high"]), money(q["vwap"]), money(q["lower_circuit"]),
        money(q["upper_circuit"]), q["price_band"] or "?"))
    flags = []
    if q["series"] and q["series"] != "EQ":
        flags.append("series %s: not EQ, cannot be day traded" % q["series"])
    if q["surveillance"]:
        flags.append("surveillance %s%s" % (q["surveillance"], (": " + q["surveillance_desc"]) if q["surveillance_desc"] else ""))
    if q["status"] and str(q["status"]).lower() not in ("listed", "active", "normal"):
        flags.append("status %s" % q["status"])
    if q["upper_circuit"] and q["price"] and q["price"] >= q["upper_circuit"] - 0.001:
        flags.append("AT THE UPPER CIRCUIT: no sellers, you may not get filled")
    if q["lower_circuit"] and q["price"] and q["price"] <= q["lower_circuit"] + 0.001:
        flags.append("AT THE LOWER CIRCUIT: no buyers, a stop may not fill")
    out.extend("  ! " + f for f in flags)
    return out


def cmd_quote(c, a, now):
    quotes, errors = [], []
    for s in a.symbols:
        try:
            quotes.append(get_quote(c, s))
        except NSEError as e:
            errors.append(str(e))
    if a.write and quotes:
        merge_prices(a.write, quotes, now)
    if a.json:
        print(json.dumps({"quotes": quotes, "errors": errors}, indent=2))
    else:
        for q in quotes:
            print("\n".join(quote_lines(q)))
        for e in errors:
            print("error: " + e)
        if a.write and quotes:
            print("Wrote %d price%s to %s (nse-budget and nse-risk use a price under 15 minutes old)."
                  % (len(quotes), "" if len(quotes) == 1 else "s", a.write))
    return 0 if quotes else 1


def get_announcements(c, sym, days, now):
    sym = clean_symbol(sym)
    since = to_ist(now) - datetime.timedelta(days=days)
    doc = c.api("/api/corporate-announcements?index=equities&symbol=" + urllib.parse.quote(sym), "announcements-" + sym)
    return parse_announcements(doc, sym, since)


def cmd_announcements(c, a, now):
    rows = []
    for s in a.symbols:
        rows += get_announcements(c, s, a.days, now)
    if a.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0 if rows else 1
    if not rows:
        print("No NSE announcements for %s in the last %d day%s." % (", ".join(map(clean_symbol, a.symbols)), a.days,
                                                                       "" if a.days == 1 else "s"))
        return 1
    print("NSE corporate announcements, last %d day%s, newest first (IST):" % (a.days, "" if a.days == 1 else "s"))
    for r in rows:
        mark = "!" if r["tags"] else " "
        print("%s %s  %-10s %s%s" % (mark, r["time_ist"], r["symbol"], r["subject"],
                                     ("  [" + ", ".join(r["tags"]) + "]") if r["tags"] else ""))
        if r["text"]:
            print("      " + (r["text"][:220] + ("..." if len(r["text"]) > 220 else "")))
        if r["pdf"]:
            print("      " + r["pdf"])
    print("Read the filing (the PDF) before saying what it means. ! = a subject that tends to move the stock.")
    return 0


def get_events(c, sym, now):
    sym = clean_symbol(sym)
    doc = c.api("/api/event-calendar?index=equities&symbol=" + urllib.parse.quote(sym), "events-" + sym)
    return parse_events(doc, sym, to_ist(now).date())


def cmd_events(c, a, now):
    rows = []
    for s in a.symbols:
        rows += get_events(c, s, now)
    if a.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    if not rows:
        print("No board meetings on NSE's event calendar for %s from today." % ", ".join(map(clean_symbol, a.symbols)))
        return 1
    for r in rows:
        when = "TODAY" if r["days_away"] == 0 else "in %d day%s" % (r["days_away"], "" if r["days_away"] == 1 else "s")
        print("%s  %-10s %-24s %s" % (r["date"], r["symbol"], r["purpose"], when))
        if r["detail"]:
            print("      " + r["detail"][:220])
    if any(r["days_away"] == 0 for r in rows):
        print("A board meeting is today: results or a fund raise can land during the session. Think twice about a day trade.")
    return 0


def get_deals(c, symbol=None):
    out, errors = [], []
    for kind in ("bulk", "block"):
        try:
            out += parse_deals(c.archive(kind + ".csv"), kind, clean_symbol(symbol) if symbol else None)
        except NSEError as e:
            errors.append(str(e))
    return out, errors


def cmd_deals(c, a, now):
    rows, errors = get_deals(c, a.symbol)
    if a.json:
        print(json.dumps({"deals": rows, "errors": errors}, indent=2, ensure_ascii=False))
        return 0 if rows else 1
    for e in errors:
        print("error: " + e)
    if not rows:
        print("No bulk or block deals%s in NSE's latest files." % ((" in " + clean_symbol(a.symbol)) if a.symbol else ""))
        return 1
    rows.sort(key=lambda r: -(r["value_cr"] or 0))
    for r in rows[:a.limit]:
        print("%-5s %s  %-10s %-4s %12s @ %-10s  %s cr  %s" % (
            r["kind"], r["date"], r["symbol"], r["side"], "{:,}".format(r["qty"]) if r["qty"] else "?",
            money(r["price"]), r["value_cr"], r["client"]))
    if len(rows) > a.limit:
        print("... %d more (--limit)" % (len(rows) - a.limit))
    return 0


def cmd_check(c, a, now):
    """One stock, everything that can sink a day trade, in one place."""
    sym = clean_symbol(a.symbol)
    res = {"symbol": sym, "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "errors": []}
    lines = ["NSE check for %s, %s IST" % (sym, to_ist(now).strftime("%Y-%m-%d %H:%M"))]
    try:
        q = get_quote(c, sym)
        res["quote"] = q
        lines += quote_lines(q)
        if a.write:
            merge_prices(a.prices, [q], now)
    except NSEError as e:
        res["errors"].append(str(e))
        lines.append("quote: " + str(e))
    try:
        ev = get_events(c, sym, now)
        res["events"] = ev
        soon = [e for e in ev if e["days_away"] <= 7]
        lines.append("Board meetings in the next 7 days: " + (
            "; ".join("%s %s" % (e["date"], e["purpose"]) for e in soon) if soon else "none on the calendar"))
    except NSEError as e:
        res["errors"].append(str(e))
        lines.append("events: " + str(e))
    try:
        an = get_announcements(c, sym, a.days, now)
        res["announcements"] = an
        imp = [x for x in an if x["tags"]]
        lines.append("Announcements in the last %d day%s: %d, %d that tend to move the stock%s" % (
            a.days, "" if a.days == 1 else "s", len(an), len(imp),
            (": " + "; ".join("%s %s" % (x["time_ist"], x["subject"]) for x in imp[:4])) if imp else ""))
    except NSEError as e:
        res["errors"].append(str(e))
        lines.append("announcements: " + str(e))
    deals, errs = get_deals(c, sym)
    res["deals"], res["errors"] = deals, res["errors"] + errs
    lines.append("Bulk / block deals in NSE's latest files: %s" % (
        "; ".join("%s %s %s @ %s" % (d["kind"], d["side"], "{:,}".format(d["qty"] or 0), money(d["price"])) for d in deals)
        if deals else "none"))
    flags = []
    q = res.get("quote") or {}
    if q.get("series") and q["series"] != "EQ":
        flags.append("not EQ series")
    if q.get("surveillance"):
        flags.append("under surveillance (%s)" % q["surveillance"])
    if any(e["days_away"] == 0 for e in res.get("events", [])):
        flags.append("board meeting today")
    if any("results" in x["tags"] for x in res.get("announcements", [])):
        flags.append("results-related filing in the window")
    res["flags"] = flags
    lines.append("Flags: " + (", ".join(flags) if flags else "none") + ".")
    if a.write:
        write_json(a.out, res)
        lines.append("Wrote %s (nse-risk reads it)." % a.out)
    if a.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        print("\n".join(lines))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="NSE India website data for the NSE Day Trader skills (read only).")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--pause", type=float, default=1.0)
    ap.add_argument("--now")
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p = sub.add_parser("holidays")
    p.add_argument("--segment", default="CM")
    p.add_argument("--write", nargs="?", const="trader/nse_holidays.json", metavar="PATH")
    p = sub.add_parser("quote")
    p.add_argument("symbols", nargs="+")
    p.add_argument("--write", nargs="?", const="trader/prices.json", metavar="PATH")
    for name in ("announcements", "events"):
        p = sub.add_parser(name)
        p.add_argument("symbols", nargs="+")
        if name == "announcements":
            p.add_argument("--days", type=int, default=3)
    p = sub.add_parser("deals")
    p.add_argument("--symbol")
    p.add_argument("--limit", type=int, default=25)
    p = sub.add_parser("check")
    p.add_argument("symbol")
    p.add_argument("--days", type=int, default=3)
    p.add_argument("--write", action="store_true")
    p.add_argument("--out", default="trader/state/nse_check.json")
    p.add_argument("--prices", default="trader/prices.json")
    for sp in sub.choices.values():
        sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        sp.add_argument("--offline", metavar="DIR", default=argparse.SUPPRESS)
        sp.add_argument("--now", default=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    try:
        now = parse_now(a.now)
        c = Client(a.offline, a.pause)
        return {"status": cmd_status, "holidays": cmd_holidays, "quote": cmd_quote, "announcements": cmd_announcements,
                "events": cmd_events, "deals": cmd_deals, "check": cmd_check}[a.cmd](c, a, now)
    except NSEError as e:
        print("nse_api.py: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
