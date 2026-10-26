"""Cross-asset Donchian breakout research against a fixed EMA baseline.

Uses public closed candles and the shared read-only backtest simulator. No orders.
The hypothesis is predeclared: 20-bar channel breakout, EMA(200) trend filter,
ATR(14) exits, 1.5 ATR stop, 2 ATR target, and the shared four-hour holding cap.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import backtest as bt
import market_universe_lab as lab

OUTPUT = Path("cross-asset-breakout-report.json")
INITIAL_HISTORY_FRACTION = 0.50
FOLD_COUNT = 4
MIN_TEST_TRADES = 5
MIN_TEST_WINDOW_CANDLES = 200  # Allows four test windows in a 90-day 1H history.
MIN_POSITIVE_FOLDS = 3
COST_MULTIPLIERS = (1.0, 1.5, 2.0)
CHANNEL_PERIOD = 20
TREND_PERIOD = 200
ATR_PERIOD = 14
STOP_ATR = 1.5
TARGET_ATR = 2.0
STRATEGIES = ("ema_9_21_baseline", "donchian_20_ema200")


def donchian_breakout_signals(candles, channel_period=CHANNEL_PERIOD,
                              trend_period=TREND_PERIOD, atr_period=ATR_PERIOD):
    """Create close-confirmed channel breakout events using only past bars.

    The channel for bar i uses bars [i-channel_period, i), excluding bar i.
    Signals are executed by the shared simulator at the next bar's open.
    """
    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    trend = bt.ema_series(closes, trend_period)
    atr = bt.atr_series(candles, atr_period)
    signals = [None] * len(candles)

    for i in range(channel_period + 1, len(candles)):
        if trend[i] is None or atr[i] is None or atr[i] <= 0:
            continue
        if trend[i - 1] is None:
            continue
        upper = max(highs[i - channel_period:i])
        lower = min(lows[i - channel_period:i])
        previous_upper = max(highs[i - channel_period - 1:i - 1])
        previous_lower = min(lows[i - channel_period - 1:i - 1])

        crossed_above = closes[i] > upper and closes[i - 1] <= previous_upper
        crossed_below = closes[i] < lower and closes[i - 1] >= previous_lower
        if crossed_above and closes[i] > trend[i]:
            signals[i] = 1
        elif crossed_below and closes[i] < trend[i]:
            signals[i] = -1

    return signals, atr


def compact(report):
    return {
        "trades": report["trades"],
        "win_rate_pct": report["win_rate_pct"],
        "profit_factor": report["profit_factor"],
        "compounded_return_pct": report["compounded_unleveraged_return_pct"],
        "max_drawdown_pct": report["max_drawdown_pct"],
    }


def cost_scenarios(base_cost):
    return [
        {"multiplier": multiplier, "round_trip_cost_pct": round(base_cost * multiplier, 6)}
        for multiplier in COST_MULTIPLIERS
    ]


def run_candidate(candles, minutes, start, end, cost, strategy):
    bt.ROUND_TRIP_COST_PCT = cost
    bt.EMA_FAST = 9
    bt.EMA_SLOW = 21
    bt.EMA_TREND = TREND_PERIOD
    bt.STOP_ATR_MULTIPLIER = STOP_ATR
    bt.TAKE_PROFIT_ATR_MULTIPLIER = TARGET_ATR
    sample = candles[:end]
    if strategy == "donchian_20_ema200":
        with mock.patch.object(bt, "make_signals", side_effect=donchian_breakout_signals):
            return bt.run_backtest(
                sample, timeframe_minutes=minutes, evaluation_start_index=start
            )
    return bt.run_backtest(sample, timeframe_minutes=minutes, evaluation_start_index=start)


def evaluate_market(symbol, timeframe, minutes):
    bt.SYMBOL = symbol
    bt.TIMEFRAME = timeframe
    bt.TIMEFRAME_MS = minutes * 60 * 1000
    bt.HISTORY_DAYS = lab.HISTORY_DAYS
    bt.MAX_PAGES = 55
    candles = bt.get_candles()
    quality = lab.validate_candles(candles, bt.TIMEFRAME_MS)

    first_test = int(len(candles) * INITIAL_HISTORY_FRACTION)
    width = (len(candles) - first_test) // FOLD_COUNT
    if width < MIN_TEST_WINDOW_CANDLES:
        raise RuntimeError(
            f"Insufficient history for {FOLD_COUNT} folds: {len(candles)} candles, "
            f"test width {width}; minimum is {MIN_TEST_WINDOW_CANDLES}."
        )

    base_cost = 2 * (bt.TAKER_FEE_PER_SIDE + bt.SLIPPAGE_PER_SIDE) * 100
    folds_by_strategy = {name: [] for name in STRATEGIES}
    for fold_index in range(FOLD_COUNT):
        start = first_test + fold_index * width
        end = len(candles) if fold_index == FOLD_COUNT - 1 else min(
            len(candles), start + width
        )
        if end - start < MIN_TEST_WINDOW_CANDLES:
            raise RuntimeError(f"Fold {fold_index + 1} is too short ({end - start} candles; minimum is {MIN_TEST_WINDOW_CANDLES}).")
        for strategy in STRATEGIES:
            scenarios = []
            for scenario in cost_scenarios(base_cost):
                result = run_candidate(
                    candles, minutes, start, end,
                    scenario["round_trip_cost_pct"], strategy,
                )
                scenarios.append({
                    **scenario,
                    "metrics": compact(result),
                })
            stressed = scenarios[-1]["metrics"]
            pf = stressed["profit_factor"]
            positive = (
                stressed["trades"] >= MIN_TEST_TRADES
                and stressed["compounded_return_pct"] > 0
                and isinstance(pf, (int, float))
                and pf > 1.0
            )
            folds_by_strategy[strategy].append({
                "fold": fold_index + 1,
                "train_history_candles": start,
                "test_candles": end - start,
                "test_start_utc": datetime.fromtimestamp(
                    candles[start]["timestamp"] / 1000, timezone.utc
                ).isoformat(),
                "test_end_utc": datetime.fromtimestamp(
                    candles[end - 1]["timestamp"] / 1000, timezone.utc
                ).isoformat(),
                "base_cost": scenarios[0]["metrics"],
                "stress_1_5x": scenarios[1]["metrics"],
                "stress_2x": stressed,
                "positive_after_2x_cost": positive,
            })

    summaries = {}
    for strategy, folds in folds_by_strategy.items():
        positive_folds = sum(f["positive_after_2x_cost"] for f in folds)
        every_fold_has_min_trades = len(folds) == FOLD_COUNT and all(
            f["stress_2x"]["trades"] >= MIN_TEST_TRADES for f in folds
        )
        summaries[strategy] = {
            "evaluated_folds": len(folds),
            "positive_folds_after_2x_cost": positive_folds,
            "total_test_trades_at_2x_cost": sum(f["stress_2x"]["trades"] for f in folds),
            "mean_fold_return_at_base_cost_pct": round(sum(
                f["base_cost"]["compounded_return_pct"] for f in folds
            ) / len(folds), 4) if folds else None,
            "mean_fold_return_at_2x_cost_pct": round(sum(
                f["stress_2x"]["compounded_return_pct"] for f in folds
            ) / len(folds), 4) if folds else None,
            "candidate_for_forward_paper": (
                every_fold_has_min_trades and positive_folds >= MIN_POSITIVE_FOLDS
            ),
            "folds": folds,
        }
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles_total": len(candles),
        "candle_quality": quality,
        "initial_history_fraction": INITIAL_HISTORY_FRACTION,
        "summaries": summaries,
        "trading_enabled": False,
    }


def main():
    base_cost = 2 * (bt.TAKER_FEE_PER_SIDE + bt.SLIPPAGE_PER_SIDE) * 100
    results = []
    skipped = []
    for symbol in lab.SYMBOLS:
        for timeframe, minutes in lab.TIMEFRAMES:
            label = f"{symbol} {timeframe}"
            print(f"Breakout walk-forward: {label}", flush=True)
            try:
                result = evaluate_market(symbol, timeframe, minutes)
                results.append(result)
                print(json.dumps({
                    "market": label,
                    "summaries": {
                        name: {
                            key: value for key, value in summary.items()
                            if key != "folds"
                        }
                        for name, summary in result["summaries"].items()
                    },
                }, ensure_ascii=False), flush=True)
            except Exception as exc:
                skipped.append({
                    "symbol": symbol, "timeframe": timeframe,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                print(f"SKIPPED {label}: {type(exc).__name__}: {exc}", flush=True)

    candidate_rows = []
    for market in results:
        for strategy, summary in market["summaries"].items():
            if summary["candidate_for_forward_paper"]:
                candidate_rows.append({
                    "symbol": market["symbol"],
                    "timeframe": market["timeframe"],
                    "strategy": strategy,
                    "summary": {key: value for key, value in summary.items() if key != "folds"},
                })

    report = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Predeclared cross-asset Donchian breakout vs fixed EMA baseline; expanding-history, next-window-only evaluation.",
        "symbols": list(lab.SYMBOLS),
        "timeframes": [tf for tf, _ in lab.TIMEFRAMES],
        "markets_tested": len(results),
        "skipped_markets": skipped,
        "strategies": list(STRATEGIES),
        "fixed_hypothesis": {
            "donchian_period": CHANNEL_PERIOD,
            "trend_filter_ema": TREND_PERIOD,
            "atr_period": ATR_PERIOD,
            "stop_atr": STOP_ATR,
            "target_atr": TARGET_ATR,
            "maximum_holding_time": "4 hours, using the shared simulator's timeframe-normalized cap",
            "entry_execution": "next candle open after a close-confirmed breakout",
            "parameters_optimized_on_test_data": False,
        },
        "fold_count": FOLD_COUNT,
        "initial_history_fraction": INITIAL_HISTORY_FRACTION,
        "cost_scenarios": cost_scenarios(base_cost),
        "candidates_for_forward_paper": candidate_rows,
        "candidate_count": len(candidate_rows),
        "results": results,
        "warnings": [
            "No candidate is promoted automatically; forward paper evidence is still required.",
            "The fixed parameters were declared before this evaluation and are not tuned on test folds.",
            "Fee and slippage assumptions are illustrative; funding is excluded.",
            "OHLC cannot reveal intrabar path; the shared simulator assumes stop first if stop and target are both touched.",
            "The four-hour cap and execution rules are shared by both candidates.",
            "No orders are placed; trading remains disabled.",
        ],
        "trading_enabled": False,
    }
    OUTPUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("===== BITGET CROSS-ASSET BREAKOUT WALK-FORWARD (READ-ONLY) =====", flush=True)
    print(json.dumps({
        "markets_tested": report["markets_tested"],
        "skipped_markets": report["skipped_markets"],
        "candidate_count": report["candidate_count"],
        "candidates_for_forward_paper": report["candidates_for_forward_paper"],
        "summary": [
            {
                "symbol": market["symbol"],
                "timeframe": market["timeframe"],
                "strategies": {
                    name: {
                        "positive_folds_after_2x_cost": summary["positive_folds_after_2x_cost"],
                        "total_test_trades_at_2x_cost": summary["total_test_trades_at_2x_cost"],
                        "mean_fold_return_at_base_cost_pct": summary["mean_fold_return_at_base_cost_pct"],
                        "mean_fold_return_at_2x_cost_pct": summary["mean_fold_return_at_2x_cost_pct"],
                        "candidate_for_forward_paper": summary["candidate_for_forward_paper"],
                    }
                    for name, summary in market["summaries"].items()
                },
            } for market in results
        ],
        "trading_enabled": False,
    }, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
