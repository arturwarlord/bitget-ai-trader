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
        self.hourly = [candle(i * htf.HOUR_MS, close=100) for i in range(220)]

    def _run(self, candles, hourly=None, ema_value=90):
        hourly = hourly or self.hourly
        ema = [None] * len(hourly)
        for i in range(199, len(ema)):
            ema[i] = float(ema_value)
        with (
            mock.patch.object(base, "ema_series", return_value=ema),
            mock.patch.object(base, "atr_series", return_value=[1.0] * len(candles)),
        ):
            return htf.htf_signals(candles, hourly)

    def test_does_not_use_hourly_candle_before_its_close(self):
        # Signal closes halfway through hour 199. The last completed hourly
        # candle has EMA 125 (reject breakout); the still-forming candle has
        # EMA 90 (would accept it). This isolates timing from EMA warmup.
        signal_close_ms = 199 * htf.HOUR_MS + 30 * 60 * 1000
        start = signal_close_ms - 21 * base.TIMEFRAME_MS
        candles = [
            candle(start + i * base.TIMEFRAME_MS, high=101, low=99, close=100)
            for i in range(25)
        ]
        candles[20]["close"] = 120
        candles[20]["high"] = 121

        ema = [125.0] * len(self.hourly)
        ema[199] = 90.0
        with (
            mock.patch.object(base, "ema_series", return_value=ema),
            mock.patch.object(base, "atr_series", return_value=[1.0] * len(candles)),
        ):
            signals, _ = htf.htf_signals(candles, self.hourly)

        self.assertIsNone(signals[20])

    def test_uses_latest_fully_closed_hourly_candle(self):
        # The breakout occurs after EMA200 warmup and the latest hourly candle
        # is fully closed by the 5m signal candle's close.
        start = 205 * htf.HOUR_MS
        candles = [
            candle(start + i * base.TIMEFRAME_MS, high=101, low=99, close=100)
            for i in range(25)
        ]
        candles[20]["close"] = 120
        candles[20]["high"] = 121
        signals, _ = self._run(candles)
        self.assertEqual(signals[20], 1)

    def test_hourly_ema_is_used_as_direction_filter(self):
        # A breakout above the channel must be rejected when price is below
        # the warmed-up hourly EMA.
        start = 205 * htf.HOUR_MS
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
        with (
            mock.patch.object(base, "ema_series", return_value=[None] * len(self.hourly)),
            mock.patch.object(base, "atr_series", return_value=[1.0] * len(candles)),
        ):
            signals, _ = htf.htf_signals(candles, self.hourly)
        self.assertIsNone(signals[20])

    def test_signal_does_not_depend_on_future_hourly_candles(self):
        start = 205 * htf.HOUR_MS
        candles = [
            candle(start + i * base.TIMEFRAME_MS, high=101, low=99, close=100)
            for i in range(25)
        ]
        candles[20]["close"] = 120
        candles[20]["high"] = 121
        altered_hourly = [dict(item) for item in self.hourly]
        for i in range(207, len(altered_hourly)):
            altered_hourly[i]["close"] += 1000
            altered_hourly[i]["high"] += 1000
            altered_hourly[i]["low"] += 1000

        signals_a, atr_a = self._run(candles, hourly=self.hourly)
        signals_b, atr_b = self._run(candles, hourly=altered_hourly)
        self.assertEqual(signals_a[:21], signals_b[:21])
        self.assertEqual(atr_a[:21], atr_b[:21])


if __name__ == "__main__":
    unittest.main()
