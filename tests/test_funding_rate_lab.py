import unittest

import funding_rate_lab as funding


HOUR_MS = 60 * 60 * 1000


def record(timestamp, rate, symbol="BTCUSDT"):
    return {
        "symbol": symbol,
        "funding_timestamp_ms": timestamp,
        "funding_rate": rate,
    }


class FundingRateLabTests(unittest.TestCase):
    def test_parse_funding_payload_normalizes_settlements(self):
        payload = {
            "code": "00000",
            "data": [
                {"symbol": "BTCUSDT", "fundingTime": "2000", "fundingRate": "0.0001"},
                {"symbol": "BTCUSDT", "fundingTime": "1000", "fundingRate": "-0.0002"},
            ],
        }
        result = funding.parse_funding_payload(payload, "BTCUSDT")
        self.assertEqual([item["funding_timestamp_ms"] for item in result], [1000, 2000])
        self.assertAlmostEqual(result[0]["funding_rate"], -0.0002)

    def test_parse_rejects_api_error_and_wrong_symbol(self):
        with self.assertRaises(RuntimeError):
            funding.parse_funding_payload({"code": "400", "msg": "bad request"}, "BTCUSDT")
        with self.assertRaises(ValueError):
            funding.parse_funding_payload({
                "code": "00000",
                "data": [{"symbol": "ETHUSDT", "fundingTime": "1000", "fundingRate": "0.1"}],
            }, "BTCUSDT")

    def test_parse_rejects_non_finite_rate(self):
        with self.assertRaises(ValueError):
            funding.parse_funding_payload({
                "code": "00000",
                "data": [{"symbol": "BTCUSDT", "fundingTime": "1000", "fundingRate": "nan"}],
            }, "BTCUSDT")

    def test_fetch_paginates_until_start_boundary_and_filters_window(self):
        pages = {
            1: [record(48 * HOUR_MS, 0.0001), record(40 * HOUR_MS, 0.0002)],
            2: [record(32 * HOUR_MS, -0.0001), record(24 * HOUR_MS, 0.0003)],
        }
        calls = []

        def page_fetcher(symbol, page_no, page_size):
            calls.append((symbol, page_no, page_size))
            return pages.get(page_no, [])

        result = funding.fetch_funding_history(
            "BTCUSDT", 28 * HOUR_MS, 52 * HOUR_MS,
            page_fetcher=page_fetcher, sleep_fn=lambda _: None,
            page_size=2, max_pages=4,
        )
        self.assertEqual(result["pages_fetched"], 2)
        self.assertTrue(result["reached_start_boundary"])
        self.assertEqual(
            [item["funding_timestamp_ms"] for item in result["records"]],
            [32 * HOUR_MS, 40 * HOUR_MS, 48 * HOUR_MS],
        )
        self.assertEqual(calls, [
            ("BTCUSDT", 1, 2),
            ("BTCUSDT", 2, 2),
        ])

    def test_fetch_rejects_stalled_pagination(self):
        page = [record(48 * HOUR_MS, 0.0001), record(40 * HOUR_MS, 0.0002)]

        def page_fetcher(symbol, page_no, page_size):
            return page

        with self.assertRaises(RuntimeError):
            funding.fetch_funding_history(
                "BTCUSDT", 8 * HOUR_MS, 56 * HOUR_MS,
                page_fetcher=page_fetcher, sleep_fn=lambda _: None,
                page_size=2, max_pages=3,
            )

    def test_coverage_accepts_regular_settlements(self):
        records = [
            record(8 * HOUR_MS, 0.0001),
            record(16 * HOUR_MS, 0.0001),
            record(24 * HOUR_MS, 0.0001),
            record(32 * HOUR_MS, 0.0001),
        ]
        result = funding.validate_funding_coverage(
            records, 4 * HOUR_MS, 36 * HOUR_MS
        )
        self.assertEqual(result["settlements"], 4)
        self.assertEqual(result["median_interval_hours"], 8.0)

    def test_coverage_rejects_sparse_or_truncated_history(self):
        sparse = [
            record(8 * HOUR_MS, 0.0001),
            record(16 * HOUR_MS, 0.0001),
            record(48 * HOUR_MS, 0.0001),
            record(56 * HOUR_MS, 0.0001),
        ]
        with self.assertRaises(RuntimeError):
            funding.validate_funding_coverage(
                sparse, 4 * HOUR_MS, 60 * HOUR_MS
            )


    def test_coverage_rejects_a_missing_single_settlement(self):
        records = [
            record(8 * HOUR_MS, 0.0001),
            record(16 * HOUR_MS, 0.0001),
            record(32 * HOUR_MS, 0.0001),
            record(40 * HOUR_MS, 0.0001),
        ]
        with self.assertRaises(RuntimeError):
            funding.validate_funding_coverage(
                records, 4 * HOUR_MS, 44 * HOUR_MS
            )

    def test_positive_funding_is_cost_for_long_and_credit_for_short(self):
        records = [record(2 * HOUR_MS, 0.0001)]
        long_cost, long_events = funding.funding_cost_pct(
            "LONG", HOUR_MS, 3 * HOUR_MS, records
        )
        short_cost, short_events = funding.funding_cost_pct(
            "SHORT", HOUR_MS, 3 * HOUR_MS, records
        )
        self.assertAlmostEqual(long_cost, 0.01)
        self.assertAlmostEqual(short_cost, -0.01)
        self.assertEqual((long_events, short_events), (1, 1))

    def test_negative_funding_reverses_cash_flow(self):
        records = [record(2 * HOUR_MS, -0.0002)]
        long_cost, _ = funding.funding_cost_pct(
            1, HOUR_MS, 3 * HOUR_MS, records
        )
        short_cost, _ = funding.funding_cost_pct(
            -1, HOUR_MS, 3 * HOUR_MS, records
        )
        self.assertAlmostEqual(long_cost, -0.02)
        self.assertAlmostEqual(short_cost, 0.02)

    def test_entry_settlement_is_excluded_and_exit_settlement_included(self):
        records = [
            record(HOUR_MS, 0.0001),
            record(2 * HOUR_MS, 0.0001),
            record(3 * HOUR_MS, 0.0001),
            record(4 * HOUR_MS, 0.0001),
        ]
        cost, count = funding.funding_cost_pct(
            "LONG", HOUR_MS, 3 * HOUR_MS, records
        )
        self.assertAlmostEqual(cost, 0.02)
        self.assertEqual(count, 2)

    def test_no_settlement_during_trade_means_zero_funding(self):
        cost, count = funding.funding_cost_pct(
            "LONG", HOUR_MS, 2 * HOUR_MS, [record(3 * HOUR_MS, 0.001)]
        )
        self.assertEqual(cost, 0.0)
        self.assertEqual(count, 0)

    def test_invalid_side_and_timestamps_are_rejected(self):
        with self.assertRaises(ValueError):
            funding.funding_cost_pct("BUY", HOUR_MS, 3 * HOUR_MS, [])
        with self.assertRaises(ValueError):
            funding.funding_cost_pct("LONG", 3 * HOUR_MS, HOUR_MS, [])

    def test_funding_adjusted_metrics_apply_cashflows_before_compounding(self):
        report = {
            "max_drawdown_pct": 3.0,
            "trade_history": [
                {
                    "side": "LONG",
                    "entry_timestamp_ms": HOUR_MS,
                    "funding_exit_timestamp_ms": 3 * HOUR_MS,
                    "gross_return_pct": 0.5,
                    "estimated_cost_pct": 0.16,
                },
                {
                    "side": "SHORT",
                    "entry_timestamp_ms": 4 * HOUR_MS,
                    "funding_exit_timestamp_ms": 6 * HOUR_MS,
                    "gross_return_pct": -0.5,
                    "estimated_cost_pct": 0.16,
                },
            ],
        }
        records = [
            record(2 * HOUR_MS, 0.001),
            record(5 * HOUR_MS, -0.001),
        ]
        result = funding.funding_adjusted_trade_metrics(report, records)
        self.assertEqual(result["trades"], 2)
        self.assertAlmostEqual(result["funding_cost_sum_pct_points"], 0.2)
        self.assertAlmostEqual(result["trade_details"][0]["net_return_after_funding_pct"], 0.24)
        self.assertAlmostEqual(result["trade_details"][1]["net_return_after_funding_pct"], -0.76)
        self.assertAlmostEqual(result["profit_factor"], 0.24 / 0.76, places=5)
        expected = ((1 + 0.24 / 100) * (1 - 0.76 / 100) - 1) * 100
        self.assertAlmostEqual(result["compounded_return_pct"], expected, places=5)
        self.assertEqual(result["simulator_pre_funding_mark_to_market_max_drawdown_pct"], 3.0)

    def test_metrics_require_full_trade_history(self):
        with self.assertRaises(ValueError):
            funding.funding_adjusted_trade_metrics({"trades": 0}, [])


if __name__ == "__main__":
    unittest.main()
