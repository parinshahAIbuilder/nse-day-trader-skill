"""Offline tests for the NSE day-trader pack. Run: python3 -m unittest discover -s tests -v

The price fixtures in tests/fixtures/nse are SYNTHETIC: real US 5-minute bars re-timed to
the NSE session (9:15 to 15:30 IST) with prices x4. They test the arithmetic and the clock,
not any NSE stock's history."""

import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _load import FIX, ROOT, load  # noqa: E402

NSE = os.path.join(FIX, "nse")
DAY = os.path.join(NSE, "DEMO-2026-10-19-5m.json")
SIXTY = os.path.join(NSE, "DEMO-60d5m.json.gz")
NOW = "2026-10-19 10:00"
CLOCK_MODULES = [("nse-entry", "entry"), ("nse-exit", "exit"), ("nse-risk", "check"),
                 ("nse-alerts", "alerts"), ("nse-scanner", "scan"), ("nse-budget", "size"),
                 ("nse-broker", "ticket"), ("nse-timemachine", "timemachine"), ("nse-news", "news")]


def run(script, *args, cwd):
    p = subprocess.run([sys.executable, "-I", os.path.join(ROOT, script)] + list(args),
                       cwd=cwd, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


class Clock(unittest.TestCase):
    def test_ist_is_utc_plus_530_everywhere(self):
        utc = datetime.datetime(2026, 10, 19, 3, 45)
        for folder, name in CLOCK_MODULES:
            m = load(folder, name)
            self.assertEqual(m.to_eastern(utc), datetime.datetime(2026, 10, 19, 9, 15), folder)
            if hasattr(m, "eastern_to_utc"):
                self.assertEqual(m.eastern_to_utc(datetime.datetime(2026, 10, 19, 9, 15)), utc, folder)

    def test_no_daylight_saving(self):
        m = load("nse-entry", "entry")
        for month in (1, 4, 7, 11):
            self.assertEqual(m.eastern_offset(datetime.datetime(2026, month, 10, 12)), 5.5)

    def test_session_and_holidays(self):
        for folder, name in [("nse-entry", "entry"), ("nse-exit", "exit"), ("nse-risk", "check"),
                             ("nse-alerts", "alerts"), ("nse-scanner", "scan")]:
            m = load(folder, name)
            self.assertEqual((m.OPEN, m.CLOSE), (datetime.time(9, 15), datetime.time(15, 30)), folder)
            self.assertIn("2026-10-20", m.NSE_HOLIDAYS)          # Dussehra
            self.assertIn("2026-11-10", m.NSE_HOLIDAYS)          # Diwali-Balipratipada
            self.assertEqual(len(m.NSE_HOLIDAYS), 16, folder)
            self.assertEqual(m.NSE_EARLY_CLOSE, {})
        tm = load("nse-timemachine", "timemachine")
        self.assertEqual((tm.OPEN_MIN, tm.CLOSE_MIN, tm.SESSION_BARS), (555, 930, 75))

    def test_market_clock(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out = run("nse-alerts/alerts.py", "clock", "--now", "2026-10-20 10:00", cwd=d)
            self.assertIn("Dussehra", out)
            self.assertIn("2026-10-21 09:15", out)
            rc, out = run("nse-alerts/alerts.py", "clock", "--now", "2026-10-26 09:20", cwd=d)
            self.assertIn("NSE OPEN", out)


class Symbols(unittest.TestCase):
    def test_yahoo_suffix(self):
        for folder, name in [("nse-entry", "entry"), ("nse-exit", "exit"), ("nse-alerts", "alerts"),
                             ("nse-news", "news"), ("nse-timemachine", "timemachine")]:
            f = load(folder, name).yahoo_symbol
            self.assertEqual(f("reliance"), "RELIANCE.NS", folder)
            self.assertEqual(f("M&M"), "M&M.NS")
            self.assertEqual(f("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")
            self.assertEqual(f("TCS.BO"), "TCS.BO")
            self.assertEqual(f("^NSEI"), "^NSEI")
        scan = load("nse-scanner", "scan")
        self.assertEqual(scan.to_yahoo("INFY"), "INFY.NS")
        self.assertEqual(scan.from_yahoo("INFY.NS"), "INFY")
        self.assertEqual(scan.from_yahoo("INFY.BO"), "INFY.BO")

    def test_risk_accepts_nse_symbols(self):
        chk = load("nse-risk", "check")
        for s in ("RELIANCE", "M&M", "BAJAJ-AUTO", "3MINDIA", "TCS.NS"):
            self.assertTrue(chk.TICKER.match(s), s)

    def test_universe_keeps_eq_series_only(self):
        scan = load("nse-scanner", "scan")
        csv = ("SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING\n"
               "RELIANCE,Reliance Industries Limited,EQ,29-NOV-1995\n"
               "XYZ,Trade For Trade Ltd,BE,01-JAN-2020\n"
               "M&M,Mahindra & Mahindra Limited,EQ,17-NOV-1995\n")
        kept, counts, _ = scan.build_universe({"EQUITY_L.csv": csv})
        self.assertEqual([k for k, _ in kept], ["M&M", "RELIANCE"])
        self.assertEqual(counts["series BE (not EQ)"], 1)


class Money(unittest.TestCase):
    def test_tick_sizes(self):
        ex = load("nse-exit", "exit")
        D = ex.Decimal
        for price, tick in [("120", "0.01"), ("249.99", "0.01"), ("250", "0.05"), ("1000", "0.05"),
                            ("1000.05", "0.10"), ("5000", "0.10"), ("7500", "0.50"), ("15000", "1.00"),
                            ("25000", "5.00")]:
            self.assertEqual(ex.tick_size(price), D(tick), price)
        self.assertEqual(ex.floor_tick("1433.37", "1456.70"), D("1433.30"))
        self.assertEqual(ex.round_tick("1480.14", "1456.70"), D("1480.10"))
        self.assertEqual(ex.round_tick("1480.15", "1456.70"), D("1480.20"))
        self.assertEqual(ex.floor_tick("612.33", "620"), D("612.30"))

    def test_intraday_charges(self):
        size = load("nse-budget", "size")
        # 40 shares bought at 1456.70, sold at 1433.30 (worked by hand from the schedule)
        buy, sell = 40 * 1456.70, 40 * 1433.30
        brk = min(20, buy * 0.0003) + min(20, sell * 0.0003)   # 0.03 % is under the flat 20 here
        exch = (buy + sell) * 0.00297 / 100
        sebi = (buy + sell) * 0.0001 / 100
        want = brk + exch + sebi + round(sell * 0.025 / 100) + round(buy * 0.003 / 100) + (brk + exch + sebi) * 0.18
        self.assertAlmostEqual(size.charges_for(40, 1456.70, 1433.30), round(want, 2), places=2)
        # a small order pays 0.03 % brokerage, not the flat 20
        self.assertLess(size.charges_for(1, 500, 500), 1.0)
        self.assertEqual(size.charges_for(0, 500, 500), 0.0)


class Pipeline(unittest.TestCase):
    """entry -> exit -> time machine -> budget -> risk -> broker, on the synthetic DEMO day."""

    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        os.makedirs(os.path.join(cls.d, "trader"))
        shutil.copy(os.path.join(ROOT, "nse-budget", "config.example.json"),
                    os.path.join(cls.d, "trader", "config.json"))
        cls.out = {}
        steps = [
            ("entry", "nse-entry/entry.py", "check", "DEMO", "--now", NOW, "--offline", DAY, "--write"),
            ("exit", "nse-exit/exit.py", "plan", "--stop", "or-mid", "--target", "1R", "--now", NOW, "--write"),
            ("tm", "nse-timemachine/timemachine.py", "run", "--offline", SIXTY, "--now", NOW, "--write"),
            ("budget", "nse-budget/size.py", "--now", NOW, "--write"),
            ("risk", "nse-risk/check.py", "--now", NOW),
            ("ticket", "nse-broker/ticket.py", "show", "--json"),
        ]
        for name, script, *args in steps:
            cls.out[name] = run(script, *args, cwd=cls.d)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d)

    def j(self, name):
        with open(os.path.join(self.d, "trader", name)) as fh:
            return json.load(fh)

    def test_entry_triggers_after_the_ist_range(self):
        rc, out = self.out["entry"]
        self.assertEqual(rc, 0, out)
        self.assertIn("Opening range 9:15-9:30", out)
        e = self.j("entry.json")
        self.assertEqual((e["symbol"], e["status"]), ("DEMO", "triggered"))

    def test_plan_on_ticks(self):
        p = self.j("plan.json")
        self.assertEqual((p["entry"], p["stop"], p["target"], p["flat_by"]), (1456.70, 1433.30, 1480.10, "15:10"))

    def test_time_machine_uses_nse_sessions(self):
        rc, out = self.out["tm"]
        self.assertEqual(rc, 0, out)
        self.assertIn("3:30 PM", out)
        self.assertIn("10,000 replays", out)

    def test_budget_sizes_in_rupees_net_of_charges(self):
        o = self.j("orders.json")
        order = o["orders"][0] if isinstance(o, dict) else o[0]
        self.assertEqual(order["qty"], "40")       # 42 by price risk alone; charges take it to 40
        self.assertEqual((order["product"], order["exchange"]), ("MIS", "NSE"))
        self.assertLessEqual(40 * 23.40 + order["est_charges"], 1000)
        self.assertIn("₹", self.out["budget"][1])

    def test_risk_passes(self):
        rc, out = self.out["risk"]
        self.assertEqual(rc, 0, out)
        self.assertIn("PASS", out)

    def test_ticket_is_kite_shaped_and_paper(self):
        rc, out = self.out["ticket"]
        t = json.loads(out)
        t = t[0] if isinstance(t, list) else (t.get("tickets") or [t])[0]
        self.assertTrue(t["paper"])
        self.assertEqual(t["tool"], "place_order")
        first, stop = t["tool_input"]
        self.assertEqual(first["args"]["tradingsymbol"], "DEMO")
        self.assertEqual((first["args"]["product"], first["args"]["order_type"]), ("MIS", "MARKET"))
        self.assertEqual((stop["args"]["order_type"], stop["args"]["trigger_price"]), ("SL-M", 1433.3))

    def test_sebi_pit_blocks_your_own_company(self):
        cfgp = os.path.join(self.d, "trader", "config.json")
        with open(cfgp) as fh:
            cfg = json.load(fh)
        cfg["pit_restricted"] = {"DEMO": "designated person"}
        with open(cfgp, "w") as fh:
            json.dump(cfg, fh)
        try:
            rc, out = run("nse-risk/check.py", "--now", NOW, "--no-write", cwd=self.d)
        finally:
            cfg["pit_restricted"] = {}
            with open(cfgp, "w") as fh:
                json.dump(cfg, fh)
        self.assertEqual(rc, 1, out)
        self.assertIn("BLOCK", out)
        self.assertIn("SEBI PIT", out)


class Repo(unittest.TestCase):
    def test_skills_and_python38(self):
        skills = sorted(d for d in os.listdir(ROOT) if d.startswith("nse-"))
        self.assertEqual(len(skills), 11)
        for s in skills:
            with open(os.path.join(ROOT, s, "SKILL.md"), encoding="utf-8") as fh:
                md = fh.read()
            self.assertTrue(md.startswith("---\nname: %s\n" % s), s)
            for f in os.listdir(os.path.join(ROOT, s)):
                if f.endswith(".py"):
                    with open(os.path.join(ROOT, s, f), encoding="utf-8") as fh:
                        src = fh.read()
                    for bad in ("import zoneinfo", "from zoneinfo", ".removeprefix(", ".removesuffix("):
                        self.assertNotIn(bad, src, "%s/%s" % (s, f))

    def test_credits_upstream(self):
        with open(os.path.join(ROOT, "LICENSE"), encoding="utf-8") as fh:
            lic = fh.read()
        self.assertIn("Jake Schincariol", lic)
        self.assertIn("Parin Shah", lic)


if __name__ == "__main__":
    unittest.main()
