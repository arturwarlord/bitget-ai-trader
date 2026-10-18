import math
import unittest

import backtest as bt


def candle(timestamp, open_price, high, low, close):
    return {
        "timestamp": timestamp,
        "open": float(open_price),
        "high": float(high),
        "low": float(low),
        "close": float(close),
    }


def synthetic_candles(count=420):
    result = []
    previous = 100.0
    for i in range(count):
        close = 100 + 0.025 * i + 2.5 * math.sin(i / 7) + 0.8 * math.sin(i / 2.7)
        open_price = previous
        result.append(candle(
            i * bt.TIMEFRAME_MS,
            open_price,
            max(open_price, close) + 0.4,
            min(open_price, close) - 0.4,
            close,
        ))
        previous = close
    return result


class BacktestUnitTests(unittest.TestCase):
    def test_ema_initialization_and_update(self):
        values = bt.ema_series([1.0, 2.0, 3.0, 4.0], 3)
        self.assertEqual(values[:2], [None, None])
        self.assertAlmostEqual(values[2], 2.0)
        self.assertAlmostEqual(values[3], 3.0)

    def test_rsi_flat_market_is_neutral(self):
        values = bt.rsi_series([100.0] * 20, 14)
        self.assertEqual(values[13], None)
        self.assertAlmostEqual(values[14], 50.0)
        self.assertAlmostEqual(values[-1], 50.0)

    def test_atr_uses_true_range_and_wilder_smoothing(self):
        candles = [
            candle(0, 10, 11, 9, 10),
            candle(300000, 10, 12, 9, 11),
            candle(600000, 11, 13, 10, 12),
            candle(900000, 12, 12, 10, 11),
        ]
        values = bt.atr_series(candles, 2)
        # TR values are 3 and 3 for the first two eligible ranges.
        self.assertAlmostEqual(values[2], 3.0)
        self.assertAlmostEqual(values[3], 2.5)

    def test_aggregation_uses_only_complete_aligned_groups(self):
        candles = [
            candle(0, 10, 11, 9, 10.5),
            candle(300000, 10.5, 12, 10, 11.5),
            candle(600000, 11.5, 13, 11, 12.5),
            # A lone candle in the next 15-minute bucket must be discarded.
            candle(900000, 12.5, 14, 12, 13.5),
        ]
        result = bt.aggregate_candles(candles, 5, 15)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["timestamp"], 0)
        self.assertEqual(result[0]["open"], 10.0)
        self.assertEqual(result[0]["high"], 13.0)
        self.assertEqual(result[0]["low"], 9.0)
        self.assertEqual(result[0]["close"], 12.5)

    def test_signals_and_indicators_do_not_depend_on_future_candles(self):
        original = synthetic_candles(420)
        cutoff = 300
        changed_future = [dict(item) for item in original]
        for i in range(cutoff + 1, len(changed_future)):
            changed_future[i]["open"] += 500
            changed_future[i]["high"] += 500
            changed_future[i]["low"] += 500
            changed_future[i]["close"] += 500

        signals_a, atr_a = bt.make_signals(original)
        signals_b, atr_b = bt.make_signals(changed_future)
        self.assertEqual(signals_a[:cutoff + 1], signals_b[:cutoff + 1])
        self.assertEqual(atr_a[:cutoff + 1], atr_b[:cutoff + 1])

    def test_backtest_report_is_read_only_and_has_core_metrics(self):
        report = bt.run_backtest(synthetic_candles())
        self.assertFalse(report["trading_enabled"])
        self.assertIn("trades", report)
        self.assertIn("profit_factor", report)
        self.assertIn("compounded_unleveraged_return_pct", report)
        self.assertIn("max_drawdown_pct", report)
        self.assertGreaterEqual(report["trades"], 0)


if __name__ == "__main__":
    unittest.main()
