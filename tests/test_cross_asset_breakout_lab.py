import math
import unittest

import backtest as bt
from cross_asset_breakout_lab import donchian_breakout_signals


def candle(i, open_price, high, low, close):
    return {
        "timestamp": i * 15 * 60 * 1000,
        "open": float(open_price),
        "high": float(high),
        "low": float(low),
        "close": float(close),
    }


def flat_candles(count=260):
    return [candle(i, 100, 101, 99, 100) for i in range(count)]


class CrossAssetBreakoutTests(unittest.TestCase):
    def test_breakout_uses_previous_channel_and_excludes_current_high(self):
        candles = flat_candles(260)
        # The current bar's high is intentionally extreme; the channel must
        # still be based on the preceding 20 completed bars.
        candles[250] = candle(250, 100, 1000, 99, 102)
        signals, atr = donchian_breakout_signals(candles)
        self.assertEqual(signals[250], 1)
        self.assertIsNotNone(atr[250])

    def test_breakout_signals_do_not_depend_on_future_candles(self):
        candles = []
        previous = 100.0
        for i in range(320):
            close = 100 + 0.015 * i + 1.4 * math.sin(i / 5.5)
            open_price = previous
            candles.append(candle(
                i, open_price, max(open_price, close) + 0.35,
                min(open_price, close) - 0.35, close
            ))
            previous = close
        cutoff = 270
        changed = [dict(item) for item in candles]
        for i in range(cutoff + 1, len(changed)):
            changed[i]["open"] += 500
            changed[i]["high"] += 500
            changed[i]["low"] += 500
            changed[i]["close"] += 500
        signals_a, atr_a = donchian_breakout_signals(candles)
        signals_b, atr_b = donchian_breakout_signals(changed)
        self.assertEqual(signals_a[:cutoff + 1], signals_b[:cutoff + 1])
        self.assertEqual(atr_a[:cutoff + 1], atr_b[:cutoff + 1])

    def test_no_signal_before_indicator_warmup(self):
        candles = flat_candles(100)
        signals, atr = donchian_breakout_signals(candles)
        self.assertTrue(all(signal is None for signal in signals))
        self.assertTrue(all(value is None for value in atr[:14]))


if __name__ == "__main__":
    unittest.main()
