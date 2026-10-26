import unittest

import market_universe_lab as lab


class MarketUniverseLabTests(unittest.TestCase):
    def test_candidate_configs_are_ordered_and_unique(self):
        configs = lab.candidate_configs()
        self.assertTrue(configs)
        self.assertEqual(len(configs), len({
            (c["ema_fast"], c["ema_slow"], c["ema_trend"], c["stop_atr"], c["target_atr"])
            for c in configs
        }))
        self.assertTrue(all(c["ema_fast"] < c["ema_slow"] for c in configs))

    def test_all_markets_and_timeframes_are_explicit_and_read_only(self):
        self.assertIn("BTCUSDT", lab.SYMBOLS)
        self.assertIn("ETHUSDT", lab.SYMBOLS)
        self.assertIn("SOLUSDT", lab.SYMBOLS)
        self.assertIn(("15m", 15), lab.TIMEFRAMES)
        self.assertIn(("1H", 60), lab.TIMEFRAMES)
        self.assertGreaterEqual(lab.HISTORY_DAYS, 60)

    def test_rank_key_prefers_profit_factor_then_return(self):
        stronger = {"development": {
            "profit_factor": 1.2,
            "compounded_return_pct": 2.0,
            "max_drawdown_pct": 5.0,
        }}
        weaker = {"development": {
            "profit_factor": 1.1,
            "compounded_return_pct": 10.0,
            "max_drawdown_pct": 1.0,
        }}
        self.assertGreater(lab.rank_key(stronger), lab.rank_key(weaker))

    def test_none_profit_factor_does_not_outrank_measured_profit_factor(self):
        no_losses_or_no_profit = {"development": {
            "profit_factor": None,
            "compounded_return_pct": 100.0,
            "max_drawdown_pct": 0.0,
        }}
        measured = {"development": {
            "profit_factor": 1.01,
            "compounded_return_pct": 1.0,
            "max_drawdown_pct": 20.0,
        }}
        self.assertGreater(lab.rank_key(measured), lab.rank_key(no_losses_or_no_profit))


if __name__ == "__main__":
    unittest.main()
