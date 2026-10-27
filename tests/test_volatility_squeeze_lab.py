import math
import unittest

from volatility_squeeze_lab import (
    linear_quantile,
    volatility_squeeze_signals,
)


def candle(i, open_price, high, low, close):
    return {
        "timestamp": i * 15 * 60 * 1000,
        "open": float(open_price),
        "high": float(high),
        "low": float(low),
        "close": float(close),
    }


def flat_candles(count=320):
    return [candle(i, 100, 101, 99, 100) for i in range(count)]


class VolatilitySqueezeTests(unittest.TestCase):
    def test_linear_quantile_interpolates(self):
        self.assertAlmostEqual(linear_quantile([0, 10, 20, 30], 50), 15.0)
        self.assertAlmostEqual(linear_quantile([1, 2, 3], 0), 1.0)
        self.assertAlmostEqual(linear_quantile([1, 2, 3], 100), 3.0)

    def test_quantile_rejects_empty_or_invalid_percentile(self):
        with self.assertRaises(ValueError):
            linear_quantile([], 30)
        with self.assertRaises(ValueError):
            linear_quantile([1, 2], 101)

    def test_current_bar_high_is_excluded_from_donchian_channel(self):
        candles = flat_candles(320)
        candles[250] = candle(250, 100, 1000, 99, 102)
        signals, atr = volatility_squeeze_signals(candles)
        self.assertEqual(signals[250], 1)
        self.assertIsNotNone(atr[250])

    def test_signals_do_not_depend_on_future_candles(self):
        candles = []
        previous = 100.0
        for i in range(420):
            close = 100 + 0.025 * i + 1.4 * math.sin(i / 5.5)
            open_price = previous
            candles.append(candle(
                i, open_price, max(open_price, close) + 0.35,
                min(open_price, close) - 0.35, close,
            ))
            previous = close

        cutoff = 340
        changed = [dict(item) for item in candles]
        for i in range(cutoff + 1, len(changed)):
            changed[i]["open"] += 500
            changed[i]["high"] += 500
            changed[i]["low"] += 500
            changed[i]["close"] += 500

        signals_a, atr_a = volatility_squeeze_signals(candles)
        signals_b, atr_b = volatility_squeeze_signals(changed)
        self.assertEqual(signals_a[:cutoff + 1], signals_b[:cutoff + 1])
        self.assertEqual(atr_a[:cutoff + 1], atr_b[:cutoff + 1])

    def test_no_signal_before_trend_and_compression_warmup(self):
        signals, atr = volatility_squeeze_signals(flat_candles(150))
        self.assertTrue(all(signal is None for signal in signals))
        self.assertTrue(all(value is None for value in atr[:14]))

    def test_fixed_parameters_are_predeclared(self):
        from volatility_squeeze_lab import (
            CHANNEL_PERIOD, TREND_PERIOD, ATR_PERIOD,
            COMPRESSION_LOOKBACK, COMPRESSION_PERCENTILE,
            STOP_ATR, TARGET_ATR,
        )
        self.assertEqual(CHANNEL_PERIOD, 20)
        self.assertEqual(TREND_PERIOD, 200)
        self.assertEqual(ATR_PERIOD, 14)
        self.assertEqual(COMPRESSION_LOOKBACK, 100)
        self.assertEqual(COMPRESSION_PERCENTILE, 30.0)
        self.assertEqual(STOP_ATR, 1.5)
        self.assertEqual(TARGET_ATR, 2.0)


if __name__ == "__main__":
    unittest.main()
