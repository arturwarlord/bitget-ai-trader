import unittest

import cross_sectional_momentum_lab as lab


HOUR_MS = 60 * 60 * 1000
DAY_MS = 24 * HOUR_MS


def make_candle(timestamp, price):
    return {
        "timestamp": timestamp,
        "open": float(price),
        "high": float(price) * 1.001,
        "low": float(price) * 0.999,
        "close": float(price) * 1.0005,
    }


def make_markets(daily_growth=None, days=12):
    if daily_growth is None:
        daily_growth = {
            "S0": -0.03,
            "S1": -0.02,
            "S2": -0.01,
            "S3": 0.00,
            "S4": 0.02,
            "S5": 0.03,
        }
    markets = {}
    for symbol, growth in daily_growth.items():
        candles = {}
        for index in range(days * 24 + 2):
            timestamp = index * HOUR_MS
            price = 100.0 * ((1.0 + growth) ** (index / 24.0))
            candles[timestamp] = make_candle(timestamp, price)
        markets[symbol] = candles
    return markets


class CrossSectionalMomentumTests(unittest.TestCase):
    def test_ranks_top_two_and_bottom_two_from_prior_week_momentum(self):
        markets = make_markets()
        observations, alignment = lab.build_daily_observations(
            markets, HOUR_MS, lookback_days=2, hold_days=1
        )
        self.assertGreater(len(observations), 0)
        first = observations[0]
        self.assertEqual(first["long_symbols"], ["S4", "S5"])
        self.assertEqual(first["short_symbols"], ["S0", "S1"])
        self.assertGreater(alignment["valid_rebalance_dates"], 0)

    def test_signal_uses_completed_bar_and_entry_is_next_open(self):
        markets = make_markets()
        observations, _ = lab.build_daily_observations(
            markets, HOUR_MS, lookback_days=2, hold_days=1
        )
        first = observations[0]
        self.assertEqual(first["entry_timestamp"], first["signal_timestamp"] + HOUR_MS)
        self.assertEqual(first["exit_timestamp"], first["entry_timestamp"] + DAY_MS)

    def test_future_candles_do_not_change_prior_rank_or_prices(self):
        markets = make_markets()
        original, _ = lab.build_daily_observations(
            markets, HOUR_MS, lookback_days=2, hold_days=1
        )
        target = original[0]
        changed = {symbol: dict(candles) for symbol, candles in markets.items()}
        for candles in changed.values():
            for timestamp, candle in list(candles.items()):
                if timestamp > target["exit_timestamp"]:
                    candles[timestamp] = make_candle(timestamp, 1_000_000.0)
        altered, _ = lab.build_daily_observations(
            changed, HOUR_MS, lookback_days=2, hold_days=1
        )
        self.assertEqual(target["long_symbols"], altered[0]["long_symbols"])
        self.assertEqual(target["short_symbols"], altered[0]["short_symbols"])
        self.assertEqual(target["momentum"], altered[0]["momentum"])
        self.assertEqual(target["entry_prices"], altered[0]["entry_prices"])
        self.assertEqual(target["exit_prices"], altered[0]["exit_prices"])

    def test_missing_required_timestamp_skips_rebalance_date(self):
        markets = make_markets()
        observations, alignment = lab.build_daily_observations(
            markets, HOUR_MS, lookback_days=2, hold_days=1
        )
        timestamp = observations[0]["signal_timestamp"]
        exit_timestamp = observations[0]["exit_timestamp"]
        del markets["S0"][exit_timestamp]
        changed, changed_alignment = lab.build_daily_observations(
            markets, HOUR_MS, lookback_days=2, hold_days=1
        )
        # The removed timestamp is the prior day's exit and the next day's entry,
        # so more than one adjacent rebalance date may correctly be skipped.
        self.assertGreaterEqual(
            changed_alignment["skipped_rebalance_dates"],
            alignment["skipped_rebalance_dates"] + 1,
        )
        self.assertNotIn(timestamp, [item["signal_timestamp"] for item in changed])

    def test_portfolio_return_applies_equal_weights_and_cost_to_gross_notional(self):
        observation = {
            "long_symbols": ["L1", "L2"],
            "short_symbols": ["S1", "S2"],
            "entry_prices": {"L1": 100, "L2": 100, "S1": 100, "S2": 100},
            "exit_prices": {"L1": 101, "L2": 102, "S1": 99, "S2": 98},
        }
        self.assertAlmostEqual(lab.portfolio_return_pct(observation, 0.16), 1.34)

    def test_higher_transaction_cost_cannot_improve_portfolio_return(self):
        markets = make_markets()
        observations, _ = lab.build_daily_observations(
            markets, HOUR_MS, lookback_days=2, hold_days=1
        )
        base = lab.portfolio_metrics(observations, 0.16)
        stressed = lab.portfolio_metrics(observations, 0.32)
        self.assertLess(stressed["compounded_return_pct"], base["compounded_return_pct"])

    def test_daily_folds_are_chronological_and_non_overlapping(self):
        folds = lab.chronological_folds(90)
        self.assertEqual(len(folds), 4)
        for left, right in zip(folds, folds[1:]):
            self.assertEqual(left[1], right[0])
        self.assertEqual(folds[0][0], 45)
        self.assertEqual(folds[-1][1], 90)

    def test_fold_builder_rejects_insufficient_oos_days(self):
        with self.assertRaises(RuntimeError):
            lab.chronological_folds(30)

    def test_universe_must_have_enough_symbols(self):
        markets = make_markets()
        small = {key: markets[key] for key in ("S0", "S1", "S2")}
        with self.assertRaises(ValueError):
            lab.build_daily_observations(small, HOUR_MS, lookback_days=2)

    def test_invalid_lookback_is_rejected(self):
        with self.assertRaises(ValueError):
            lab.build_daily_observations(make_markets(), HOUR_MS, lookback_days=0)

    def test_tied_momentum_uses_deterministic_symbol_order(self):
        growth = {f"S{i}": 0.0 for i in range(6)}
        observations, _ = lab.build_daily_observations(
            make_markets(growth), HOUR_MS, lookback_days=2, hold_days=1
        )
        self.assertEqual(observations[0]["short_symbols"], ["S0", "S1"])
        self.assertEqual(observations[0]["long_symbols"], ["S4", "S5"])


if __name__ == "__main__":
    unittest.main()
