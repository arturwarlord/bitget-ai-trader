import unittest

import walk_forward_validation as wf


class WalkForwardValidationTests(unittest.TestCase):
    def test_score_prefers_higher_profit_factor_before_return(self):
        better = {"metrics": {
            "profit_factor": 1.2, "compounded_return_pct": 2.0,
            "max_drawdown_pct": 5.0,
        }}
        worse = {"metrics": {
            "profit_factor": 1.1, "compounded_return_pct": 20.0,
            "max_drawdown_pct": 1.0,
        }}
        self.assertGreater(wf.score(better), wf.score(worse))

    def test_missing_profit_factor_cannot_win_by_return_alone(self):
        no_pf = {"metrics": {
            "profit_factor": None, "compounded_return_pct": 100.0,
            "max_drawdown_pct": 0.0,
        }}
        measured = {"metrics": {
            "profit_factor": 1.01, "compounded_return_pct": 0.5,
            "max_drawdown_pct": 10.0,
        }}
        self.assertGreater(wf.score(measured), wf.score(no_pf))

    def test_hourly_four_fold_window_threshold_fits_ninety_day_history(self):
        # Bitget returns about 2,149 hourly candles for 90 days; four test
        # windows after a 50% initial training period are about 268 candles.
        self.assertEqual(wf.MIN_TEST_WINDOW_CANDLES, 200)
        self.assertGreaterEqual((2149 - int(2149 * wf.INITIAL_TRAIN_FRACTION)) // wf.FOLDS,
                                wf.MIN_TEST_WINDOW_CANDLES)

    def test_compact_metrics_keeps_risk_and_trade_count(self):
        report = {
            "trades": 12, "win_rate_pct": 50.0, "profit_factor": 1.1,
            "compounded_unleveraged_return_pct": 2.3, "max_drawdown_pct": 4.0,
        }
        result = wf.compact(report)
        self.assertEqual(result["trades"], 12)
        self.assertEqual(result["profit_factor"], 1.1)
        self.assertEqual(result["max_drawdown_pct"], 4.0)
        self.assertEqual(result["compounded_return_pct"], 2.3)


if __name__ == "__main__":
    unittest.main()
