import math
import unittest

import backtest as bt
from strategy_comparison import (
    adx_series,
    donchian_adx_signals,
    donchian_signals,
    pullback_signals,
    run_comparison,
)


def candle(timestamp, open_price, high, low, close):
    return {
        "timestamp": timestamp,
        "open": float(open_price),
        "high": float(high),
        "low": float(low),
        "close": float(close),
    }


def synthetic_candles(count=760):
    result = []
    previous = 100.0
    for i in range(count):
        close = 100 + 0.018 * i + 2.8 * math.sin(i / 8.3) + 1.1 * math.sin(i / 2.6)
        open_price = previous
        result.append(candle(
            i * bt.TIMEFRAME_MS,
            open_price,
            max(open_price, close) + 0.45,
            min(open_price, close) - 0.45,
            close,
        ))
        previous = close
    return result


class StrategyComparisonTests(unittest.TestCase):
    def test_donchian_channel_excludes_the_signal_candle_high(self):
        candles = [candle(i * bt.TIMEFRAME_MS, 100, 101, 99, 100) for i in range(230)]
        # A large current high must not be included in the prior channel.
        candles[210] = candle(210 * bt.TIMEFRAME_MS, 100, 120, 99, 103)
        signals, atr = donchian_signals(candles, period=20, trend_period=200)
        self.assertEqual(signals[210], 1)
        self.assertIsNotNone(atr[210])

    def test_adx_is_unavailable_before_warmup_and_then_finite(self):
        values = adx_series(synthetic_candles(100), period=14)
        self.assertTrue(all(value is None for value in values[:27]))
        self.assertIsNotNone(values[27])
        self.assertTrue(all(value is None or 0 <= value <= 100 for value in values))

    def test_custom_signals_and_indicators_do_not_depend_on_future_candles(self):
        original = synthetic_candles()
        cutoff = 500
        changed_future = [dict(item) for item in original]
        for i in range(cutoff + 1, len(changed_future)):
            changed_future[i]["open"] += 250
            changed_future[i]["high"] += 250
            changed_future[i]["low"] += 250
            changed_future[i]["close"] += 250

        builders = (
            lambda data: donchian_signals(data),
            lambda data: pullback_signals(data),
            lambda data: donchian_adx_signals(data),
        )
        for builder in builders:
            signals_a, atr_a = builder(original)
            signals_b, atr_b = builder(changed_future)
            self.assertEqual(signals_a[:cutoff + 1], signals_b[:cutoff + 1])
            self.assertEqual(atr_a[:cutoff + 1], atr_b[:cutoff + 1])

        adx_a = adx_series(original)
        adx_b = adx_series(changed_future)
        self.assertEqual(adx_a[:cutoff + 1], adx_b[:cutoff + 1])

    def test_all_fixed_strategy_definitions_are_present_and_read_only(self):
        candles = synthetic_candles(1400)
        report = run_comparison(candles, fold_count=3)
        self.assertFalse(report["trading_enabled"])
        self.assertEqual(
            set(report["aggregate_by_strategy"]),
            {"ema_9_21", "ema_12_30", "donchian_20", "trend_pullback", "donchian_adx20"},
        )
        self.assertEqual(report["common_execution"]["stop_atr_multiplier"], 1.5)
        self.assertEqual(report["common_execution"]["target_atr_multiplier"], 2.0)
        self.assertEqual(set(report["cost_sensitivity_by_round_trip_cost_pct"]), {
            "0.16%", "0.20%", "0.24%"
        })
        self.assertEqual(len(report["folds"]), 3)
        self.assertTrue(all(len(fold["results"]) == 5 for fold in report["folds"]))

    def test_cost_sensitivity_reuses_same_fold_boundaries(self):
        report = run_comparison(synthetic_candles(1400), fold_count=3)
        for scenario in report["fold_results_by_cost_scenario"].values():
            self.assertEqual(len(scenario), 3)
            self.assertEqual(
                [(fold["test_start_utc"], fold["test_end_utc"]) for fold in scenario],
                [(fold["test_start_utc"], fold["test_end_utc"]) for fold in report["folds"]],
            )


if __name__ == "__main__":
    unittest.main()
