import contextlib
import io
import json
import tempfile
import unittest
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
        self.candles = [
            candle(1_800_000_000_000),
            candle(1_800_000_900_000),
            candle(1_800_001_800_000),
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


if __name__ == "__main__":
    unittest.main()
