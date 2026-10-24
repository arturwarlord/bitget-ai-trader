"""Read-only, apples-to-apples walk-forward comparison of fixed entry strategies.

All candidates use the same 5-minute BTCUSDT candles, execution simulator, position
sizing, ATR exits, holding limit, fold boundaries and transaction-cost scenarios.
No parameters are selected from out-of-sample results and this module places no orders.
"""
import json
from datetime import datetime, timezone
from unittest import mock

import backtest as bt
from walk_forward import fold_ranges


BASE_COST_PCT = 0.16
COST_SCENARIOS_PCT = (0.16, 0.20, 0.24)
FOLD_COUNT = 6
INITIAL_HISTORY_FRACTION = 0.40
ATR_PERIOD = 14
MAX_HOLD_CANDLES = 48

# Predeclared configurations. Common exits isolate entry-signal differences.
STRATEGIES = (
    {
        "name": "ema_9_21",
        "kind": "ema",
        "description": "EMA 9/21 crossover with EMA200 trend filter and fixed RSI confirmation.",
        "ema_fast": 9,
        "ema_slow": 21,
    },
    {
        "name": "ema_12_30",
        "kind": "ema",
        "description": "Slower EMA 12/30 crossover with the same EMA200/RSI rules.",
        "ema_fast": 12,
        "ema_slow": 30,
    },
    {
        "name": "donchian_20",
        "kind": "donchian",
        "description": "Close breaks the prior 20-bar channel in the direction of EMA200.",
        "donchian_period": 20,
        "ema_trend": 200,
    },
    {
        "name": "trend_pullback",
        "kind": "pullback",
        "description": "EMA50/EMA200 trend alignment, EMA20 recross after a recent RSI pullback.",
        "ema_pullback": 20,
        "ema_trend": 50,
        "ema_slow": 200,
        "rsi_long_max": 42,
        "rsi_short_min": 58,
        "rsi_lookback": 4,
    },
    {
        "name": "donchian_adx20",
        "kind": "donchian_adx",
        "description": "Donchian 20 breakout with EMA200 direction filter and ADX14 >= 20.",
        "donchian_period": 20,
        "ema_trend": 200,
        "adx_period": 14,
        "adx_min": 20,
    },
)


def donchian_signals(candles, period=20, trend_period=200):
    """Return close-confirmed breakouts using only highs/lows before bar i."""
    closes = [c["close"] for c in candles]
    trend = bt.ema_series(closes, trend_period)
    atr = bt.atr_series(candles, ATR_PERIOD)
    signals = [None] * len(candles)
    for i in range(period, len(candles)):
        if trend[i] is None or atr[i] is None or atr[i] <= 0:
            continue
        prior = candles[i - period:i]
        prior_high = max(c["high"] for c in prior)
        prior_low = min(c["low"] for c in prior)
        if closes[i] > prior_high and closes[i] > trend[i]:
            signals[i] = 1
        elif closes[i] < prior_low and closes[i] < trend[i]:
            signals[i] = -1
    return signals, atr


def pullback_signals(candles, ema_pullback=20, ema_trend=50, ema_slow=200,
                     rsi_long_max=42, rsi_short_min=58, rsi_lookback=4):
    """Fixed trend-pullback hypothesis adapted to the common 5-minute sample."""
    closes = [c["close"] for c in candles]
    pullback = bt.ema_series(closes, ema_pullback)
    trend = bt.ema_series(closes, ema_trend)
    slow = bt.ema_series(closes, ema_slow)
    rsi = bt.rsi_series(closes, bt.RSI_PERIOD)
    atr = bt.atr_series(candles, ATR_PERIOD)
    signals = [None] * len(candles)

    for i in range(1, len(candles)):
        if any(series[i] is None for series in (pullback, trend, slow, rsi, atr)):
            continue
        if pullback[i - 1] is None or rsi[i] is None or atr[i] <= 0:
            continue
        crossed_up = closes[i] > pullback[i] and closes[i - 1] <= pullback[i - 1]
        crossed_down = closes[i] < pullback[i] and closes[i - 1] >= pullback[i - 1]
        recent = [
            value for value in rsi[max(0, i - rsi_lookback):i + 1]
            if value is not None
        ]
        had_long_pullback = any(value <= rsi_long_max for value in recent)
        had_short_pullback = any(value >= rsi_short_min for value in recent)

        if trend[i] > slow[i] and closes[i] > slow[i] and crossed_up and had_long_pullback:
            signals[i] = 1
        elif trend[i] < slow[i] and closes[i] < slow[i] and crossed_down and had_short_pullback:
            signals[i] = -1
    return signals, atr


def adx_series(candles, period=14):
    """Wilder ADX with an initial DX average; each output uses data through that bar."""
    n = len(candles)
    adx = [None] * n
    if n <= 2 * period:
        return adx

    tr = [None] * n
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        up = candles[i]["high"] - candles[i - 1]["high"]
        down = candles[i - 1]["low"] - candles[i]["low"]
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0
        tr[i] = max(
            candles[i]["high"] - candles[i]["low"],
            abs(candles[i]["high"] - candles[i - 1]["close"]),
            abs(candles[i]["low"] - candles[i - 1]["close"]),
        )

    sm_tr = sum(tr[1:period + 1])
    sm_plus = sum(plus_dm[1:period + 1])
    sm_minus = sum(minus_dm[1:period + 1])
    dx = [None] * n
    for i in range(period, n):
        if i > period:
            sm_tr = sm_tr - sm_tr / period + tr[i]
            sm_plus = sm_plus - sm_plus / period + plus_dm[i]
            sm_minus = sm_minus - sm_minus / period + minus_dm[i]
        plus_di = 100 * sm_plus / sm_tr if sm_tr else 0.0
        minus_di = 100 * sm_minus / sm_tr if sm_tr else 0.0
        denominator = plus_di + minus_di
        dx[i] = 100 * abs(plus_di - minus_di) / denominator if denominator else 0.0

    first_adx = 2 * period - 1
    initial_dx = [value for value in dx[period:first_adx + 1] if value is not None]
    if len(initial_dx) != period:
        return adx
    adx[first_adx] = sum(initial_dx) / period
    for i in range(first_adx + 1, n):
        if dx[i] is not None:
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
    return adx


def donchian_adx_signals(candles, period=20, trend_period=200,
                         adx_period=14, adx_min=20):
    signals, atr = donchian_signals(candles, period, trend_period)
    adx = adx_series(candles, adx_period)
    filtered = [
        signal if adx[i] is not None and adx[i] >= adx_min else None
        for i, signal in enumerate(signals)
    ]
    return filtered, atr


def _apply_common_execution(config, cost_pct):
    bt.EMA_FAST = config.get("ema_fast", 9)
    bt.EMA_SLOW = config.get("ema_slow", 21)
    bt.EMA_TREND = 200
    bt.ATR_PERIOD = ATR_PERIOD
    bt.STOP_ATR_MULTIPLIER = 1.5
    bt.TAKE_PROFIT_ATR_MULTIPLIER = 2.0
    bt.MAX_HOLD_CANDLES = MAX_HOLD_CANDLES
    bt.ROUND_TRIP_COST_PCT = cost_pct


def _run_one(candles, config, start, end, cost_pct):
    _apply_common_execution(config, cost_pct)
    sample = candles[:end]
    kind = config["kind"]
    if kind == "ema":
        # Use the production EMA signal implementation and the shared simulator.
        return bt.run_backtest(sample, timeframe_minutes=5, evaluation_start_index=start)

    if kind == "donchian":
        signal_builder = lambda data: donchian_signals(
            data, config["donchian_period"], config["ema_trend"]
        )
    elif kind == "pullback":
        signal_builder = lambda data: pullback_signals(
            data,
            config["ema_pullback"],
            config["ema_trend"],
            config["ema_slow"],
            config["rsi_long_max"],
            config["rsi_short_min"],
            config["rsi_lookback"],
        )
    elif kind == "donchian_adx":
        signal_builder = lambda data: donchian_adx_signals(
            data,
            config["donchian_period"],
            config["ema_trend"],
            config["adx_period"],
            config["adx_min"],
        )
    else:
        raise ValueError(f"Unknown strategy kind: {kind}")

    # The patched builder receives only sample candles, which end at this fold.
    with mock.patch.object(bt, "make_signals", side_effect=signal_builder):
        return bt.run_backtest(sample, timeframe_minutes=5, evaluation_start_index=start)


def _metrics(report):
    return {
        "trades": report["trades"],
        "win_rate_pct": report["win_rate_pct"],
        "profit_factor": report["profit_factor"],
        "compounded_return_pct": report["compounded_unleveraged_return_pct"],
        "max_drawdown_pct": report["max_drawdown_pct"],
        "buy_and_hold_after_one_round_trip_cost_pct": report[
            "buy_and_hold_after_one_round_trip_cost_pct"
        ],
    }


def _aggregate(fold_results):
    returns = [item["metrics"]["compounded_return_pct"] for item in fold_results]
    return {
        "folds": len(returns),
        "profitable_folds": sum(value > 0 for value in returns),
        "negative_folds": sum(value < 0 for value in returns),
        "mean_fold_return_pct": round(sum(returns) / len(returns), 4),
        "worst_fold_return_pct": round(min(returns), 4),
        "best_fold_return_pct": round(max(returns), 4),
        "fold_returns_pct": [round(value, 4) for value in returns],
        "trades_by_fold": [item["metrics"]["trades"] for item in fold_results],
    }


def run_comparison(candles, fold_count=FOLD_COUNT):
    if len(candles) < 1000:
        raise ValueError("At least 1,000 candles are required for this comparison.")
    ranges = fold_ranges(
        len(candles),
        initial_fraction=INITIAL_HISTORY_FRACTION,
        fold_count=fold_count,
    )

    original_globals = {
        key: getattr(bt, key)
        for key in (
            "EMA_FAST", "EMA_SLOW", "EMA_TREND", "ATR_PERIOD",
            "STOP_ATR_MULTIPLIER", "TAKE_PROFIT_ATR_MULTIPLIER",
            "MAX_HOLD_CANDLES", "ROUND_TRIP_COST_PCT",
        )
    }
    try:
        folds_by_cost = {}
        aggregates_by_cost = {}
        for cost_pct in COST_SCENARIOS_PCT:
            fold_results = []
            strategy_results = {item["name"]: [] for item in STRATEGIES}
            for fold_number, (start, end) in enumerate(ranges, start=1):
                fold_result = {
                    "fold": fold_number,
                    "test_start_utc": datetime.fromtimestamp(
                        candles[start]["timestamp"] / 1000, timezone.utc
                    ).isoformat(),
                    "test_end_utc": datetime.fromtimestamp(
                        candles[end - 1]["timestamp"] / 1000, timezone.utc
                    ).isoformat(),
                    "test_candles": end - start,
                    "results": [],
                }
                for config in STRATEGIES:
                    report = _run_one(candles, config, start, end, cost_pct)
                    result = {
                        "strategy": config["name"],
                        "metrics": _metrics(report),
                    }
                    strategy_results[config["name"]].append(result)
                    fold_result["results"].append({
                        "config": config,
                        "metrics": result["metrics"],
                    })
                fold_results.append(fold_result)

            folds_by_cost[f"{cost_pct:.2f}%"] = fold_results
            aggregates_by_cost[f"{cost_pct:.2f}%"] = {
                name: _aggregate(results)
                for name, results in strategy_results.items()
            }

        return {
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "exchange": "Bitget",
            "symbol": bt.SYMBOL,
            "timeframe": "5m",
            "history_days_requested": bt.HISTORY_DAYS,
            "candles_total": len(candles),
            "first_candle_utc": datetime.fromtimestamp(
                candles[0]["timestamp"] / 1000, timezone.utc
            ).isoformat(),
            "last_candle_utc": datetime.fromtimestamp(
                candles[-1]["timestamp"] / 1000, timezone.utc
            ).isoformat(),
            "initial_history_fraction_for_indicator_warmup": INITIAL_HISTORY_FRACTION,
            "fold_count": fold_count,
            "strategy_configs": list(STRATEGIES),
            "common_execution": {
                "position_sizing": "unleveraged; notional equals current equity",
                "entry_timing": "signal at candle close; enter/reverse at next candle open",
                "stop_atr_multiplier": 1.5,
                "target_atr_multiplier": 2.0,
                "max_holding_candles": MAX_HOLD_CANDLES,
                "base_round_trip_cost_pct": BASE_COST_PCT,
                "cost_scenarios_pct": list(COST_SCENARIOS_PCT),
                "cost_components_reference": "0.06% taker fee per side + 0.02% slippage per side at base",
            },
            "fold_method": (
                "Six contiguous, non-overlapping chronological test folds covering the final "
                "60% of history. Earlier candles are available only for indicator warmup; "
                "each simulation starts at its fold boundary and stops at that fold's end. "
                "All entry rules are fixed before testing; no fold is used for parameter selection."
            ),
            "folds": folds_by_cost[f"{BASE_COST_PCT:.2f}%"],
            "aggregate_by_strategy": aggregates_by_cost[f"{BASE_COST_PCT:.2f}%"],
            "cost_sensitivity_by_round_trip_cost_pct": aggregates_by_cost,
            "fold_results_by_cost_scenario": folds_by_cost,
            "trading_enabled": False,
            "warning": (
                "Research-only historical simulation, not a trading recommendation. Fee and "
                "slippage values are estimates; funding is excluded. OHLC data cannot reveal "
                "intrabar price order, so the simulator assumes stop first if stop and target "
                "are both touched. The pullback hypothesis is evaluated on 5-minute bars for "
                "apples-to-apples comparison; it may differ from the original 15-minute experiment. "
                "No orders are placed."
            ),
        }
    finally:
        for key, value in original_globals.items():
            setattr(bt, key, value)


def main():
    candles = bt.get_candles()
    report = run_comparison(candles)
    with open("strategy-comparison-report.json", "w", encoding="utf-8") as output:
        json.dump(report, output, indent=2, ensure_ascii=False)

    compact = {
        "checked_at_utc": report["checked_at_utc"],
        "candles_total": report["candles_total"],
        "fold_count": report["fold_count"],
        "common_execution": report["common_execution"],
        "aggregate_by_strategy": report["aggregate_by_strategy"],
        "cost_sensitivity_by_round_trip_cost_pct": report[
            "cost_sensitivity_by_round_trip_cost_pct"
        ],
        "trading_enabled": False,
    }
    print("\n===== BITGET STRATEGY COMPARISON (READ-ONLY) =====")
    print(json.dumps(compact, indent=2, ensure_ascii=False))
    print("\nSafety: public market data only. No orders are placed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        raise
