import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import forward_paper_test as paper


def candle(timestamp, open_price=100.0, high=102.0, low=98.0, close=100.5):
    return {
        "timestamp": timestamp,
        "open": float(open_price),
        "high": float(high),
        "low": float(low),
        "close": float(close),
    }


class ForwardPaperStateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.state_path = root / "data" / "forward_paper_state.json"
        self.report_path = root / "forward-paper-report.json"
        self.patches = [
            mock.patch.object(paper, "STATE_PATH", self.state_path),
            mock.patch.object(paper, "REPORT_PATH", self.report_path),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        timeframe_ms = paper.exp.TIMEFRAME_MS
        current_bucket = int(datetime.now(timezone.utc).timestamp() * 1000) // timeframe_ms
        base_timestamp = (current_bucket - 4) * timeframe_ms
        self.candles = [
            candle(base_timestamp),
            candle(base_timestamp + timeframe_ms),
            candle(base_timestamp + 2 * timeframe_ms),
        ]
        self.signals = [None, None, None]
        self.atr = [1.0, 1.0, 1.0]
        self.trend = [100.0, 100.0, 100.0]
        self.slow = [101.0, 101.0, 101.0]
        self.data_patches = [
            mock.patch.object(paper.exp, "get_candles", return_value=self.candles),
            mock.patch.object(
                paper.exp,
                "build_signals",
                return_value=(self.signals, self.atr, self.trend, self.slow),
            ),
            mock.patch.object(paper.exp, "trend_regime_at", return_value="weak"),
        ]
        for patcher in self.data_patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_main(self):
        with contextlib.redirect_stdout(io.StringIO()):
            paper.main()

    def write_state(self, state):
        paper.save_json(self.state_path, state)

    def read_state(self):
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def base_state(self, last_timestamp):
        return {
            "created_at_utc": "2026-01-01T00:00:00+00:00",
            "last_processed_timestamp": last_timestamp,
            "position": {
                "side": -1,
                "entry_price": 100.0,
                "entry_timestamp": self.candles[0]["timestamp"],
                "stop": 110.0,
                "target": 90.0,
                "bars_held": 0,
                "trend_regime": "weak",
            },
            "pending": None,
            "trades": [],
            "equity": 1.0,
            "strategy": "test",
            "stop_atr_multiplier": paper.STOP_ATR_MULT,
            "target_r_multiple": paper.TARGET_R,
            "allowed_trend_regimes": ["weak", "strong"],
            "estimated_round_trip_cost_pct": paper.exp.ROUND_TRIP_COST_PCT,
        }

    def test_stale_latest_candle_fails_closed(self):
        latest = self.candles[-1]["timestamp"]
        now_ms = latest + 4 * paper.exp.TIMEFRAME_MS + 1

        with self.assertRaisesRegex(RuntimeError, "stale by more than three timeframes"):
            paper.validate_latest_candle_freshness(self.candles, now_ms=now_ms)

    def test_atomic_save_failure_preserves_previous_file(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        original = '{"keep": "previous state"}\n'
        self.state_path.write_text(original, encoding="utf-8")

        with mock.patch.object(paper.os, "replace", side_effect=OSError("simulated replace failure")):
            with self.assertRaisesRegex(OSError, "simulated replace failure"):
                paper.save_json(self.state_path, {"keep": "new state"})

        self.assertEqual(self.state_path.read_text(encoding="utf-8"), original)
        self.assertEqual(list(self.state_path.parent.glob("*.tmp")), [])
        self.assertEqual(list(self.state_path.parent.glob(".*.tmp")), [])

    def test_stale_market_data_does_not_modify_saved_state(self):
        state = self.base_state(self.candles[0]["timestamp"])
        self.write_state(state)
        before = self.state_path.read_text(encoding="utf-8")
        old_candles = [
            candle(self.candles[0]["timestamp"] - 10 * paper.exp.TIMEFRAME_MS),
            candle(self.candles[0]["timestamp"] - 9 * paper.exp.TIMEFRAME_MS),
            candle(self.candles[0]["timestamp"] - 8 * paper.exp.TIMEFRAME_MS),
        ]

        with mock.patch.object(paper.exp, "get_candles", return_value=old_candles):
            with self.assertRaisesRegex(RuntimeError, "stale by more than three timeframes"):
                self.run_main()

        self.assertEqual(self.state_path.read_text(encoding="utf-8"), before)

    def test_unclosed_latest_candle_fails_closed(self):
        latest = self.candles[-1]["timestamp"]
        now_ms = latest + paper.exp.TIMEFRAME_MS - 1

        with self.assertRaisesRegex(RuntimeError, "not closed yet"):
            paper.validate_latest_candle_freshness(self.candles, now_ms=now_ms)

    def test_repeated_run_does_not_process_same_candles_twice(self):
        self.write_state(self.base_state(self.candles[0]["timestamp"]))

        self.run_main()
        after_first_run = self.read_state()
        self.assertEqual(after_first_run["last_processed_timestamp"], self.candles[2]["timestamp"])
        self.assertEqual(after_first_run["position"]["bars_held"], 2)

        self.run_main()
        after_second_run = self.read_state()
        self.assertEqual(after_second_run["last_processed_timestamp"], self.candles[2]["timestamp"])
        self.assertEqual(after_second_run["position"]["bars_held"], 2)
        self.assertEqual(after_second_run["trades"], [])

    def test_open_position_survives_restart_and_continues_on_new_candle(self):
        self.write_state(self.base_state(self.candles[0]["timestamp"]))

        with mock.patch.object(paper.exp, "get_candles", return_value=self.candles[:2]):
            self.run_main()

        first_state = self.read_state()
        self.assertIsNotNone(first_state["position"])
        self.assertEqual(first_state["position"]["bars_held"], 1)
        self.assertEqual(first_state["trades"], [])

        self.run_main()
        second_state = self.read_state()
        self.assertIsNotNone(second_state["position"])
        self.assertEqual(second_state["position"]["bars_held"], 2)
        self.assertEqual(second_state["trades"], [])

    def test_pending_signal_opens_position_at_next_candle_open_and_persists(self):
        state = self.base_state(self.candles[0]["timestamp"])
        state["position"] = None
        state["pending"] = {"atr": 2.0, "trend_regime": "strong"}
        self.write_state(state)

        with mock.patch.object(paper.exp, "get_candles", return_value=self.candles[:2]):
            self.run_main()

        saved = self.read_state()
        self.assertIsNone(saved["pending"])
        self.assertIsNotNone(saved["position"])
        self.assertEqual(saved["position"]["entry_price"], self.candles[1]["open"])
        self.assertEqual(saved["position"]["entry_timestamp"], self.candles[1]["timestamp"])
        self.assertEqual(saved["position"]["stop"], self.candles[1]["open"] + 3.0)
        self.assertEqual(saved["position"]["target"], self.candles[1]["open"] - 4.5)
        self.assertEqual(saved["position"]["bars_held"], 1)


    def test_missing_saved_timestamp_fails_closed_without_resetting_state(self):
        state = self.base_state(1_700_000_000_000)
        self.write_state(state)

        with self.assertRaisesRegex(RuntimeError, "Refusing to reset or silently skip data"):
            self.run_main()

        saved = self.read_state()
        self.assertEqual(saved["last_processed_timestamp"], 1_700_000_000_000)
        self.assertEqual(saved["trades"], [])

    def test_missing_candle_after_last_processed_timestamp_fails_closed(self):
        state = self.base_state(self.candles[0]["timestamp"])
        self.write_state(state)
        self.candles[2]["timestamp"] += 900_000

        with self.assertRaisesRegex(RuntimeError, "Gap detected in downloaded candles"):
            self.run_main()

        saved = self.read_state()
        self.assertEqual(saved["last_processed_timestamp"], self.candles[0]["timestamp"])
        self.assertEqual(saved["trades"], [])

    def test_stop_wins_when_stop_and_target_are_both_touched_in_one_candle(self):
        self.candles[1] = candle(
            self.candles[1]["timestamp"],
            open_price=100.0,
            high=111.0,
            low=89.0,
            close=100.0,
        )
        self.write_state(self.base_state(self.candles[0]["timestamp"]))

        self.run_main()

        saved = self.read_state()
        self.assertIsNone(saved["position"])
        self.assertEqual(len(saved["trades"]), 1)
        self.assertEqual(saved["trades"][0]["exit_reason"], "stop_loss")
        self.assertEqual(saved["trades"][0]["exit_price"], 110.0)

    def test_trade_stats_handles_empty_and_no_loss_samples(self):
        self.assertEqual(
            paper.trade_stats([]),
            {
                "closed_trades": 0,
                "win_rate_pct": 0.0,
                "profit_factor": None,
                "compounded_unleveraged_return_pct": 0.0,
                "trade_to_trade_max_drawdown_pct": 0.0,
                "wins": 0,
                "losses": 0,
            },
        )
        all_wins = paper.trade_stats([
            {"net_return_pct": 2.0},
            {"net_return_pct": 1.0},
        ])
        self.assertEqual(all_wins["profit_factor"], "infinite")
        self.assertEqual(all_wins["win_rate_pct"], 100.0)
        self.assertEqual(all_wins["compounded_unleveraged_return_pct"], 3.02)

    def test_trade_stats_compounds_returns_and_counts_drawdown(self):
        stats = paper.trade_stats([
            {"net_return_pct": 10.0},
            {"net_return_pct": -10.0},
        ])
        self.assertEqual(stats["closed_trades"], 2)
        self.assertEqual(stats["wins"], 1)
        self.assertEqual(stats["losses"], 1)
        self.assertEqual(stats["profit_factor"], 1.0)
        self.assertEqual(stats["compounded_unleveraged_return_pct"], -1.0)
        self.assertEqual(stats["trade_to_trade_max_drawdown_pct"], 10.0)


if __name__ == "__main__":
    unittest.main()
