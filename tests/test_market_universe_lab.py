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

    def test_candle_validation_rejects_excessive_gaps(self):
        step = 15 * 60 * 1000
        candles = [{"timestamp": 1_800_000_000_000}, {"timestamp": 1_800_000_000_000 + 2 * step}]
        with self.assertRaisesRegex(RuntimeError, "exceed 2%"):
            lab.validate_candles(candles, step, now_ms=1_800_000_000_000 + 3 * step)

    def test_candle_validation_reports_small_gaps(self):
        step = 15 * 60 * 1000
        start = 1_800_000_000_000
        timestamps = [start + i * step for i in range(100)]
        timestamps.remove(start + 50 * step)
        candles = [{"timestamp": t} for t in timestamps]
        quality = lab.validate_candles(candles, step, now_ms=timestamps[-1] + step)
        self.assertEqual(quality["gaps_detected"], 1)
        self.assertEqual(quality["missing_intervals"], 1)
        self.assertGreater(quality["missing_intervals_pct"], 0)

    def test_candle_validation_rejects_stale_or_unclosed_data(self):
        step = 15 * 60 * 1000
        latest = 1_800_000_000_000
        with self.assertRaisesRegex(RuntimeError, "not closed yet"):
            lab.validate_candles([{"timestamp": latest}], step, now_ms=latest + step - 1)
        with self.assertRaisesRegex(RuntimeError, "stale"):
            lab.validate_candles([{"timestamp": latest}], step, now_ms=latest + 5 * step)

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


    @staticmethod
    def _candidate_fold(result, profit_factor=1.2, trades=5):
        return {
            "stress_2x": {
                "trades": trades,
                "compounded_return_pct": result,
                "profit_factor": profit_factor,
            }
        }

    def test_paper_gate_rejects_three_positive_folds_when_mean_is_negative(self):
        folds = [
            self._candidate_fold(0.10),
            self._candidate_fold(0.10),
            self._candidate_fold(0.10),
            self._candidate_fold(-0.50, profit_factor=0.4),
        ]
        self.assertFalse(lab.qualifies_for_forward_paper(folds))

    def test_paper_gate_requires_positive_mean_and_three_qualifying_folds(self):
        folds = [
            self._candidate_fold(0.10),
            self._candidate_fold(0.20),
            self._candidate_fold(0.10),
            self._candidate_fold(-0.05, profit_factor=0.8),
        ]
        self.assertTrue(lab.qualifies_for_forward_paper(folds))

    def test_paper_gate_rejects_insufficient_trades_or_positive_folds(self):
        insufficient = [
            self._candidate_fold(0.10),
            self._candidate_fold(0.20),
            self._candidate_fold(0.10),
            self._candidate_fold(-0.01, trades=4),
        ]
        only_two_positive = [
            self._candidate_fold(0.10),
            self._candidate_fold(0.20),
            self._candidate_fold(-0.01, profit_factor=0.8),
            self._candidate_fold(-0.02, profit_factor=0.7),
        ]
        self.assertFalse(lab.qualifies_for_forward_paper(insufficient))
        self.assertFalse(lab.qualifies_for_forward_paper(only_two_positive))

    def test_portfolio_gate_also_requires_positive_mean_return(self):
        folds = [
            {"stress_2x": {"compounded_return_pct": 0.1}, "qualifies_at_2x_cost": True},
            {"stress_2x": {"compounded_return_pct": 0.1}, "qualifies_at_2x_cost": True},
            {"stress_2x": {"compounded_return_pct": 0.1}, "qualifies_at_2x_cost": True},
            {"stress_2x": {"compounded_return_pct": -0.5}, "qualifies_at_2x_cost": False},
        ]
        self.assertFalse(lab.qualifies_for_portfolio_forward_paper(folds))
        folds[-1]["stress_2x"]["compounded_return_pct"] = -0.05
        self.assertTrue(lab.qualifies_for_portfolio_forward_paper(folds))


if __name__ == "__main__":
    unittest.main()
