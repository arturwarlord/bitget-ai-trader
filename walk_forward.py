import json
from datetime import datetime, timezone

import backtest as bt


# Fixed, pre-declared candidates. This workflow does not optimize on test folds.
CONFIGS = (
    {
        "name": "baseline_ema_9_21",
        "ema_fast": 9,
        "ema_slow": 21,
        "ema_trend": 200,
        "stop_atr": 1.5,
        "target_atr": 2.0,
    },
    {
        "name": "candidate_ema_12_30",
        "ema_fast": 12,
        "ema_slow": 30,
        "ema_trend": 200,
        "stop_atr": 2.0,
        "target_atr": 2.5,
    },
)
INITIAL_HISTORY_FRACTION = 0.40
FOLD_COUNT = 6


def fold_ranges(candle_count, initial_fraction=INITIAL_HISTORY_FRACTION, fold_count=FOLD_COUNT):
    """Return contiguous, non-overlapping test ranges after an initial warmup history."""
    if fold_count < 1:
        raise ValueError("fold_count must be at least 1")
    if not 0 < initial_fraction < 1:
        raise ValueError("initial_fraction must be between 0 and 1")
    first_test = int(candle_count * initial_fraction)
    if candle_count - first_test < fold_count * 200:
        raise ValueError("Not enough candles for the requested walk-forward folds")
    boundaries = [first_test + (candle_count - first_test) * i // fold_count
                  for i in range(fold_count + 1)]
    return [(boundaries[i], boundaries[i + 1]) for i in range(fold_count)]


def apply_config(config):
    bt.EMA_FAST = config["ema_fast"]
    bt.EMA_SLOW = config["ema_slow"]
    bt.EMA_TREND = config["ema_trend"]
    bt.STOP_ATR_MULTIPLIER = config["stop_atr"]
    bt.TAKE_PROFIT_ATR_MULTIPLIER = config["target_atr"]


def summarize(report):
    return {
        "trades": report["trades"],
        "win_rate_pct": report["win_rate_pct"],
        "profit_factor": report["profit_factor"],
        "compounded_return_pct": report["compounded_unleveraged_return_pct"],
        "max_drawdown_pct": report["max_drawdown_pct"],
        "buy_and_hold_gross_return_pct": report["buy_and_hold_gross_return_pct"],
        "buy_and_hold_after_one_round_trip_cost_pct": report["buy_and_hold_after_one_round_trip_cost_pct"],
    }


def run_walk_forward(candles, fold_count=FOLD_COUNT):
    ranges = fold_ranges(len(candles), fold_count=fold_count)
    folds = []
    per_config_returns = {config["name"]: [] for config in CONFIGS}

    for fold_number, (start, end) in enumerate(ranges, start=1):
        # Keep all prior candles available for indicator warmup, but only simulate
        # trades inside [start, end). Slicing at end prevents later candles leaking in.
        fold_candles = candles[:end]
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

        for config in CONFIGS:
            apply_config(config)
            report = bt.run_backtest(
                fold_candles,
                timeframe_minutes=5,
                evaluation_start_index=start,
            )
            metrics = summarize(report)
            fold_result["results"].append({
                "config": config,
                "metrics": metrics,
            })
            per_config_returns[config["name"]].append(metrics["compounded_return_pct"])

        folds.append(fold_result)

    aggregate = {}
    for config in CONFIGS:
        values = per_config_returns[config["name"]]
        aggregate[config["name"]] = {
            "folds": len(values),
            "profitable_folds": sum(value > 0 for value in values),
            "negative_folds": sum(value < 0 for value in values),
            "mean_fold_return_pct": round(sum(values) / len(values), 4),
            "worst_fold_return_pct": round(min(values), 4),
            "best_fold_return_pct": round(max(values), 4),
            "fold_returns_pct": [round(value, 4) for value in values],
        }

    return {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "exchange": "Bitget",
        "symbol": bt.SYMBOL,
        "timeframe": "5m",
        "history_days_requested": bt.HISTORY_DAYS,
        "candles_total": len(candles),
        "initial_history_fraction_for_indicator_warmup": INITIAL_HISTORY_FRACTION,
        "fold_count": fold_count,
        "estimated_round_trip_cost_pct": bt.ROUND_TRIP_COST_PCT,
        "fold_method": (
            "Six contiguous non-overlapping test folds covering the final 60% of history. "
            "Each fold uses only candles up to that fold's end; earlier candles are available "
            "for indicator warmup, but trade simulation starts at the fold boundary. "
            "Configurations are fixed in advance and are not selected using fold results."
        ),
        "folds": folds,
        "aggregate_by_config": aggregate,
        "trading_enabled": False,
        "warning": (
            "Research-only historical simulation, not a trading recommendation. Results may "
            "remain sensitive to market regime and sample size. Estimated fees/slippage are "
            "applied; funding is excluded. OHLC cannot reveal intrabar price order, so the "
            "backtest assumes stop first when stop and target are both touched. No orders are placed."
        ),
    }


def main():
    candles = bt.get_candles()
    report = run_walk_forward(candles)
    with open("walk-forward-report.json", "w", encoding="utf-8") as output:
        json.dump(report, output, indent=2, ensure_ascii=False)

    compact = {
        "checked_at_utc": report["checked_at_utc"],
        "candles_total": report["candles_total"],
        "fold_count": report["fold_count"],
        "estimated_round_trip_cost_pct": report["estimated_round_trip_cost_pct"],
        "aggregate_by_config": report["aggregate_by_config"],
        "folds": report["folds"],
        "trading_enabled": False,
    }
    print("\n===== BITGET WALK-FORWARD VALIDATION (READ-ONLY) =====")
    print(json.dumps(compact, indent=2, ensure_ascii=False))
    print("\nSafety: public market data only. No orders are placed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        raise
