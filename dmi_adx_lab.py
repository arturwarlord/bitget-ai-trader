"""Cross-asset DMI/ADX directional-momentum research. Read-only; never places orders."""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import backtest as bt
import market_universe_lab as lab
import volatility_squeeze_lab as baseline_lab

OUTPUT = Path("dmi-adx-report.json")
INITIAL_HISTORY_FRACTION = baseline_lab.INITIAL_HISTORY_FRACTION
FOLD_COUNT = baseline_lab.FOLD_COUNT
MIN_TEST_TRADES = baseline_lab.MIN_TEST_TRADES
MIN_TEST_WINDOW_CANDLES = baseline_lab.MIN_TEST_WINDOW_CANDLES
MIN_POSITIVE_FOLDS = baseline_lab.MIN_POSITIVE_FOLDS
COST_MULTIPLIERS = baseline_lab.COST_MULTIPLIERS

DMI_PERIOD = 14
ADX_MINIMUM = 25.0
TREND_EMA = 200
STOP_ATR = 1.5
TARGET_ATR = 2.0
DMI_STRATEGY = "dmi_14_adx25_ema200"
STRATEGIES = baseline_lab.STRATEGIES + (DMI_STRATEGY,)


def dmi_adx_series(candles, period=DMI_PERIOD):
    """Return Wilder +DI, -DI and ADX series using data through each bar only."""
    if not isinstance(period, int) or period < 1:
        raise ValueError("period must be a positive integer")

    count = len(candles)
    plus_di = [None] * count
    minus_di = [None] * count
    adx = [None] * count
    if count <= 2 * period - 1:
        return plus_di, minus_di, adx

    true_range = [0.0] * count
    plus_dm = [0.0] * count
    minus_dm = [0.0] * count
    for i in range(1, count):
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        previous_high = float(candles[i - 1]["high"])
        previous_low = float(candles[i - 1]["low"])
        previous_close = float(candles[i - 1]["close"])

        up_move = high - previous_high
        down_move = previous_low - low
        plus_dm[i] = up_move if up_move > down_move and up_move > 0 else 0.0
        minus_dm[i] = down_move if down_move > up_move and down_move > 0 else 0.0
        true_range[i] = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        )

    smooth_tr = sum(true_range[1:period + 1]) / period
    smooth_plus = sum(plus_dm[1:period + 1]) / period
    smooth_minus = sum(minus_dm[1:period + 1]) / period
    dx_values = {}

    for i in range(period, count):
        if i > period:
            smooth_tr = (smooth_tr * (period - 1) + true_range[i]) / period
            smooth_plus = (smooth_plus * (period - 1) + plus_dm[i]) / period
            smooth_minus = (smooth_minus * (period - 1) + minus_dm[i]) / period

        if smooth_tr <= 0:
            positive = negative = 0.0
        else:
            positive = 100.0 * smooth_plus / smooth_tr
            negative = 100.0 * smooth_minus / smooth_tr
        plus_di[i] = positive
        minus_di[i] = negative

        denominator = positive + negative
        dx = 0.0 if denominator <= 0 else 100.0 * abs(positive - negative) / denominator
        dx_values[i] = dx

        if i == 2 * period - 1:
            adx[i] = sum(dx_values[j] for j in range(period, 2 * period)) / period
        elif i > 2 * period - 1:
            adx[i] = (adx[i - 1] * (period - 1) + dx) / period

    return plus_di, minus_di, adx


def dmi_adx_signals(candles):
    """Signal on a DI crossover with ADX strength and EMA200 direction filters.

    All conditions are evaluated at the completed signal candle close. The
    shared simulator enters at the next candle open. No future bar is read.
    """
    closes = [float(c["close"]) for c in candles]
    trend = bt.ema_series(closes, TREND_EMA)
    atr = bt.atr_series(candles, DMI_PERIOD)
    plus_di, minus_di, adx = dmi_adx_series(candles, DMI_PERIOD)
    signals = [None] * len(candles)

    for i in range(1, len(candles)):
        if any(series[i] is None for series in (trend, atr, plus_di, minus_di, adx)):
            continue
        if any(series[i - 1] is None for series in (plus_di, minus_di)):
            continue
        if atr[i] <= 0 or adx[i] < ADX_MINIMUM:
            continue

        crossed_bullish = plus_di[i - 1] <= minus_di[i - 1] and plus_di[i] > minus_di[i]
        crossed_bearish = minus_di[i - 1] <= plus_di[i - 1] and minus_di[i] > plus_di[i]

        if crossed_bullish and closes[i] > trend[i]:
            signals[i] = 1
        elif crossed_bearish and closes[i] < trend[i]:
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


def run_dmi_candidate(candles, minutes, start, end, cost):
    bt.ROUND_TRIP_COST_PCT = cost
    bt.EMA_FAST = 9
    bt.EMA_SLOW = 21
    bt.EMA_TREND = TREND_EMA
    bt.STOP_ATR_MULTIPLIER = STOP_ATR
    bt.TAKE_PROFIT_ATR_MULTIPLIER = TARGET_ATR
    with mock.patch.object(bt, "make_signals", side_effect=dmi_adx_signals):
        return bt.run_backtest(
            candles[:end],
            timeframe_minutes=minutes,
            evaluation_start_index=start,
        )


def run_candidate(candles, minutes, start, end, cost, strategy):
    if strategy == DMI_STRATEGY:
        return run_dmi_candidate(candles, minutes, start, end, cost)
    return baseline_lab.run_candidate(candles, minutes, start, end, cost, strategy)


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
            raise RuntimeError(
                f"Fold {fold_index + 1} is too short ({end - start} candles; "
                f"minimum is {MIN_TEST_WINDOW_CANDLES})."
            )

        for strategy in STRATEGIES:
            scenarios = []
            for multiplier in COST_MULTIPLIERS:
                cost = base_cost * multiplier
                result = run_candidate(candles, minutes, start, end, cost, strategy)
                scenarios.append({
                    "multiplier": multiplier,
                    "round_trip_cost_pct": round(cost, 6),
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
        positive_folds = sum(fold["positive_after_2x_cost"] for fold in folds)
        every_fold_has_min_trades = len(folds) == FOLD_COUNT and all(
            fold["stress_2x"]["trades"] >= MIN_TEST_TRADES for fold in folds
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
            print(f"DMI/ADX cross-asset walk-forward: {label}", flush=True)
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
            "Predeclared cross-asset DMI/ADX directional-momentum candidate "
            "compared with fixed EMA, Donchian, Trend Pullback and ATR-compression "
            "strategies using identical candles, folds and execution rules."
        ),
        "symbols": list(lab.SYMBOLS),
        "timeframes": [timeframe for timeframe, _ in lab.TIMEFRAMES],
        "markets_tested": len(results),
        "skipped_markets": skipped,
        "strategies": list(STRATEGIES),
        "fixed_hypothesis": {
            "directional_movement_period": DMI_PERIOD,
            "adx_minimum": ADX_MINIMUM,
            "trend_filter_ema": TREND_EMA,
            "entry": (
                "close-confirmed +DI/-DI crossover with ADX >= 25 and EMA200 "
                "direction filter; enter at next candle open"
            ),
            "exit": (
                "shared 1.5 ATR stop, 2 ATR target and four-hour maximum hold"
            ),
            "parameters_optimized_on_test_data": False,
        },
        "fold_count": FOLD_COUNT,
        "initial_history_fraction": INITIAL_HISTORY_FRACTION,
        "cost_scenarios": [
            {
                "multiplier": multiplier,
                "round_trip_cost_pct": round(base_cost * multiplier, 6),
            }
            for multiplier in COST_MULTIPLIERS
        ],
        "candidates_for_forward_paper": candidates,
        "candidate_count": len(candidates),
        "results": results,
        "warnings": [
            "The DMI/ADX rules were fixed before the test windows and were not tuned on them.",
            "All candidates use the same downloaded candle snapshot, chronological folds, next-open entries and shared exit simulator within each market/timeframe run.",
            "Fee and slippage assumptions are illustrative; funding is excluded.",
            "OHLC data cannot reconstruct intrabar path; the shared simulator assumes stop first if both stop and target are touched.",
            "The additional candidate increases multiple-testing risk; results are exploratory and require fresh forward evidence.",
            "No candidate is promoted automatically and no orders are placed.",
        ],
        "trading_enabled": False,
    }
    OUTPUT.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print("===== BITGET DMI/ADX CROSS-ASSET RESEARCH (READ-ONLY) =====", flush=True)
    print(json.dumps({
        "checked_at_utc": report["checked_at_utc"],
        "markets_tested": len(results),
        "skipped_markets": skipped,
        "candidate_count": len(candidates),
        "summary": [
            {
                "symbol": market["symbol"],
                "timeframe": market["timeframe"],
                "strategies": {
                    name: {
                        key: value for key, value in summary.items()
                        if key != "folds"
                    }
                    for name, summary in market["summaries"].items()
                },
            }
            for market in results
        ],
        "trading_enabled": False,
    }, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
