#!/usr/bin/env python3
"""Daily Opportunity Scan for nse-opportunities: read many NSE charts, test several technical
setups on each, back-test every setup on that stock's own last year, rank what matched, and
write a report with charts. Standard library only (PNG screenshots need Playwright, optional).

    python3 picks.py scan                                   # bundled liquid watchlist, daily bars
    python3 picks.py scan --index nifty50                   # NSE's own index list (nse-exchange)
    python3 picks.py scan --symbols RELIANCE,TCS,INFY --top 5
    python3 picks.py scan --intraday                        # also today's opening range and VWAP
    python3 picks.py scan --html --png                      # trader/reports/picks-<date>.html (+ .png)
    python3 picks.py scan --offline FILE_OR_DIR --now "2026-10-23 16:00"
    python3 picks.py demo-data OUT.json.gz                  # synthetic charts for tests and screenshots

What it does, per stock (completed daily bars only; today's bar is used after 15:30 IST):
  indicators   EMA 20/50/200, RSI 14 (Wilder), MACD 12/26/9, ATR 14 (Wilder), Bollinger 20/2,
               20-day average volume and traded value, 52-week high, 20-day return vs Nifty 50
  setups       breakout_20d     close above the highest high of the 20 sessions before, volume >= 1.5x
               trend_pullback   uptrend (close > EMA50 > EMA200), dipped to EMA20 in the last 3 bars,
                                closed back above it, RSI 40 to 65
               macd_cross       MACD crossed above its signal in the last 2 bars, close > EMA50
               squeeze_break    Bollinger width in its lowest 15 % of 120 bars, close above the upper band
               near_52w_high    within 3 % of the 52-week high, above EMA20, RSI under 75
               oversold_bounce  RSI under 30 in the last 3 bars, today closed above yesterday's high
  plan         buy-stop entry one tick over today's high; stop at the higher of the 5-bar swing low
               and entry - 1.5 ATR, on NSE ticks; target 2R; time stop 5 sessions
  back-test    every past bar the same setup fired on this stock: enter next session if price
               trades through the entry, stop / target / 5-session exit, same-bar touch counts as
               the stop. Reports signals, % target first, % stop first, average R
  score 0-100  setup quality + trend alignment + relative strength + volume + back-test record,
               minus penalties for thin liquidity, a wide ATR, or a stretched RSI
  size         shares for your max loss (trader/config.json, nse-budget), delivery (CNC) charges

The list is what the rules matched on the data it read, ranked. It is not a recommendation to
buy anything, it does not know the news, and a back-test is history, not a forecast.
Yahoo Finance daily bars (SYMBOL.NS) are free, unofficial and delayed.
"""

import argparse
import datetime
import gzip
import html
import json
import math
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (compatible; nse-day-trader-skill/1.0; +https://github.com/parinshahAIbuilder/nse-day-trader-skill)"
IST = datetime.timedelta(hours=5, minutes=30)
EPOCH = datetime.datetime(1970, 1, 1)
SLEEP = time.sleep
SETUPS = {
    "breakout_20d": ("20-day breakout on volume", 30),
    "trend_pullback": ("Pullback to EMA20 in an uptrend", 28),
    "macd_cross": ("MACD bullish cross above EMA50", 22),
    "squeeze_break": ("Bollinger squeeze breakout", 26),
    "near_52w_high": ("Holding near the 52-week high", 20),
    "oversold_bounce": ("Oversold bounce (counter-trend)", 16),
}
MAX_HOLD = 5
TARGET_R = 2.0
DEFAULT_CFG = {"budget": 100000, "max_loss_per_trade_inr": 1000, "max_order_inr": 80000}
DELIVERY = {"stt_pct": 0.1, "exchange_txn_pct": 0.00297, "sebi_fee_pct": 0.0001, "stamp_buy_pct": 0.015,
            "dp_charge_inr": 15.93, "gst_pct": 18.0, "brokerage_inr": 0.0}


class PickError(Exception):
    pass


# ---------------------------------------------------------------- time

def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)


def parse_now(text):
    if not text:
        return utc_now()
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise PickError('--now wants an IST time like "2026-10-23 16:00"')
    if dt.tzinfo is None:
        return dt - IST
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def ist(utc):
    return utc + IST


# ---------------------------------------------------------------- data

def yahoo_symbol(sym):
    s = sym.strip().upper()
    return s if s.startswith("^") or "." in s else s + ".NS"


def plain(sym):
    s = sym.strip().upper()
    return s[:-3] if s.endswith(".NS") else s


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        return data.decode("utf-8")


def fetch_chart(sym, rng="1y", interval="1d", pause=0.4):
    last = None
    for host in ("query2", "query1"):
        url = "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=%s" % (
            host, urllib.parse.quote(yahoo_symbol(sym)), rng, interval)
        try:
            text = http_get(url)
            SLEEP(pause)
            return json.loads(text)
        except urllib.error.HTTPError as e:
            last = "Yahoo answered HTTP %d for %s" % (e.code, sym)
            if e.code == 429:
                SLEEP(5)
        except (urllib.error.URLError, OSError, ValueError) as e:
            last = "could not read %s from Yahoo: %s" % (sym, getattr(e, "reason", e))
    raise PickError(last)


def bars_from_chart(doc, today_ist, after_close):
    """Yahoo v8 chart -> list of daily bars {d, o, h, l, c, v}; today's bar only once the session is over."""
    try:
        r = doc["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        raise PickError("not a Yahoo chart")
    q = (r.get("indicators") or {}).get("quote", [{}])[0]
    out = []
    for i, ts in enumerate(r.get("timestamp") or []):
        vals = [q.get(k, [None])[i] if i < len(q.get(k) or []) else None for k in ("open", "high", "low", "close", "volume")]
        if any(v is None for v in vals[:4]):
            continue
        d = (EPOCH + datetime.timedelta(seconds=int(ts)) + IST).date()
        if d > today_ist or (d == today_ist and not after_close):
            continue
        o, h, l, c, v = vals
        out.append({"d": d.isoformat(), "o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": float(v or 0)})
    dedup = {}
    for b in out:
        dedup[b["d"]] = b
    return [dedup[k] for k in sorted(dedup)]


def load_offline(path):
    """A bundle {"SYM": <v8 chart>} (.json or .json.gz) or a folder of <SYM>.json."""
    def rd(p):
        op = gzip.open if p.endswith(".gz") else open
        with op(p, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    if os.path.isdir(path):
        out = {}
        for f in sorted(os.listdir(path)):
            for ext in (".json.gz", ".json"):
                if f.endswith(ext):
                    out[f[:-len(ext)].upper()] = rd(os.path.join(path, f))
        return out
    doc = rd(path)
    if isinstance(doc, dict) and "chart" in doc:
        sym = doc["chart"]["result"][0]["meta"]["symbol"]
        return {plain(sym): doc}
    return {k.upper(): v for k, v in doc.items() if not k.startswith("_")}


def read_universe(a):
    if a.symbols:
        return [plain(s) for s in a.symbols.split(",") if s.strip()]
    if a.index:
        api = _load_sibling("nse-exchange", "nse_api")
        c = api.Client(a.nse_offline, 1.0)
        name = {"nifty50": "NIFTY 50", "nifty100": "NIFTY 100", "niftynext50": "NIFTY NEXT 50",
                "fno": "SECURITIES IN F&O", "nifty200": "NIFTY 200"}.get(a.index.lower(), a.index)
        doc = c.api("/api/equity-stockIndices?index=" + urllib.parse.quote(name), "index-" + a.index.lower())
        syms = [plain(r.get("symbol", "")) for r in doc.get("data") or []
                if r.get("symbol") and r.get("symbol") != name and r.get("priority", 0) == 0]
        if not syms:
            raise PickError("NSE sent no constituents for %s" % name)
        return syms
    path = a.watchlist or os.path.join(HERE, "universe", "liquid50.txt")
    with open(path, encoding="utf-8") as fh:
        return [plain(ln.split("|")[0]) for ln in fh if ln.strip() and not ln.startswith("#")]


def _load_sibling(folder, name):
    import importlib.util
    p = os.path.join(os.path.dirname(HERE), folder, name + ".py")
    if not os.path.exists(p):
        raise PickError("%s is not installed next to this skill" % folder)
    spec = importlib.util.spec_from_file_location("nse_" + name, p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_config(path):
    cfg = dict(DEFAULT_CFG)
    try:
        with open(path, encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    except (OSError, ValueError):
        pass
    return cfg


# ---------------------------------------------------------------- indicators

def ema(xs, n):
    out, k, e = [], 2.0 / (n + 1), None
    for i, x in enumerate(xs):
        if i < n - 1:
            out.append(None)
            continue
        e = sum(xs[:n]) / n if e is None else x * k + e * (1 - k)
        out.append(e)
    return out


def wilder(xs, n):
    out, s = [], None
    for i, x in enumerate(xs):
        if i < n - 1 or x is None:
            out.append(None)
            continue
        if s is None:
            vals = [v for v in xs[i - n + 1:i + 1] if v is not None]
            if len(vals) < n:
                out.append(None)
                continue
            s = sum(vals) / n
        else:
            s = (s * (n - 1) + x) / n
        out.append(s)
    return out


def rsi(closes, n=14):
    gains, losses = [None], [None]
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    ag, al = wilder(gains[1:], n), wilder(losses[1:], n)
    out = [None]
    for g, l in zip(ag, al):
        if g is None or l is None:
            out.append(None)
        elif l == 0:
            out.append(100.0)
        else:
            out.append(100 - 100 / (1 + g / l))
    return out


def atr(bars, n=14):
    trs = []
    for i, b in enumerate(bars):
        pc = bars[i - 1]["c"] if i else b["c"]
        trs.append(max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc)))
    return wilder(trs, n)


def sma(xs, n):
    return [None if i < n - 1 else sum(xs[i - n + 1:i + 1]) / n for i in range(len(xs))]


def stdev(xs, n):
    out = []
    for i in range(len(xs)):
        if i < n - 1:
            out.append(None)
            continue
        w = xs[i - n + 1:i + 1]
        m = sum(w) / n
        out.append(math.sqrt(sum((x - m) ** 2 for x in w) / n))
    return out


def indicators(bars):
    c = [b["c"] for b in bars]
    v = [b["v"] for b in bars]
    e12, e26 = ema(c, 12), ema(c, 26)
    macd = [None if a is None or b is None else a - b for a, b in zip(e12, e26)]
    first = next((i for i, x in enumerate(macd) if x is not None), None)
    sig = [None] * len(macd)
    if first is not None:
        s9 = ema(macd[first:], 9)
        sig[first:] = s9
    mid, sd = sma(c, 20), stdev(c, 20)
    width = [None if m is None else (4 * s) / m for m, s in zip(mid, sd)]
    return {"c": c, "ema20": ema(c, 20), "ema50": ema(c, 50), "ema200": ema(c, 200), "rsi": rsi(c),
            "macd": macd, "signal": sig, "atr": atr(bars), "bb_mid": mid,
            "bb_up": [None if m is None else m + 2 * s for m, s in zip(mid, sd)], "bb_width": width,
            "vol20": sma(v, 20), "value20": sma([b["c"] * b["v"] for b in bars], 20)}


# ---------------------------------------------------------------- setups

def setups_at(bars, ind, i):
    """Setups that fire on bar i (completed). Returns {name: one-line reason}."""
    out = {}
    if i < 60:
        return out
    b, c = bars[i], ind["c"]
    e20, e50, e200 = ind["ema20"][i], ind["ema50"][i], ind["ema200"][i]
    r, vol20 = ind["rsi"][i], ind["vol20"][i - 1]
    volx = b["v"] / vol20 if vol20 else 0
    hh20 = max(x["h"] for x in bars[i - 20:i])
    if c[i] > hh20 and volx >= 1.5:
        out["breakout_20d"] = "closed %.2f above the 20-day high %.2f on %.1fx average volume" % (c[i], hh20, volx)
    if e20 and e50 and e200 and c[i] > e50 > e200 and r is not None and 40 <= r <= 65:
        if any(bars[j]["l"] <= ind["ema20"][j] * 1.005 for j in range(i - 2, i + 1)) and c[i] > e20:
            out["trend_pullback"] = "uptrend (close > EMA50 > EMA200), dipped to EMA20 and closed back above it, RSI %.0f" % r
    m, s = ind["macd"], ind["signal"]
    if all(x is not None for x in (m[i], s[i], m[i - 2], s[i - 2])) and e50 and c[i] > e50:
        if m[i] > s[i] and (m[i - 1] <= s[i - 1] or m[i - 2] <= s[i - 2]):
            out["macd_cross"] = "MACD crossed above its signal in the last 2 sessions, price above EMA50"
    w = ind["bb_width"]
    if i >= 140 and w[i - 1] is not None:
        hist = sorted(x for x in w[i - 120:i] if x is not None)
        if hist and w[i - 1] <= hist[int(len(hist) * 0.15)] and c[i] > ind["bb_up"][i]:
            out["squeeze_break"] = "Bollinger bands were at their tightest 15%% of 120 sessions, closed above the upper band"
    hi52 = max(x["h"] for x in bars[max(0, i - 250):i + 1])
    if c[i] >= hi52 * 0.97 and e20 and c[i] > e20 and r is not None and r < 75:
        out["near_52w_high"] = "within %.1f%% of the 52-week high %.2f, above EMA20" % (100 * (1 - c[i] / hi52), hi52)
    rr = ind["rsi"]
    if any(rr[j] is not None and rr[j] < 30 for j in range(i - 2, i + 1)) and c[i] > bars[i - 1]["h"]:
        out["oversold_bounce"] = "RSI was under 30 in the last 3 sessions, closed above yesterday's high"
    return out


# ---------------------------------------------------------------- levels and back-test

def tick_size(p):
    for upto, t in ((250, 0.01), (1000, 0.05), (5000, 0.10), (10000, 0.50), (20000, 1.00)):
        if (p < upto if upto == 250 else p <= upto):
            return t
    return 5.0


def up_tick(x, ref):
    t = tick_size(ref)
    return round(math.ceil(round(x / t, 6)) * t, 2)


def down_tick(x, ref):
    t = tick_size(ref)
    return round(math.floor(round(x / t, 6)) * t, 2)


def plan_at(bars, ind, i):
    b = bars[i]
    a = ind["atr"][i]
    if not a:
        return None
    entry = up_tick(b["h"] + tick_size(b["h"]), b["h"])
    swing = min(x["l"] for x in bars[i - 4:i + 1])
    stop = down_tick(max(swing, entry - 1.5 * a), entry)
    if stop >= entry:
        return None
    risk = round(entry - stop, 2)
    target = up_tick(entry + TARGET_R * risk, entry)
    return {"entry": entry, "stop": stop, "target": target, "risk": risk, "atr": round(a, 2),
            "atr_pct": round(100 * a / b["c"], 2)}


def simulate(bars, i, p):
    """From bar i's plan: enter on bar i+1 if it trades through the entry; up to MAX_HOLD sessions."""
    if i + 1 >= len(bars):
        return None
    nb = bars[i + 1]
    if nb["h"] < p["entry"]:
        return {"result": "no fill"}
    fill = max(p["entry"], nb["o"])
    risk = fill - p["stop"]
    if risk <= 0:
        return {"result": "no fill"}
    for k in range(i + 1, min(i + 1 + MAX_HOLD, len(bars))):
        x = bars[k]
        if x["l"] <= p["stop"]:
            return {"result": "stop", "r": round((p["stop"] - fill) / risk, 2), "days": k - i}
        if x["h"] >= p["target"]:
            return {"result": "target", "r": round((p["target"] - fill) / risk, 2), "days": k - i}
    last = bars[min(i + MAX_HOLD, len(bars) - 1)]
    if i + MAX_HOLD >= len(bars):
        return {"result": "open"}
    return {"result": "time", "r": round((last["c"] - fill) / risk, 2), "days": MAX_HOLD}


def backtest(bars, ind, name, upto):
    res = {"signals": 0, "filled": 0, "target": 0, "stop": 0, "time": 0, "r": []}
    for i in range(60, upto):
        if name not in setups_at(bars, ind, i):
            continue
        p = plan_at(bars, ind, i)
        if not p:
            continue
        res["signals"] += 1
        s = simulate(bars, i, p)
        if not s or s["result"] in ("no fill", "open"):
            continue
        res["filled"] += 1
        res[s["result"]] += 1
        res["r"].append(s["r"])
    n = res["filled"]
    res["win_pct"] = round(100.0 * res["target"] / n, 1) if n else None
    res["stop_pct"] = round(100.0 * res["stop"] / n, 1) if n else None
    res["avg_r"] = round(sum(res["r"]) / n, 2) if n else None
    del res["r"]
    return res


# ---------------------------------------------------------------- scoring

def score(name, why, bars, ind, i, bt, rs20):
    pts = SETUPS[name][1]
    notes = []
    c = ind["c"][i]
    e20, e50, e200 = ind["ema20"][i], ind["ema50"][i], ind["ema200"][i]
    if e50 and e200 and c > e50 > e200:
        pts += 15
        notes.append("trend aligned (above EMA50 and EMA200)")
    elif e50 and c > e50:
        pts += 7
    if rs20 is not None:
        if rs20 > 3:
            pts += 12
            notes.append("outperformed Nifty 50 by %.1f pts over 20 sessions" % rs20)
        elif rs20 > 0:
            pts += 6
        elif rs20 < -5:
            pts -= 6
            notes.append("lagging Nifty 50 by %.1f pts" % -rs20)
    vol20 = ind["vol20"][i - 1]
    volx = bars[i]["v"] / vol20 if vol20 else 0
    if volx >= 2:
        pts += 10
        notes.append("volume %.1fx its 20-day average" % volx)
    elif volx >= 1.3:
        pts += 5
    if bt["filled"] >= 5 and bt["avg_r"] is not None:
        if bt["avg_r"] > 0.3:
            pts += 15
        elif bt["avg_r"] > 0:
            pts += 8
        elif bt["avg_r"] < -0.2:
            pts -= 10
            notes.append("this setup lost money on this stock over the last year")
    elif bt["filled"] < 3:
        pts -= 4
        notes.append("too few past signals to judge")
    r = ind["rsi"][i]
    if r is not None and r > 75:
        pts -= 8
        notes.append("RSI %.0f is stretched" % r)
    a = ind["atr"][i]
    if a and a / c > 0.05:
        pts -= 8
        notes.append("wide daily range (ATR %.1f%%)" % (100 * a / c))
    val = ind["value20"][i]
    if val is not None and val < 20e7:
        pts -= 12
        notes.append("thin: ₹%.0f cr a day traded" % (val / 1e7))
    return max(0, min(100, int(round(pts)))), notes


def delivery_charges(qty, buy, sell):
    k = DELIVERY
    bv, sv = qty * buy, qty * sell
    stt = round((bv + sv) * k["stt_pct"] / 100)
    exch = (bv + sv) * k["exchange_txn_pct"] / 100
    sebi = (bv + sv) * k["sebi_fee_pct"] / 100
    stamp = round(bv * k["stamp_buy_pct"] / 100)
    gst = (k["brokerage_inr"] * 2 + exch + sebi + k["dp_charge_inr"] / 1.18) * k["gst_pct"] / 100
    return round(stt + exch + sebi + stamp + k["dp_charge_inr"] / 1.18 + gst + k["brokerage_inr"] * 2, 2)


def size(p, cfg):
    max_loss = float(cfg.get("max_loss_per_trade_inr") or 1000)
    cap = float(cfg.get("max_order_inr") or 80000)
    q = int(max_loss // p["risk"]) if p["risk"] > 0 else 0
    q = min(q, int(cap // p["entry"]))
    while q > 0 and q * p["risk"] + delivery_charges(q, p["entry"], p["stop"]) > max_loss:
        q -= 1
    if q <= 0:
        return {"qty": 0, "note": "one share risks more than your max loss"}
    ch = delivery_charges(q, p["entry"], p["stop"])
    return {"qty": q, "cost": round(q * p["entry"], 2), "loss_at_stop": round(q * p["risk"] + ch, 2),
            "gain_at_target": round(q * (p["target"] - p["entry"]) - delivery_charges(q, p["entry"], p["target"]), 2),
            "charges_at_stop": ch}


# ---------------------------------------------------------------- intraday (optional)

def intraday_status(chart_doc, today_ist):
    """Today's 15-minute opening range and VWAP from 5-minute bars."""
    try:
        r = chart_doc["chart"]["result"][0]
        q = r["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError):
        return None
    bars = []
    for i, ts in enumerate(r.get("timestamp") or []):
        t = EPOCH + datetime.timedelta(seconds=int(ts)) + IST
        if t.date() != today_ist or q["close"][i] is None:
            continue
        bars.append((t, q["high"][i], q["low"][i], q["close"][i], q["volume"][i] or 0))
    if len(bars) < 4:
        return None
    rng = [b for b in bars if b[0].hour == 9 and b[0].minute < 30]
    if len(rng) < 3:
        return None
    hi, lo = max(b[1] for b in rng), min(b[2] for b in rng)
    pv = sum((b[1] + b[2] + b[3]) / 3 * b[4] for b in bars)
    vv = sum(b[4] for b in bars)
    vwap = pv / vv if vv else None
    last = bars[-1][3]
    brk = next((b for b in bars[3:] if b[3] > hi), None)
    return {"or_high": round(hi, 2), "or_low": round(lo, 2), "last": round(last, 2), "vwap": round(vwap, 2) if vwap else None,
            "above_vwap": bool(vwap and last > vwap),
            "orb": ("broke out at %s" % brk[0].strftime("%H:%M")) if brk else ("inside the range" if last >= lo else "below the range")}


# ---------------------------------------------------------------- the scan

def scan(charts, universe, now, cfg, top, index_chart=None, intraday=None):
    today = ist(now).date()
    after_close = ist(now).time() >= datetime.time(15, 30)
    idx_bars = bars_from_chart(index_chart, today, after_close) if index_chart else []
    idx_by_d = {b["d"]: b["c"] for b in idx_bars}
    picks, failed, scanned, as_of = [], {}, 0, None
    for sym in universe:
        doc = charts.get(sym)
        if doc is None:
            failed[sym] = "no data"
            continue
        try:
            bars = bars_from_chart(doc, today, after_close)
        except PickError as e:
            failed[sym] = str(e)
            continue
        if len(bars) < 160:
            failed[sym] = "only %d daily bars (needs 160)" % len(bars)
            continue
        scanned += 1
        ind = indicators(bars)
        i = len(bars) - 1
        as_of = max(as_of or bars[i]["d"], bars[i]["d"])
        fired = setups_at(bars, ind, i)
        if not fired:
            continue
        rs20 = None
        d0, d1 = bars[i - 20]["d"], bars[i]["d"]
        if d0 in idx_by_d and d1 in idx_by_d:
            rs20 = 100 * (bars[i]["c"] / bars[i - 20]["c"] - idx_by_d[d1] / idx_by_d[d0])
        p = plan_at(bars, ind, i)
        if not p:
            continue
        best = None
        for name, why in fired.items():
            bt = backtest(bars, ind, name, i)
            sc, notes = score(name, why, bars, ind, i, bt, rs20)
            cand = {"setup": name, "setup_label": SETUPS[name][0], "why": why, "score": sc, "notes": notes, "backtest": bt}
            if best is None or sc > best["score"]:
                best = cand
        others = [SETUPS[n][0] for n in fired if n != best["setup"]]
        pick = {"symbol": sym, "date": bars[i]["d"], "close": round(bars[i]["c"], 2),
                "change_pct": round(100 * (bars[i]["c"] / bars[i - 1]["c"] - 1), 2),
                "rsi": round(ind["rsi"][i], 1) if ind["rsi"][i] is not None else None,
                "rs20_vs_nifty": round(rs20, 1) if rs20 is not None else None,
                "value20_cr": round(ind["value20"][i] / 1e7, 1) if ind["value20"][i] else None,
                "also": others, "plan": p, "size": size(p, cfg), **best,
                "_bars": bars[-90:], "_ema20": ind["ema20"][-90:], "_ema50": ind["ema50"][-90:]}
        if intraday and sym in intraday:
            pick["intraday"] = intraday_status(intraday[sym], today)
        picks.append(pick)
    picks.sort(key=lambda x: (-x["score"], -(x["backtest"]["avg_r"] or -9), x["symbol"]))
    return {"as_of_bar": as_of, "run_at_ist": ist(now).strftime("%Y-%m-%d %H:%M"), "universe": len(universe),
            "scanned": scanned, "failed": failed, "matched": len(picks), "top": picks[:top], "all": picks}


# ---------------------------------------------------------------- output

def money(x):
    return "₹{:,.2f}".format(x)


def text_report(res):
    L = ["NSE daily opportunity scan, bars to %s (run %s IST). %d of %d charts read, %d matched a setup."
         % (res["as_of_bar"], res["run_at_ist"], res["scanned"], res["universe"], res["matched"])]
    if res["failed"]:
        L.append("Could not read %d: %s" % (len(res["failed"]), ", ".join(sorted(res["failed"])[:8]) +
                                              (" ..." if len(res["failed"]) > 8 else "")))
    L.append("")
    L.append(" #  %-13s %5s  %-34s %10s %10s %10s  %-15s %s" % ("symbol", "score", "setup", "entry >", "stop", "target",
                                                                 "hit target/n", "avg R"))
    for k, p in enumerate(res["top"], 1):
        bt, pl = p["backtest"], p["plan"]
        L.append("%2d  %-13s %5d  %-34s %10.2f %10.2f %10.2f  %-15s %s" % (
            k, p["symbol"], p["score"], p["setup_label"][:34], pl["entry"], pl["stop"], pl["target"],
            ("%.0f%% of %d" % (bt["win_pct"], bt["filled"])) if bt["filled"] else "no history",
            ("%+.2f" % bt["avg_r"]) if bt["avg_r"] is not None else "-"))
    L.append("")
    for k, p in enumerate(res["top"], 1):
        s = p["size"]
        L.append("%d. %s %s: %s." % (k, p["symbol"], p["setup_label"], p["why"]))
        if p["notes"]:
            L.append("   " + "; ".join(p["notes"]) + ".")
        if s.get("qty"):
            L.append("   Buy-stop above %.2f, stop %.2f, target %.2f (2R), out in 5 sessions if neither. %d shares = %s; "
                     "loss at the stop about %s with charges." % (p["plan"]["entry"], p["plan"]["stop"],
                                                                 p["plan"]["target"], s["qty"], money(s["cost"]),
                                                                 money(s["loss_at_stop"])))
        if p.get("intraday"):
            it = p["intraday"]
            L.append("   Today: opening range %.2f-%.2f, %s, last %.2f %s VWAP %s." % (
                it["or_low"], it["or_high"], it["orb"], it["last"], "above" if it["above_vwap"] else "below", it["vwap"]))
    L.append("")
    L.append("Matched rules on delayed data, ranked. Not a recommendation: check the news (nse-exchange check, "
             "nse-news), then nse-entry / nse-exit / nse-risk before any order.")
    return "\n".join(L)


def svg_chart(p, w=560, h=230):
    bars, e20, e50 = p["_bars"], p["_ema20"], p["_ema50"]
    pl = p["plan"]
    vol_h = 40
    ph = h - vol_h - 18
    lo = min(min(b["l"] for b in bars), pl["stop"])
    hi = max(max(b["h"] for b in bars), pl["target"])
    pad = (hi - lo) * 0.04
    lo, hi = lo - pad, hi + pad
    n = len(bars)
    bw = (w - 60) / n
    X = lambda k: 6 + k * bw + bw / 2  # noqa: E731
    Y = lambda v: 6 + (hi - v) / (hi - lo) * (ph - 6)  # noqa: E731
    vmax = max(b["v"] for b in bars) or 1
    out = ['<svg viewBox="0 0 %d %d" class="chart" role="img" aria-label="%s daily chart">' % (w, h, p["symbol"])]
    for frac in (0.25, 0.5, 0.75):
        v = lo + (hi - lo) * frac
        out.append('<line x1="6" x2="%d" y1="%.1f" y2="%.1f" class="grid"/>' % (w - 54, Y(v), Y(v)))
        out.append('<text x="%d" y="%.1f" class="ax">%.0f</text>' % (w - 50, Y(v) + 3, v))
    for k, b in enumerate(bars):
        up = b["c"] >= b["o"]
        cls = "up" if up else "dn"
        out.append('<line x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f" class="wick %s"/>' % (X(k), X(k), Y(b["h"]), Y(b["l"]), cls))
        top, bot = Y(max(b["o"], b["c"])), Y(min(b["o"], b["c"]))
        out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" class="body %s"/>' % (
            X(k) - bw * 0.35, top, max(bw * 0.7, 1), max(bot - top, 0.8), cls))
        vh = b["v"] / vmax * (vol_h - 4)
        out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" class="vol %s"/>' % (
            X(k) - bw * 0.35, h - 14 - vh, max(bw * 0.7, 1), vh, cls))
    for series, cls in ((e20, "ema20"), (e50, "ema50")):
        pts = ["%.1f,%.1f" % (X(k), Y(v)) for k, v in enumerate(series) if v is not None]
        if pts:
            out.append('<polyline points="%s" class="%s"/>' % (" ".join(pts), cls))
    for v, cls, lab in ((pl["target"], "lt", "target"), (pl["entry"], "le", "entry"), (pl["stop"], "ls", "stop")):
        out.append('<line x1="6" x2="%d" y1="%.1f" y2="%.1f" class="lvl %s"/>' % (w - 54, Y(v), Y(v), cls))
        dy = 11 if cls == "ls" else -3
        out.append('<text x="%d" y="%.1f" class="lab %s">%s %.2f</text>' % (w - 54 - 4, Y(v) + dy, cls, lab, v))
    out.append('<text x="6" y="%d" class="ax">%s</text><text x="%d" y="%d" class="ax" text-anchor="end">%s</text>' % (
        h - 2, bars[0]["d"], w - 54, h - 2, bars[-1]["d"]))
    out.append("</svg>")
    return "".join(out)


CSS = """
:root{--bg:#f7f8fa;--card:#fff;--ink:#14171f;--mute:#5b6372;--line:#e3e6ec;--up:#0f9d58;--dn:#d93025;
--acc:#2457d6;--warn:#b26a00;--e20:#2457d6;--e50:#b26a00}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0f1218;--card:#171b23;--ink:#e8ebf1;
--mute:#9aa3b2;--line:#2a303c;--up:#34c37a;--dn:#ff6b5e;--acc:#7aa2ff;--warn:#f0b04a;--e20:#7aa2ff;--e50:#f0b04a}}
:root[data-theme="dark"]{--bg:#0f1218;--card:#171b23;--ink:#e8ebf1;--mute:#9aa3b2;--line:#2a303c;--up:#34c37a;
--dn:#ff6b5e;--acc:#7aa2ff;--warn:#f0b04a;--e20:#7aa2ff;--e50:#f0b04a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:24px;margin:0 0 4px}.sub{color:var(--mute);margin:0 0 18px}
.banner{border:1px solid var(--warn);color:var(--warn);border-radius:10px;padding:8px 12px;margin:0 0 16px;font-weight:600}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:18px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px}.kpi b{display:block;font-size:22px}
.kpi span{color:var(--mute);font-size:12px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;margin-bottom:22px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}th{color:var(--mute);font-weight:600;font-size:12px}
td:nth-child(2),th:nth-child(2),td:nth-child(4),th:nth-child(4){text-align:left}
.tbl{overflow-x:auto}
.score{display:inline-block;min-width:38px;text-align:center;border-radius:999px;padding:2px 8px;font-weight:700;color:#fff;background:var(--acc)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(520px,1fr));gap:14px}
@media (max-width:600px){.cards{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px}
.card h3{margin:0;font-size:17px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}.card .meta{color:var(--mute);font-size:12px;margin:2px 0 8px}
.chart{width:100%;height:auto;display:block}.grid{stroke:var(--line)}.ax{fill:var(--mute);font-size:10px}
.wick{stroke-width:1}.wick.up{stroke:var(--up)}.wick.dn{stroke:var(--dn)}.body.up{fill:var(--up)}.body.dn{fill:var(--dn)}
.vol{opacity:.35}.vol.up{fill:var(--up)}.vol.dn{fill:var(--dn)}
.ema20{fill:none;stroke:var(--e20);stroke-width:1.4}.ema50{fill:none;stroke:var(--e50);stroke-width:1.4;stroke-dasharray:4 3}
.lvl{stroke-width:1.2;stroke-dasharray:5 4}.lvl.lt{stroke:var(--up)}.lvl.le{stroke:var(--acc)}.lvl.ls{stroke:var(--dn)}
.lab{font-size:10px;text-anchor:end;font-weight:600;paint-order:stroke;stroke:var(--card);stroke-width:3px;stroke-linejoin:round}.lab.lt{fill:var(--up)}.lab.le{fill:var(--acc)}.lab.ls{fill:var(--dn)}
.facts{display:grid;grid-template-columns:1fr 1fr 1fr 1.5fr;gap:6px;margin:8px 0}.facts div{background:var(--bg);border-radius:8px;padding:6px 8px}
.facts span{display:block;color:var(--mute);font-size:11px}.facts b{font-size:14px}
ul{margin:6px 0 0 18px;padding:0}li{margin:2px 0}.legend{color:var(--mute);font-size:12px;margin:6px 0 0}
.foot{color:var(--mute);font-size:12px;margin-top:22px;border-top:1px solid var(--line);padding-top:12px}
.tag{font-size:11px;font-weight:600;border:1px solid var(--line);border-radius:999px;padding:1px 8px;color:var(--mute)}
"""


def html_report(res, sample=False):
    e = html.escape
    top = res["top"]
    rows = []
    for k, p in enumerate(top, 1):
        bt, pl, s = p["backtest"], p["plan"], p["size"]
        rows.append("<tr><td>%d</td><td><b>%s</b></td><td><span class='score'>%d</span></td><td>%s</td><td>%.2f</td>"
                    "<td>%.2f</td><td>%.2f</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                        k, e(p["symbol"]), p["score"], e(p["setup_label"]), pl["entry"], pl["stop"], pl["target"],
                        ("%.0f%% of %d" % (bt["win_pct"], bt["filled"])) if bt["filled"] else "–",
                        ("%+.2f" % bt["avg_r"]) if bt["avg_r"] is not None else "–",
                        s.get("qty") or "–", money(s["loss_at_stop"]) if s.get("qty") else "–"))
    cards = []
    for k, p in enumerate(top, 1):
        bt, pl, s = p["backtest"], p["plan"], p["size"]
        notes = "".join("<li>%s</li>" % e(n) for n in [p["why"]] + p["notes"])
        also = ("<div class='meta'>Also matched: %s</div>" % e(", ".join(p["also"]))) if p["also"] else ""
        intr = ""
        if p.get("intraday"):
            it = p["intraday"]
            intr = "<div class='meta'>Today: opening range %.2f–%.2f, %s; last %.2f, %s VWAP %s</div>" % (
                it["or_low"], it["or_high"], e(it["orb"]), it["last"], "above" if it["above_vwap"] else "below", it["vwap"])
        cards.append(
            "<div class='card' id='pick-%d'><h3>#%d %s <span class='score'>%d</span><span class='tag'>%s</span></h3>"
            "<div class='meta'>Close %s (%+.2f%%) on %s · RSI %s · vs Nifty 20d %s · ₹%s cr/day</div>%s"
            "<div class='facts'><div><span>Entry above</span><b>%.2f</b></div><div><span>Stop</span><b>%.2f</b></div>"
            "<div><span>Target 2R</span><b>%.2f</b></div><div><span>Past: hit · avg R · n</span><b>%s</b></div></div>"
            "<ul>%s</ul><div class='meta'>%s</div>%s%s</div>" % (
                k, k, e(p["symbol"]), p["score"], e(p["setup_label"]), money(p["close"]), p["change_pct"], p["date"],
                p["rsi"], ("%+.1f pts" % p["rs20_vs_nifty"]) if p["rs20_vs_nifty"] is not None else "–",
                p["value20_cr"], svg_chart(p), pl["entry"], pl["stop"], pl["target"],
                ("%.0f%% hit · %+.2fR · %d" % (bt["win_pct"], bt["avg_r"], bt["filled"]))
                if bt["filled"] else "no past signals", notes,
                ("Size at your max loss: %d shares, %s; loss at the stop about %s incl. delivery charges; at the target about +%s."
                 % (s["qty"], money(s["cost"]), money(s["loss_at_stop"]), money(s["gain_at_target"]))) if s.get("qty")
                else e(s.get("note", "")), also, intr))
    best = top[0] if top else None
    banner = ("<div class='banner'>Sample report on SYNTHETIC data, made by <code>picks.py demo-data</code>. "
              "Symbols and prices are invented. Run <code>picks.py scan --html</code> for live NSE charts.</div>") if sample else ""
    doc = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NSE Opportunity Scan</title><style>%s</style></head><body><div class="wrap">
<h1>NSE daily opportunity scan</h1><p class="sub">Daily bars to %s · run %s IST · 6 setups · each back-tested on the stock's own last year</p>%s
<div class="kpis"><div class="kpi"><b>%d</b><span>charts read of %d</span></div><div class="kpi"><b>%d</b><span>matched a setup</span></div>
<div class="kpi"><b>%s</b><span>top score</span></div><div class="kpi"><b>%s</b><span>best past avg R in the top %d</span></div></div>
<div class="tbl"><table><thead><tr><th>#</th><th>Symbol</th><th>Score</th><th>Setup</th><th>Entry above</th><th>Stop</th><th>Target</th>
<th>Hit target / past trades</th><th>Avg R</th><th>Shares</th><th>Loss at stop</th></tr></thead><tbody>%s</tbody></table></div>
<div class="cards">%s</div>
<p class="legend">Charts: last 90 sessions · candles and volume · blue EMA20 · amber dashed EMA50 · entry, stop and target lines.</p>
<div class="foot">Ranked list of rule matches on delayed data, not a recommendation to buy or sell. A back-test is history, not a forecast; it
ignores news, gaps beyond the stop and slippage. Check the stock's announcements and board meetings (nse-exchange), then plan the trade with
nse-entry, nse-exit and nse-risk. Not investment advice; this tool is not a SEBI-registered investment adviser or research analyst.
Most individual intraday traders lose money (SEBI). Generated by the NSE Day Trader skill.</div></div></body></html>""" % (
        CSS, res["as_of_bar"], res["run_at_ist"], banner, res["scanned"], res["universe"], res["matched"],
        best["score"] if best else "–",
        ("%+.2f" % max((p["backtest"]["avg_r"] for p in top if p["backtest"]["avg_r"] is not None), default=0)) if top else "–",
        len(top), "".join(rows), "".join(cards))
    return doc


def screenshot(html_path, png_path, width=1240):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "PNG skipped: Playwright is not installed (pip install playwright; playwright install chromium)"
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": width, "height": 900}, device_scale_factor=2)
        pg.goto("file://" + os.path.abspath(html_path))
        pg.screenshot(path=png_path, full_page=True)
        first = pg.query_selector("#pick-1")
        if first:
            first.screenshot(path=png_path.replace(".png", "-top-pick.png"))
        b.close()
    return None


def public(res):
    def strip(p):
        return {k: v for k, v in p.items() if not k.startswith("_")}
    out = dict(res)
    out["top"] = [strip(p) for p in res["top"]]
    out["all"] = [strip(p) for p in res["all"]]
    return out


# ---------------------------------------------------------------- synthetic data

SAMPLE_NAMES = ["DEMO-AUTO", "DEMO-BANK", "DEMO-CEMENT", "DEMO-CHEM", "DEMO-DEFENCE", "DEMO-ENERGY", "DEMO-FMCG",
                "DEMO-INFRA", "DEMO-IT", "DEMO-METAL", "DEMO-NBFC", "DEMO-PHARMA", "DEMO-POWER", "DEMO-RAIL",
                "DEMO-REALTY", "DEMO-RETAIL", "DEMO-TELECOM", "DEMO-TEXTILE", "DEMO-HOTEL", "DEMO-LOGISTICS"]


def demo_data(out, seed=7, end="2026-10-23", n_days=260):
    """Seeded random-walk daily charts with a few planted setups, in Yahoo v8 shape. SYNTHETIC."""
    rnd = random.Random(seed)
    end_d = datetime.date.fromisoformat(end)
    days, d = [], end_d
    while len(days) < n_days:
        if d.weekday() < 5:
            days.append(d)
        d -= datetime.timedelta(days=1)
    days.reverse()

    def chart(sym, closes, vols, opens, highs, lows):
        ts = [int((datetime.datetime.combine(x, datetime.time(9, 15)) - IST - EPOCH).total_seconds()) for x in days]
        return {"chart": {"result": [{"meta": {"symbol": sym + ".NS", "currency": "INR", "exchangeName": "NSI"},
                                       "timestamp": ts, "indicators": {"quote": [{"open": opens, "high": highs, "low": lows,
                                                                                  "close": closes, "volume": vols}]}}],
                          "error": None}, "_note": "SYNTHETIC"}
    bundle = {"_note": "SYNTHETIC sample data for tests and screenshots. Invented symbols and prices."}
    idx = 22000.0
    ic, io_, ih, il, iv = [], [], [], [], []
    for _ in days:
        o = idx
        idx *= math.exp(rnd.gauss(0.0004, 0.008))
        ic.append(round(idx, 2)); io_.append(round(o, 2)); ih.append(round(max(o, idx) * 1.003, 2))
        il.append(round(min(o, idx) * 0.997, 2)); iv.append(0)
    bundle["^NSEI"] = chart("^NSEI", ic, iv, io_, ih, il)
    bundle["^NSEI"]["chart"]["result"][0]["meta"]["symbol"] = "^NSEI"
    for k, sym in enumerate(SAMPLE_NAMES):
        p = rnd.uniform(180, 4200)
        drift = rnd.choice([0.0012, 0.0008, 0.0004, 0.0, -0.0004, 0.0015])
        vol = rnd.uniform(0.011, 0.022)
        base_v = rnd.uniform(4e5, 6e6)
        plant = k % 4
        C, O, H, L, V = [], [], [], [], []
        for j, _ in enumerate(days):
            o = p * math.exp(rnd.gauss(0, vol / 3))
            mu = drift
            sig = vol
            if plant == 0 and j > n_days - 35 and j < n_days - 1:
                sig = vol * 0.35; mu = 0.0002          # squeeze then break
            if plant == 0 and j == n_days - 1:
                mu = 0.035
            if plant == 1 and j == n_days - 1:
                mu = 0.028                             # breakout day
            if plant == 2 and n_days - 6 < j < n_days - 1:
                mu = -0.012                            # pullback
            if plant == 2 and j == n_days - 1:
                mu = 0.014
            c = o * math.exp(rnd.gauss(mu, sig))
            h = max(o, c) * (1 + abs(rnd.gauss(0, vol / 2)))
            l = min(o, c) * (1 - abs(rnd.gauss(0, vol / 2)))
            v = base_v * math.exp(rnd.gauss(0, 0.35)) * (2.4 if (j == n_days - 1 and plant in (0, 1)) else 1)
            C.append(round(c, 2)); O.append(round(o, 2)); H.append(round(h, 2)); L.append(round(l, 2)); V.append(int(v))
            p = c
        bundle[sym] = chart(sym, C, V, O, H, L)
    op = gzip.open if out.endswith(".gz") else open
    with op(out, "wt", encoding="utf-8") as fh:
        json.dump(bundle, fh)
    return len(SAMPLE_NAMES)


# ---------------------------------------------------------------- main

def write(path, text):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def cmd_scan(a):
    now = parse_now(a.now)
    cfg = read_config(a.config)
    intraday = None
    if a.offline:
        charts = load_offline(a.offline)
        universe = read_universe(a) if (a.symbols or a.index or a.watchlist) else [s for s in charts if not s.startswith("^")]
        index_chart = charts.get("^NSEI")
    else:
        universe = read_universe(a)
        charts, index_chart = {}, None
        try:
            index_chart = fetch_chart("^NSEI", pause=a.pause)
        except PickError:
            pass
        for k, s in enumerate(universe):
            if not a.quiet:
                print("\rReading daily charts %d/%d %-12s" % (k + 1, len(universe), s), end="", file=sys.stderr)
            try:
                charts[s] = fetch_chart(s, pause=a.pause)
            except PickError:
                pass
        if not a.quiet:
            print(file=sys.stderr)
    res = scan(charts, universe, now, cfg, a.top, index_chart)
    if a.intraday and res["top"] and not a.offline:
        intraday = {}
        for p in res["top"]:
            try:
                intraday[p["symbol"]] = fetch_chart(p["symbol"], "1d", "5m", a.pause)
            except PickError:
                pass
        today = ist(now).date()
        for p in res["top"]:
            if p["symbol"] in intraday:
                p["intraday"] = intraday_status(intraday[p["symbol"]], today)
    if a.json:
        print(json.dumps(public(res), indent=2, ensure_ascii=False))
    else:
        print(text_report(res))
    if not a.no_write:
        write(a.out, json.dumps(public(res), indent=2, ensure_ascii=False))
        if not a.json:
            print("Wrote %s." % a.out)
    if a.html or a.png:
        hp = a.html_out or os.path.join("trader", "reports", "picks-%s.html" % (res["as_of_bar"] or "latest"))
        write(hp, html_report(res, sample=a.sample))
        if not a.json:
            print("Wrote %s." % hp)
        if a.png:
            msg = screenshot(hp, hp[:-5] + ".png")
            if not a.json:
                print(msg or "Wrote %s and %s." % (hp[:-5] + ".png", hp[:-5] + "-top-pick.png"))
    return 0 if res["matched"] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="NSE daily opportunity scan (nse-opportunities).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan")
    p.add_argument("--symbols")
    p.add_argument("--index", help="nifty50, nifty100, nifty200, niftynext50 or fno (NSE's own list, via nse-exchange)")
    p.add_argument("--watchlist", help="a file, one SYMBOL (or SYMBOL|Name) a line")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--intraday", action="store_true", help="also today's opening range and VWAP for the top picks")
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--out", default="trader/picks.json")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--html", action="store_true")
    p.add_argument("--html-out")
    p.add_argument("--png", action="store_true", help="also a full-page PNG and the top pick's card (Playwright)")
    p.add_argument("--sample", action="store_true", help="mark the report as synthetic sample data")
    p.add_argument("--offline")
    p.add_argument("--nse-offline", help="offline folder for nse-exchange's index list")
    p.add_argument("--now")
    p.add_argument("--pause", type=float, default=0.4)
    p.add_argument("--json", action="store_true")
    p.add_argument("--quiet", action="store_true")
    d = sub.add_parser("demo-data")
    d.add_argument("out")
    d.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "demo-data":
            n = demo_data(a.out, a.seed)
            print("Wrote %s: %d SYNTHETIC charts plus a synthetic ^NSEI." % (a.out, n))
            return 0
        return cmd_scan(a)
    except PickError as e:
        print("picks.py: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
