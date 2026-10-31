import math
import unittest
from unittest import mock

import backtest as bt
import dmi_adx_lab as lab


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


class DmiAdxTests(unittest.TestCase):
    def test_wilder_warmup_boundaries(self):
        candles = flat_candles(40)
        plus, minus, adx = lab.dmi_adx_series(candles, period=14)
        self.assertTrue(all(value is None for value in plus[:14]))
        self.assertTrue(all(value is None for value in minus[:14]))
        self.assertTrue(all(value is None for value in adx[:27]))
        self.assertIsNotNone(plus[14])
        self.assertIsNotNone(minus[14])
        self.assertIsNotNone(adx[27])

    def test_monotonic_uptrend_has_positive_directional_dominance(self):
        candles = []
        previous = 100.0
        for i in range(80):
            close = 100.0 + i
            candles.append(candle(i, previous, close + 1, previous - 0.5, close))
            previous = close
        plus, minus, adx = lab.dmi_adx_series(candles)
        self.assertGreater(plus[-1], minus[-1])
        self.assertGreater(adx[-1], 25.0)

    def test_monotonic_downtrend_has_negative_directional_dominance(self):
        candles = []
        previous = 200.0
        for i in range(80):
            close = 200.0 - i
            candles.append(candle(i, previous, previous + 0.5, close - 1, close))
            previous = close
        plus, minus, adx = lab.dmi_adx_series(candles)
        self.assertGreater(minus[-1], plus[-1])
        self.assertGreater(adx[-1], 25.0)

    def test_flat_market_has_zero_directional_movement(self):
        plus, minus, adx = lab.dmi_adx_series(flat_candles(80))
        self.assertEqual(plus[-1], 0.0)
        self.assertEqual(minus[-1], 0.0)
        self.assertEqual(adx[-1], 0.0)

    def test_invalid_period_is_rejected(self):
        with self.assertRaises(ValueError):
            lab.dmi_adx_series(flat_candles(), period=0)

    def test_bullish_di_crossover_requires_adx_and_ema_filter(self):
        candles = flat_candles(210)
        for item in candles:
            item["close"] = 101.0
        plus = [None] * len(candles)
        minus = [None] * len(candles)
        adx = [None] * len(candles)
        for i in range(200, len(candles)):
            plus[i], minus[i], adx[i] = 20.0, 25.0, 30.0
        plus[202], minus[202], adx[202] = 30.0, 20.0, 30.0
        with mock.patch.object(lab, "dmi_adx_series", return_value=(plus, minus, adx)), mock.patch.object(
            bt, "ema_series", return_value=[100.0] * len(candles)
        ), mock.patch.object(bt, "atr_series", return_value=[1.0] * len(candles)):
            signals, _ = lab.dmi_adx_signals(candles)
        self.assertEqual(signals[202], 1)

    def test_weak_adx_blocks_di_crossover(self):
        candles = flat_candles(210)
        for item in candles:
            item["close"] = 101.0
        plus = [None] * len(candles)
        minus = [None] * len(candles)
        adx = [None] * len(candles)
        for i in range(200, len(candles)):
            plus[i], minus[i], adx[i] = 20.0, 25.0, 30.0
        plus[202], minus[202], adx[202] = 30.0, 20.0, 20.0
        with mock.patch.object(lab, "dmi_adx_series", return_value=(plus, minus, adx)), mock.patch.object(
            bt, "ema_series", return_value=[100.0] * len(candles)
        ), mock.patch.object(bt, "atr_series", return_value=[1.0] * len(candles)):
            signals, _ = lab.dmi_adx_signals(candles)
        self.assertIsNone(signals[202])

    def test_wrong_ema200_side_blocks_bullish_crossover(self):
        candles = flat_candles(210)
        for item in candles:
            item["close"] = 99.0
        plus = [None] * len(candles)
        minus = [None] * len(candles)
        adx = [None] * len(candles)
        for i in range(200, len(candles)):
            plus[i], minus[i], adx[i] = 20.0, 25.0, 30.0
        plus[202], minus[202], adx[202] = 30.0, 20.0, 30.0
        with mock.patch.object(lab, "dmi_adx_series", return_value=(plus, minus, adx)), mock.patch.object(
            bt, "ema_series", return_value=[100.0] * len(candles)
        ), mock.patch.object(bt, "atr_series", return_value=[1.0] * len(candles)):
            signals, _ = lab.dmi_adx_signals(candles)
        self.assertIsNone(signals[202])

    def test_indicators_and_signals_do_not_depend_on_future_candles(self):
        candles = []
        previous = 100.0
        for i in range(520):
            close = 100 + 0.035 * i + 2.0 * math.sin(i / 7.0)
            open_price = previous
            candles.append(candle(
                i,
                open_price,
                max(open_price, close) + 0.5,
                min(open_price, close) - 0.5,
                close,
            ))
            previous = close

        cutoff = 410
        changed = [dict(item) for item in candles]
        for i in range(cutoff + 1, len(changed)):
            changed[i]["open"] += 700
            changed[i]["high"] += 700
            changed[i]["low"] += 700
            changed[i]["close"] += 700

        plus_a, minus_a, adx_a = lab.dmi_adx_series(candles)
        plus_b, minus_b, adx_b = lab.dmi_adx_series(changed)
        signals_a, atr_a = lab.dmi_adx_signals(candles)
        signals_b, atr_b = lab.dmi_adx_signals(changed)
        self.assertEqual(plus_a[:cutoff + 1], plus_b[:cutoff + 1])
        self.assertEqual(minus_a[:cutoff + 1], minus_b[:cutoff + 1])
        self.assertEqual(adx_a[:cutoff + 1], adx_b[:cutoff + 1])
        self.assertEqual(signals_a[:cutoff + 1], signals_b[:cutoff + 1])
        self.assertEqual(atr_a[:cutoff + 1], atr_b[:cutoff + 1])

    def test_parameters_are_predeclared(self):
        self.assertEqual(lab.DMI_PERIOD, 14)
        self.assertEqual(lab.ADX_MINIMUM, 25.0)
        self.assertEqual(lab.TREND_EMA, 200)
        self.assertEqual(lab.STOP_ATR, 1.5)
        self.assertEqual(lab.TARGET_ATR, 2.0)
        self.assertEqual(lab.STRATEGIES[-1], "dmi_14_adx25_ema200")


if __name__ == "__main__":
    unittest.main()
