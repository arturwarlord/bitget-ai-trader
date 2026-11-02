"""Cross-asset volume-confirmed Donchian breakout research. Read-only; no orders."""

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import backtest as bt
import dmi_adx_lab as baseline_lab
import market_universe_lab as lab

OUTPUT = Path("volume-breakout-report.json")
VOLUME_STRATEGY = "donchian_20_ema200_volume_1_5x"
STRATEGIES = baseline_lab.STRATEGIES + (VOLUME_STRATEGY,)

CHANNEL_PERIOD = 20
VOLUME_LOOKBACK = 20
VOLUME_MULTIPLIER = 1.5
TREND_EMA = 200
ATR_PERIOD = 14
STOP_ATR = 1.5
TARGET_ATR = 2.0


def prior_volume_average(volumes, index, lookback=VOLUME_LOOKBACK):
    """Average only the lookback observations strictly before index."""
    if not isinstance(lookback, int) or lookback < 1:
        raise ValueError("lookback must be a positive integer")
    if index < lookback or index > len(volumes):
        raise ValueError("index does not have enough prior volume observations")
    window = volumes[index - lookback:index]
    if len(window) != lookback:
        raise ValueError("incomplete prior volume window")
    values = [float(value) for value in window]
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError("volume observations must be finite and non-negative")
    return sum(values) / lookback


def volume_breakout_signals(
    candles,
    channel_period=CHANNEL_PERIOD,
    volume_lookback=VOLUME_LOOKBACK,
    volume_multiplier=VOLUME_MULTIPLIER,
    trend_period=TREND_EMA,
    atr_period=ATR_PERIOD,
):
    """Close-confirmed Donchian breakout with prior-volume confirmation.

    The current candle's volume is compared with the mean of the preceding
    20 completed candles. The current volume is never included in its own
    reference average. The channel also excludes the signal candle.
    """
    if not isinstance(channel_period, int) or channel_period < 2:
        raise ValueError("channel_period must be an integer >= 2")
    if not isinstance(volume_lookback, int) or volume_lookback < 1:
        raise ValueError("volume_lookback must be a positive integer")
    if not math.isfinite(float(volume_multiplier)) or volume_multiplier <= 0:
        raise ValueError("volume_multiplier must be positive and finite")

    volumes = []
    for index, candle in enumerate(candles):
        value = candle.get("volume")
        if value is None:
            raise ValueError(f"missing candle volume at index {index}")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0:
            raise ValueError(f"invalid candle volume at index {index}")
        volumes.append(numeric)

    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    trend = bt.ema_series(closes, trend_period)
    atr = bt.atr_series(candles, atr_period)
    signals = [None] * len(candles)

    first_signal_index = max(channel_period + 1, volume_lookback)
    for i in range(first_signal_index, len(candles)):
        if trend[i] is None or atr[i] is None or atr[i] <= 0:
            continue
        if trend[i - 1] is None:
            continue

        average_volume = prior_volume_average(volumes, i, volume_lookback)
        if average_volume <= 0:
            continue
        if volumes[i] < volume_multiplier * average_volume:
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


def run_candidate(candles, minutes, start, end, cost, strategy):
    if strategy != VOLUME_STRATEGY:
        return baseline_lab.run_candidate(
            candles, minutes, start, end, cost, strategy
        )

    bt.ROUND_TRIP_COST_PCT = cost
    bt.EMA_FAST = 9
    bt.EMA_SLOW = 21
    bt.EMA_TREND = TREND_EMA
    bt.STOP_ATR_MULTIPLIER = STOP_ATR
    bt.TAKE_PROFIT_ATR_MULTIPLIER = TARGET_ATR

    from unittest import mock
    with mock.patch.object(bt, "make_signals", side_effect=volume_breakout_signals):
        return bt.run_backtest(
            candles[:end],
            timeframe_minutes=minutes,
            evaluation_start_index=start,
        )


def evaluate_market(symbol, timeframe, minutes):
    bt.SYMBOL = symbol
    bt.TIMEFRAME = timeframe
    bt.TIMEFRAME_MS = minutes * 60 * 1000
    bt.HISTORY_DAYS = lab.HISTORY_DAYS
    bt.MAX_PAGES = 55
    candles = bt.get_candles()
    quality = lab.validate_candles(candles, bt.TIMEFRAME_MS)

    volume_error = None
    for index, candle in enumerate(candles):
        value = candle.get("volume")
        if value is None:
            volume_error = f"Missing base volume at candle index {index}."
            break
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            volume_error = f"Invalid base volume at candle index {index}."
            break
        if not math.isfinite(numeric) or numeric < 0:
            volume_error = f"Invalid base volume at candle index {index}."
            break

    first_test = int(len(candles) * baseline_lab.INITIAL_HISTORY_FRACTION)
    width = (len(candles) - first_test) // baseline_lab.FOLD_COUNT
    if width < baseline_lab.MIN_TEST_WINDOW_CANDLES:
        raise RuntimeError(
            f"Insufficient history for {baseline_lab.FOLD_COUNT} folds: "
            f"{len(candles)} candles, test width {width}; minimum is "
            f"{baseline_lab.MIN_TEST_WINDOW_CANDLES}."
        )

    base_cost = 2 * (bt.TAKER_FEE_PER_SIDE + bt.SLIPPAGE_PER_SIDE) * 100
    folds_by_strategy = {name: [] for name in STRATEGIES}
    strategy_errors = {}
    if volume_error:
        strategy_errors[VOLUME_STRATEGY] = volume_error

    for fold_index in range(baseline_lab.FOLD_COUNT):
        start = first_test + fold_index * width
        end = len(candles) if fold_index == baseline_lab.FOLD_COUNT - 1 else min(
            len(candles), start + width
        )
        if end - start < baseline_lab.MIN_TEST_WINDOW_CANDLES:
            raise RuntimeError(
                f"Fold {fold_index + 1} is too short ({end - start} candles; "
                f"minimum is {baseline_lab.MIN_TEST_WINDOW_CANDLES})."
            )

        for strategy in STRATEGIES:
            if strategy == VOLUME_STRATEGY and volume_error:
                continue
            scenarios = []
            try:
                for multiplier in baseline_lab.COST_MULTIPLIERS:
                    cost = base_cost * multiplier
                    result = run_candidate(candles, minutes, start, end, cost, strategy)
                    scenarios.append({
                        "multiplier": multiplier,
                        "round_trip_cost_pct": round(cost, 6),
                        "metrics": compact(result),
                    })
            except Exception as exc:
                strategy_errors[strategy] = f"{type(exc).__name__}: {exc}"
                folds_by_strategy[strategy] = []
                continue

            stressed = scenarios[-1]["metrics"]
            pf = stressed["profit_factor"]
            positive = (
                stressed["trades"] >= baseline_lab.MIN_TEST_TRADES
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
        positive_folds = sum(fold["positive_after_2x_cost"] for fold in folds)
        every_fold_has_min_trades = len(folds) == baseline_lab.FOLD_COUNT and all(
            fold["stress_2x"]["trades"] >= baseline_lab.MIN_TEST_TRADES
            for fold in folds
        )
        summaries[strategy] = {
            "evaluated_folds": len(folds),
            "positive_folds_after_2x_cost": positive_folds,
            "total_test_trades_at_2x_cost": sum(
                fold["stress_2x"]["trades"] for fold in folds
            ),
            "mean_fold_return_at_base_cost_pct": round(sum(
                fold["base_cost"]["compounded_return_pct"] for fold in folds
            ) / len(folds), 4) if folds else None,
            "mean_fold_return_at_2x_cost_pct": round(sum(
                fold["stress_2x"]["compounded_return_pct"] for fold in folds
            ) / len(folds), 4) if folds else None,
            "candidate_for_forward_paper": lab.qualifies_for_forward_paper(
                folds, baseline_lab.FOLD_COUNT, baseline_lab.MIN_TEST_TRADES,
                baseline_lab.MIN_POSITIVE_FOLDS
            ),
            "error": strategy_errors.get(strategy),
            "folds": folds,
        }

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles_total": len(candles),
        "candle_quality": quality,
        "volume_coverage_pct": round(
            sum(1 for c in candles if c.get("volume") is not None) / len(candles) * 100,
            4,
        ),
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
            print(f"Volume-confirmed breakout walk-forward: {label}", flush=True)
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
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                print(f"SKIPPED {label}: {type(exc).__name__}: {exc}", flush=True)

    candidates = []
    for market in results:
        for strategy, summary in market["summaries"].items():
            if summary["candidate_for_forward_paper"]:
                candidates.append({
                    "symbol": market["symbol"],
                    "timeframe": market["timeframe"],
                    "strategy": strategy,
                    "summary": {
                        key: value for key, value in summary.items()
                        if key != "folds"
                    },
                })

    report = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": (
            "Predeclared volume-confirmed Donchian breakout compared with fixed "
            "EMA, Donchian, Trend Pullback, ATR-compression and DMI/ADX candidates "
            "on identical cross-asset snapshots, folds and execution rules."
        ),
        "symbols": list(lab.SYMBOLS),
        "timeframes": [timeframe for timeframe, _ in lab.TIMEFRAMES],
        "markets_tested": len(results),
        "skipped_markets": skipped,
        "strategies": list(STRATEGIES),
        "fixed_hypothesis": {
            "donchian_channel_period": CHANNEL_PERIOD,
            "volume_lookback_bars": VOLUME_LOOKBACK,
            "current_volume_minimum_multiple_of_prior_average": VOLUME_MULTIPLIER,
            "volume_field": "Bitget Mix candle row[5], base-coin volume",
            "trend_filter_ema": TREND_EMA,
            "atr_period": ATR_PERIOD,
            "stop_atr": STOP_ATR,
            "target_atr": TARGET_ATR,
            "entry": (
                "close-confirmed Donchian(20) breakout with current completed "
                "bar volume >= 1.5x the mean of the previous 20 completed bars, "
                "EMA200 direction filter; enter at next candle open"
            ),
            "maximum_holding_time": "four hours via shared timeframe-normalized simulator",
            "parameters_optimized_on_test_data": False,
        },
        "fold_count": baseline_lab.FOLD_COUNT,
        "initial_history_fraction": baseline_lab.INITIAL_HISTORY_FRACTION,
        "cost_scenarios": [
            {
                "multiplier": multiplier,
                "round_trip_cost_pct": round(base_cost * multiplier, 6),
            }
            for multiplier in baseline_lab.COST_MULTIPLIERS
        ],
        "candidates_for_forward_paper": candidates,
        "candidate_count": len(candidates),
        "results": results,
        "warnings": [
            "The volume-confirmation rules were fixed before the test windows and were not tuned on them.",
            "Volume is base-coin volume from Bitget Mix candle row[5]; the ratio is computed within each symbol and timeframe.",
            "All candidates use the same candle snapshot, chronological folds, next-open entries and shared exit simulator within each market/timeframe run.",
            "Fee and slippage assumptions are illustrative; funding is excluded.",
            "OHLC data cannot reconstruct intrabar path; the shared simulator assumes stop first if both stop and target are touched.",
            "The same 90-day history has been inspected across multiple hypotheses; multiple-testing/selection bias remains material and fresh forward evidence is required.",
            "No candidate is promoted automatically and no orders are placed.",
        ],
        "trading_enabled": False,
    }
    OUTPUT.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print("===== BITGET VOLUME-CONFIRMED BREAKOUT RESEARCH (READ-ONLY) =====", flush=True)
    print(json.dumps({
        "checked_at_utc": report["checked_at_utc"],
        "markets_tested": len(results),
        "skipped_markets": skipped,
        "candidate_count": len(candidates),
        "strategies": report["strategies"],
        "trading_enabled": False,
    }, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
