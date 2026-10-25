"""Offline tests for nse-exchange (NSE website data) and nse-opportunities (the daily scan).
Fixtures: tests/fixtures/nse/exchange holds NSE-shaped answers (DEMO / THINCO are invented);
tests/fixtures/nse/sample-daily.json.gz is SYNTHETIC daily data from picks.py demo-data."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _load import FIX, ROOT, load  # noqa: E402

EX = os.path.join(FIX, "nse", "exchange")
SAMPLE = os.path.join(FIX, "nse", "sample-daily.json.gz")


def run(script, *args, cwd):
    p = subprocess.run([sys.executable, "-I", os.path.join(ROOT, script)] + list(args),
                       cwd=cwd, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


class Exchange(unittest.TestCase):
    def setUp(self):
        self.api = load("nse-exchange", "nse_api")
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_parsers(self):
        api = self.api
        with open(os.path.join(EX, "holiday-master.json")) as fh:
            hol = api.parse_holidays(json.load(fh))
        self.assertEqual(hol["2026-10-20"], "Dussehra")
        self.assertIn("2027-01-26", hol)
        with open(os.path.join(EX, "quote-THINCO.json")) as fh:
            q = api.parse_quote(json.load(fh), "THINCO")
        self.assertEqual((q["series"], q["surveillance"], q["upper_circuit"]), ("BE", "ASM", 63.1))
        self.assertEqual(q["time"], "2026-10-26T04:35:12Z")
        with open(os.path.join(EX, "announcements-DEMO.json")) as fh:
            an = api.parse_announcements(json.load(fh), "DEMO", api.parse_nse_date("23-Oct-2026 10:06"))
        self.assertEqual([a["subject"] for a in an], ["Board Meeting Intimation", "Credit Rating"])
        self.assertIn("results", an[0]["tags"])
        self.assertIn("rating", an[1]["tags"])
        with open(os.path.join(EX, "bulk.csv")) as fh:
            deals = api.parse_deals(fh.read(), "bulk", "DEMO")
        self.assertEqual((deals[0]["qty"], deals[0]["side"], deals[0]["value_cr"]), (1250000, "BUY", 181.17))

    def test_live_holidays_extend_every_calendar(self):
        rc, out = run("nse-exchange/nse_api.py", "holidays", "--offline", EX, "--write", cwd=self.d)
        self.assertEqual(rc, 0, out)
        rc, out = run("nse-alerts/alerts.py", "clock", "--now", "2027-01-26 10:00", cwd=self.d)
        self.assertIn("Republic Day", out)

    def test_check_and_risk_blocks_non_eq(self):
        os.makedirs(os.path.join(self.d, "trader"))
        rc, out = run("nse-exchange/nse_api.py", "check", "DEMO", "--offline", EX, "--now", "2026-10-26 10:06",
                      "--write", cwd=self.d)
        self.assertEqual(rc, 0, out)
        self.assertIn("board meeting today", out)
        risk = load("nse-risk", "check")
        cwd = os.getcwd()
        os.chdir(self.d)
        try:
            facts = risk._nse_facts()
        finally:
            os.chdir(cwd)
        self.assertTrue(facts["DEMO"]["board_meeting_today"])
        self.assertEqual(facts["DEMO"]["upper_circuit"], 1602.35)
        rc, out = run("nse-exchange/nse_api.py", "quote", "THINCO", "--offline", EX, "--write", cwd=self.d)
        self.assertIn("not EQ", out)
        with open(os.path.join(self.d, "trader", "prices.json")) as fh:
            self.assertEqual(json.load(fh)["detail"]["THINCO"]["series"], "BE")


class Picks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        cls.rc, cls.out = run("nse-opportunities/picks.py", "scan", "--offline", SAMPLE, "--now", "2026-10-23 16:00",
                              "--top", "5", "--html", "--sample", cwd=cls.d)
        with open(os.path.join(cls.d, "trader", "picks.json")) as fh:
            cls.res = json.load(fh)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d)

    def test_scan_reads_everything_and_ranks(self):
        self.assertEqual(self.rc, 0, self.out)
        self.assertEqual((self.res["scanned"], self.res["universe"]), (20, 20))
        scores = [p["score"] for p in self.res["top"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(self.res["top"][0]["symbol"], "DEMO-TEXTILE")

    def test_levels_are_sane_and_on_ticks(self):
        pk = load("nse-opportunities", "picks")
        for p in self.res["all"]:
            pl = p["plan"]
            self.assertLess(pl["stop"], pl["entry"])
            self.assertGreater(pl["target"], pl["entry"])
            t = pk.tick_size(pl["entry"])
            for v in (pl["entry"], pl["stop"], pl["target"]):
                self.assertAlmostEqual(round(v / t) * t, v, places=2)
            self.assertAlmostEqual(pl["target"] - pl["entry"], 2 * pl["risk"], delta=pk.tick_size(pl["entry"]) + 0.01)

    def test_sizing_fits_max_loss_with_charges(self):
        for p in self.res["all"]:
            if p["size"].get("qty"):
                self.assertLessEqual(p["size"]["loss_at_stop"], 1000.0)

    def test_backtest_counts_add_up(self):
        for p in self.res["all"]:
            bt = p["backtest"]
            self.assertEqual(bt["target"] + bt["stop"] + bt["time"], bt["filled"])
            self.assertLessEqual(bt["filled"], bt["signals"])

    def test_no_lookahead(self):
        """A bar after the scan date never changes the scan: same result with the future cut off."""
        pk = load("nse-opportunities", "picks")
        charts = pk.load_offline(SAMPLE)
        res = pk.scan(charts, [s for s in charts if not s.startswith("^")], pk.parse_now("2026-10-23 16:00"),
                      pk.DEFAULT_CFG, 5, charts["^NSEI"])
        self.assertEqual([p["symbol"] for p in res["top"]], [p["symbol"] for p in self.res["top"]])
        early = pk.scan(charts, ["DEMO-TEXTILE"], pk.parse_now("2026-10-23 11:00"), pk.DEFAULT_CFG, 5, charts["^NSEI"])
        self.assertNotEqual(early["as_of_bar"], "2026-10-23")   # mid-session: today's bar is not used

    def test_indicators(self):
        pk = load("nse-opportunities", "picks")
        self.assertEqual(pk.ema([1, 2, 3, 4], 2)[1:], [1.5, 2.5, 3.5])
        r = pk.rsi([float(x) for x in range(1, 40)])
        self.assertEqual(r[-1], 100.0)

    def test_html_report(self):
        p = os.path.join(self.d, "trader", "reports", "picks-2026-10-23.html")
        with open(p, encoding="utf-8") as fh:
            h = fh.read()
        self.assertIn("SYNTHETIC", h)
        self.assertEqual(h.count("<svg"), 5)
        self.assertIn("not a recommendation", h)


if __name__ == "__main__":
    unittest.main()
