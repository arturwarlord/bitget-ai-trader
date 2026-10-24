import unittest
from unittest import mock

import donchian_experiment as base
import donchian_htf_experiment as htf


def candle(timestamp, open_price=100, high=101, low=99, close=100):
    return {
        "timestamp": timestamp,
        "open": float(open_price),
        "high": float(high),
        "low": float(low),
        "close": float(close),
    }


class HigherTimeframeSignalTests(unittest.TestCase):
    def setUp(self):
        self.hourly = [
            candle(i * htf.HOUR_MS, close=100)
            for i in range(220)
        ]

    def _run(self, candles, hourly=None, ema_value=90):
        hourly = hourly or self.hourly
        ema = [None] * len(hourly)
        # The EMA is defined only after the 200-candle warmup.
        for i in range(199, len(ema)):
            ema[i] = float(ema_value)
        with mock.patch.object(base, "ema_series", return_value=ema), \
             mock.patch.object(base, "atr_series", return_value=[1.0] * len(candles)):
            return htf.htf_signals(candles, hourly)

    def test_does_not_use_hourly_candle_before_its_close(self):
        # The 5m signal candle closes at 10 minutes. The first hourly candle
        # is still open, so no hourly EMA confirmation may be used.
        candles = [
            candle(i * base.TIMEFRAME_MS, high=101, low=99, close=100)
            for i in range(25)
        ]
        candles[20]["close"] = 120
        candles[20]["high"] = 121
        signals, _ = self._run(candles)
        self.assertIsNone(signals[20])

    def test_uses_latest_fully_closed_hourly_candle(self):
        # Place 5m candles just after the first hourly close. At index 20,
        # the prior 20 highs are 101 and close 120 breaks out. Hourly EMA=90.
        start = htf.HOUR_MS
        candles = [
            candle(start + i * base.TIMEFRAME_MS, high=101, low=99, close=100)
            for i in range(25)
        ]
        candles[20]["close"] = 120
        candles[20]["high"] = 121
        signals, _ = self._run(candles)
        self.assertEqual(signals[20], 1)

    def test_hourly_ema_is_used_as_direction_filter(self):
        start = htf.HOUR_MS
        candles = [
            candle(start + i * base.TIMEFRAME_MS, high=101, low=99, close=100)
            for i in range(25)
        ]
        candles[20]["close"] = 120
        candles[20]["high"] = 121
        signals, _ = self._run(candles, ema_value=125)
        self.assertIsNone(signals[20])

    def test_no_hourly_ema_before_warmup(self):
        start = htf.HOUR_MS
        candles = [
            candle(start + i * base.TIMEFRAME_MS, high=101, low=99, close=100)
            for i in range(25)
        ]
        candles[20]["close"] = 120
        candles[20]["high"] = 121
        ema = [None] * len(self.hourly)
        with mock.patch.object(base, "ema_series", return_value=ema), \
             mock.patch.object(base, "atr_series", return_value=[1.0] * len(candles)):
            signals, _ = htf.htf_signals(candles, self.hourly)
        self.assertIsNone(signals[20])


if __name__ == "__main__":
    unittest.main()
