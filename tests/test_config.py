import os
import unittest

from spare.core import config
from spare.core.config import ConfigError, from_dict

from tests.helpers import UNIVERSE, TempDirCase


class ConfigTests(TempDirCase):
    def test_defaults_are_safe(self):
        cfg = from_dict({})
        self.assertTrue(cfg.dry_run)
        self.assertEqual(cfg.alpaca_env, "paper")
        self.assertEqual(cfg.buy_weekday, 0)  # Monday
        self.assertEqual(cfg.monthly_cap_cents, 20_000)
        self.assertEqual(cfg.multiplier, 1)
        self.assertFalse(cfg.limits.allow_stocks)

    def test_example_config_loads(self):
        cfg = config.load(os.path.join(config.REPO_ROOT, "config.example.toml"))
        self.assertTrue(cfg.dry_run)

    def test_dollars_to_cents(self):
        self.assertEqual(from_dict({"monthly_cap": 123.45}).monthly_cap_cents, 12_345)
        with self.assertRaises(ConfigError):
            from_dict({"monthly_cap": 12.345})

    def test_buy_day(self):
        self.assertEqual(from_dict({"buy_day": "Friday"}).buy_weekday, 4)
        for bad in ("saturday", "sunday", "someday", 1):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                from_dict({"buy_day": bad})

    def test_rejects_invalid(self):
        for raw in ({"multiplier": 4}, {"buy_trigger": 50}, {"monthly_cap": 0.5}, {"alpaca_env": "prod"}, {"dry_run": "no"},
                    {"surprise": 1}, {"limits": {"max_etf_weight": 0.5}},
                    {"limits": {"min_holdings": 3}}, {"limits": {"bogus": 1}}):
            with self.subTest(raw=raw), self.assertRaises(ConfigError):
                from_dict(raw)

    def test_tighter_limits_allowed(self):
        cfg = from_dict({"limits": {"max_etf_weight": 0.25, "min_holdings": 6}})
        self.assertEqual(cfg.limits.max_etf_weight, 0.25)

    def test_bad_toml(self):
        path = os.path.join(self.dir, "c.toml")
        with open(path, "w") as f:
            f.write("multiplier = = 2")
        with self.assertRaises(ConfigError):
            config.load(path)


class AllowlistTests(unittest.TestCase):
    def test_fifteen_etfs(self):
        self.assertEqual(len(UNIVERSE.etfs), 15)
        self.assertIn("VTI", UNIVERSE.etfs)

    def test_entries_complete(self):
        import tomllib
        with open(config.DEFAULT_ALLOWLIST, "rb") as f:
            data = tomllib.load(f)
        for symbol, entry in data.items():
            self.assertEqual(set(entry), {"asset_class", "expense_ratio", "description"}, symbol)
            self.assertLess(entry["expense_ratio"], 0.01, symbol)
