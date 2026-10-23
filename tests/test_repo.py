import gzip
import importlib.util
import json
import os
import re
import unittest

from _load import ROOT, FIX

# the India edition: the eleven folders, NSE stocks only
SKILLS = ["trader-scanner", "trader-news", "trader-entry", "trader-exit", "trader-timemachine",
          "trader-risk", "trader-budget", "trader-broker", "trader-alerts", "trader-analyst",
          "trader-insider"]
ABOUT = ("11 Claude skills for day trading Indian stocks on the NSE: scan the Nifty 500, track news and "
         "exchange filings, time every entry and exit, and replay every outcome before you risk a rupee.")
# skills on the way to an order carry the safety block
ORDER_PATH = ["trader-scanner", "trader-entry", "trader-exit", "trader-timemachine", "trader-budget",
              "trader-insider", "trader-risk", "trader-broker", "trader-alerts"]
V1_ONLY = ["trader-copycat", "trader-screener", "trader-tax", "trader-rebalance"]
# what the US edition read and traded through: none of it belongs in an NSE-only pack
US_ONLY = ["NYSE", "New York", "nyse.com", "Nasdaq Trader", "nasdaqtrader", "EDGAR", "data.sec.gov",
           "sec_user_agent", "SEC_USER_AGENT", "House Clerk", "disclosures-clerk", "STOCK Act",
           "Alpaca", "alpaca", "place_stock_order", "FINRA", "pdt_guard", "_usd", "to_eastern",
           "eastern_offset", "stooq", "trader-pelosi"]
REPO = "saitech-systems/day-trader-skill-ind"


def frontmatter(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return (m.group(1) if m else ""), text


def every_text_file():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for f in files:
            if f.endswith((".md", ".py", ".json", ".csv", ".txt", ".xml", ".html")) or f == "LICENSE":
                yield os.path.join(base, f)


def skill_files():
    for s in SKILLS:
        folder = os.path.join(ROOT, s)
        for f in sorted(os.listdir(folder)):
            path = os.path.join(folder, f)
            if os.path.isfile(path):
                yield s, f, path


def block(src, name):
    """The text of a top-level dict literal NAME = {...} in a tool's source, or None."""
    m = re.search(r"(?m)^%s = \{\n(.*?)\n\}" % re.escape(name), src, re.S)
    return m.group(1) if m else None


class Repo(unittest.TestCase):
    def test_eleven_skill_folders_at_the_root(self):
        found = sorted(d for d in os.listdir(ROOT)
                       if os.path.isfile(os.path.join(ROOT, d, "SKILL.md")))
        self.assertEqual(found, sorted(SKILLS))

    def test_root_holds_only_the_pack(self):
        root = sorted(d for d in os.listdir(ROOT) if not d.startswith(".git"))
        self.assertEqual(root, sorted([".claude-plugin", "LICENSE", "README.md", "tests"] + SKILLS))

    def test_frontmatter_name_and_description(self):
        for s in SKILLS:
            fm, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            self.assertIn("name: %s\n" % s, fm + "\n", s)
            self.assertRegex(fm, r"description: ", s)
            self.assertGreater(len(fm), 200, "%s description is too thin" % s)
            self.assertGreater(len(text.splitlines()), 60, s)

    def test_order_path_skills_carry_the_safety_rule(self):
        for s in ORDER_PATH:
            _, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            low = " ".join(text.lower().split())
            self.assertIn("not financial advice", low, s)
            self.assertIn("sebi-registered", low, s)
            self.assertIn("yes", low, s)

    def test_plugin_manifest_points_at_the_root_skills(self):
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json")) as fh:
            plugin = json.load(fh)
        self.assertEqual(plugin["name"], "day-trader-skill-ind")
        self.assertEqual(plugin["skills"], "./")
        self.assertEqual(plugin["description"], ABOUT)
        self.assertEqual(plugin["repository"], "https://github.com/" + REPO)
        with open(os.path.join(ROOT, ".claude-plugin", "marketplace.json")) as fh:
            market = json.load(fh)
        self.assertEqual(market["name"], "day-trader-skill-ind")
        self.assertEqual(market["plugins"][0]["name"], "day-trader-skill-ind")
        self.assertEqual(market["plugins"][0]["source"], "./")

    def test_readme_tells_how_to_install_and_use_it(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = " ".join(fh.read().split())
        self.assertTrue(readme.startswith("# The Day Trader skill: India edition"))
        for line in (ABOUT,
                     "https://github.com/%s install skill" % REPO,
                     "/plugin marketplace add %s" % REPO,
                     "/plugin install day-trader-skill-ind@day-trader-skill-ind",
                     "## Fine print",
                     "synthetic"):
            self.assertIn(line, readme)
        for s in SKILLS:
            self.assertIn("/" + s, readme, s)

    def test_the_original_author_keeps_the_credit(self):
        with open(os.path.join(ROOT, "LICENSE"), encoding="utf-8") as fh:
            lic = fh.read()
        self.assertIn("Copyright (c) 2026 Jake Schincariol", lic)
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            self.assertIn("Jake Schincariol", fh.read())

    def test_no_em_dashes_and_no_personal_email(self):
        for path in every_text_file():
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            rel = os.path.relpath(path, ROOT)
            if not rel.startswith(os.path.join("tests", "fixtures") + os.sep) or rel.endswith(".py"):
                self.assertNotIn("\u2014", text, rel)  # fixtures may carry real data later, kept as published
            self.assertNotIn("jakeschincariol" + "@" + "gmail.com", text, rel)  # built, so this file passes too

    def test_no_v1_only_skill_is_left_behind(self):
        for path in every_text_file():
            rel = os.path.relpath(path, ROOT)
            if rel.startswith("tests" + os.sep):
                continue
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            for old in V1_ONLY:
                self.assertNotIn(old, text, rel)

    def test_no_us_market_left_in_the_skills(self):
        for s, f, path in skill_files():
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            for word in US_ONLY:
                self.assertNotIn(word, text, "%s/%s still says %r" % (s, f, word))

    def test_every_tool_runs_on_python_3_8(self):
        # no 3.9+ only modules or syntax in the tools (the README promises 3.8)
        for s, f, path in skill_files():
            if not f.endswith(".py"):
                continue
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            for bad in ("import zoneinfo", "from zoneinfo", ".removeprefix(", ".removesuffix(",
                        "match ", "list[", "dict[", "tuple["):
                if bad == "match ":
                    self.assertNotRegex(src, r"(?m)^\s+match \w+.*:$", "%s/%s" % (s, f))
                elif bad.endswith("["):
                    self.assertNotRegex(src, r"(?<![\w.])%s" % re.escape(bad), "%s/%s" % (s, f))
                else:
                    self.assertNotIn(bad, src, "%s/%s" % (s, f))

    def test_every_tool_knows_the_same_nse_calendar(self):
        # each tool is standalone, so each carries its own copy: they must agree
        seen = {}
        for s, f, path in skill_files():
            if not f.endswith(".py"):
                continue
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            for name in ("NSE_HOLIDAYS", "NSE_SPECIAL_SESSIONS", "TICK_BANDS", "CHARGES"):
                body = block(src, name) if name != "TICK_BANDS" else (
                    re.search(r"(?m)^TICK_BANDS = \((.*?)\n\)", src, re.S) or [None, None])[1]
                if body is not None:
                    seen.setdefault(name, {})["%s/%s" % (s, f)] = " ".join(body.split())
        self.assertIn("NSE_HOLIDAYS", seen)
        self.assertGreaterEqual(len(seen["NSE_HOLIDAYS"]), 6)
        for name, copies in seen.items():
            self.assertEqual(len(set(copies.values())), 1, "%s differs between %s" % (name, sorted(copies)))

    def test_the_holidays_are_weekdays_and_the_specials_are_not(self):
        for s, f, path in skill_files():
            if f.endswith(".py") and "NSE_HOLIDAYS = {" in open(path, encoding="utf-8").read():
                spec = importlib.util.spec_from_file_location("cal_" + f[:-3], path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                break
        import datetime
        for d in mod.NSE_HOLIDAYS:
            self.assertLess(datetime.date.fromisoformat(d).weekday(), 5, d)
        for d in mod.NSE_SPECIAL_SESSIONS:
            self.assertGreaterEqual(datetime.date.fromisoformat(d).weekday(), 5, d)
        self.assertEqual(len(mod.NSE_HOLIDAYS), 16)

    def test_day_trading_limits_ship_in_the_example_config(self):
        with open(os.path.join(ROOT, "trader-budget", "config.example.json")) as fh:
            cfg = json.load(fh)
        for k in ("max_loss_per_trade_inr", "max_daily_loss_inr", "max_trades_per_day",
                  "min_reward_risk", "require_stop", "no_entries_after", "flat_by", "charges"):
            self.assertIn(k, cfg, k)
        self.assertFalse([k for k in cfg if k.endswith("_usd")])
        self.assertIs(cfg["require_stop"], True)
        self.assertEqual((cfg["exchange"], cfg["product"], cfg["broker"]), ("NSE", "MIS", "zerodha"))
        self.assertEqual(cfg["flat_by"], "15:05")
        self.assertIs(cfg.get("allow_short", False), False)
        self.assertIs(cfg.get("allow_margin", False), False)

    def test_paper_trading_is_the_default(self):
        with open(os.path.join(ROOT, "trader-budget", "config.example.json")) as fh:
            cfg = json.load(fh)
        self.assertIs(cfg["paper"], True)
        self.assertIs(cfg.get("allow_options", False), False)

    def test_no_broker_key_ships_in_the_repo(self):
        for path in every_text_file():
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            for m in re.finditer(r'"KITE_API_(?:KEY|SECRET)"\s*:\s*"([^"]*)"', text):
                self.assertRegex(m.group(1).lower(), r"your|here|never", os.path.relpath(path, ROOT))


class Synthetic(unittest.TestCase):
    """The shared fixtures are synthetic and must be exactly what the generator makes."""

    def test_shared_fixtures_match_the_generator(self):
        spec = importlib.util.spec_from_file_location("make_synthetic", os.path.join(FIX, "make_synthetic.py"))
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)
        days, hist, monday = gen.demo_history()
        bars = [b for sess in hist for b in sess] + monday
        want = gen.chart_doc("DEMO", bars, gen.FETCHED, "60d", gen.PREV_CLOSE, chart_prev_close=hist[0][0][1])
        with gzip.open(os.path.join(FIX, "shared", "DEMO-60d5m-synthetic.json.gz"), "rt", encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), json.loads(json.dumps(want)))
        with open(os.path.join(FIX, "shared", "DEMO-2026-10-19-5m-synthetic.json"), encoding="utf-8") as fh:
            one = json.load(fh)
        self.assertEqual(one, json.loads(json.dumps(gen.chart_doc("DEMO", monday, gen.FETCHED, "1d", gen.PREV_CLOSE))))
        self.assertEqual(len(days), 60)
        self.assertEqual((days[0].isoformat(), days[-1].isoformat()), ("2026-07-24", "2026-10-19"))
        self.assertTrue(all(len(sess) == 75 for sess in hist))
        self.assertEqual(hist[-1][-1][4], 1352.40)
        self.assertIn("(synthetic)", one["chart"]["result"][0]["meta"]["longName"])


if __name__ == "__main__":
    unittest.main()
