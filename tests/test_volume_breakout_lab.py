import math
import unittest
from unittest import mock

import backtest as bt
import volume_breakout_lab as lab


def candle(i, open_price=100, high=101, low=99, close=100, volume=100):
    return {
        "timestamp": i * 15 * 60 * 1000,
        "open": float(open_price),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "volume": float(volume),
    }


def flat_candles(count=260):
    return [candle(i) for i in range(count)]


class VolumeBreakoutTests(unittest.TestCase):
    def test_prior_volume_average_excludes_current_observation(self):
        volumes = [100.0] * 20 + [10000.0]
        self.assertAlmostEqual(lab.prior_volume_average(volumes, 20), 100.0)

    def test_prior_volume_average_rejects_incomplete_window(self):
        with self.assertRaises(ValueError):
            lab.prior_volume_average([100.0] * 10, 10, 20)

    def test_long_breakout_requires_volume_confirmation(self):
        candles = flat_candles()
        candles[220] = candle(220, 100, 103, 100, 102, 200)
        with mock.patch.object(bt, "ema_series", return_value=[99.0] * len(candles)), \
             mock.patch.object(bt, "atr_series", return_value=[1.0] * len(candles)):
            signals, _ = lab.volume_breakout_signals(candles)
        self.assertEqual(signals[220], 1)

    def test_breakout_without_enough_volume_is_ignored(self):
        candles = flat_candles()
        candles[220] = candle(220, 100, 103, 100, 102, 149)
        with mock.patch.object(bt, "ema_series", return_value=[99.0] * len(candles)), \
             mock.patch.object(bt, "atr_series", return_value=[1.0] * len(candles)):
            signals, _ = lab.volume_breakout_signals(candles)
        self.assertIsNone(signals[220])

    def test_short_breakout_uses_volume_and_downtrend_filter(self):
        candles = flat_candles()
        candles[220] = candle(220, 100, 100, 97, 98, 200)
        with mock.patch.object(bt, "ema_series", return_value=[101.0] * len(candles)), \
             mock.patch.object(bt, "atr_series", return_value=[1.0] * len(candles)):
            signals, _ = lab.volume_breakout_signals(candles)
        self.assertEqual(signals[220], -1)

    def test_wrong_ema200_side_blocks_breakout(self):
        candles = flat_candles()
        candles[220] = candle(220, 100, 103, 100, 102, 200)
        with mock.patch.object(bt, "ema_series", return_value=[103.0] * len(candles)), \
             mock.patch.object(bt, "atr_series", return_value=[1.0] * len(candles)):
            signals, _ = lab.volume_breakout_signals(candles)
        self.assertIsNone(signals[220])

    def test_missing_volume_fails_closed(self):
        candles = flat_candles()
        del candles[30]["volume"]
        with self.assertRaisesRegex(ValueError, "missing candle volume"):
            lab.volume_breakout_signals(candles)

    def test_future_candles_do_not_change_past_signals_or_atr(self):
        candles = []
        previous = 100.0
        for i in range(420):
            close = 100 + 0.025 * i + 1.4 * math.sin(i / 5.5)
            open_price = previous
            candles.append(candle(
                i,
                open_price,
                max(open_price, close) + 0.35,
                min(open_price, close) - 0.35,
                close,
                100 + 15 * math.sin(i / 3.0) + 20,
            ))
            previous = close

        cutoff = 340
        changed = [dict(item) for item in candles]
        for i in range(cutoff + 1, len(changed)):
            changed[i]["open"] += 500
            changed[i]["high"] += 500
            changed[i]["low"] += 500
            changed[i]["close"] += 500
            changed[i]["volume"] *= 10

        signals_a, atr_a = lab.volume_breakout_signals(candles)
        signals_b, atr_b = lab.volume_breakout_signals(changed)
        self.assertEqual(signals_a[:cutoff + 1], signals_b[:cutoff + 1])
        self.assertEqual(atr_a[:cutoff + 1], atr_b[:cutoff + 1])

    def test_no_signal_before_ema200_warmup(self):
        signals, atr = lab.volume_breakout_signals(flat_candles(150))
        self.assertTrue(all(signal is None for signal in signals))
        self.assertIsNotNone(atr[-1])

    def test_fixed_parameters_are_predeclared(self):
        self.assertEqual(lab.CHANNEL_PERIOD, 20)
        self.assertEqual(lab.VOLUME_LOOKBACK, 20)
        self.assertEqual(lab.VOLUME_MULTIPLIER, 1.5)
        self.assertEqual(lab.TREND_EMA, 200)
        self.assertEqual(lab.STOP_ATR, 1.5)
        self.assertEqual(lab.TARGET_ATR, 2.0)


if __name__ == "__main__":
    unittest.main()
