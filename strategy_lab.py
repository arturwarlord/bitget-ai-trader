import itertools
import json
from datetime import datetime, timezone

import backtest as bt


# Small, predefined diagnostic grid. This is research only, not live trading.
FAST_EMAS = (9, 12)
SLOW_EMAS = (21, 30, 40)
TREND_EMAS = (100, 200)
STOP_ATR_MULTIPLIERS = (1.5, 2.0)
TARGET_ATR_MULTIPLIERS = (2.0, 2.5)
DEVELOPMENT_FRACTION = 0.70
TOP_CANDIDATES_FOR_HOLDOUT = 8
MIN_DEVELOPMENT_TRADES = 20


def apply_config(config):
    bt.EMA_FAST = config["ema_fast"]
    bt.EMA_SLOW = config["ema_slow"]
    bt.EMA_TREND = config["ema_trend"]
    bt.STOP_ATR_MULTIPLIER = config["stop_atr"]
    bt.TAKE_PROFIT_ATR_MULTIPLIER = config["target_atr"]


def metrics(report):
    return {
        "trades": report["trades"],
        "win_rate_pct": report["win_rate_pct"],
        "profit_factor": report["profit_factor"],
        "compounded_return_pct": report["compounded_unleveraged_return_pct"],
        "max_drawdown_pct": report["max_drawdown_pct"],
        "average_holding_minutes": report["average_holding_minutes"],
    }


def main():
    candles = bt.get_candles()
    split = int(len(candles) * DEVELOPMENT_FRACTION)
    development = candles[:split]
    holdout = candles[split:]

    if len(development) < 1000 or len(holdout) < 1000:
        raise RuntimeError("Not enough candles for a meaningful chronological split.")

    baseline = {
        "ema_fast": 9,
        "ema_slow": 21,
        "ema_trend": 200,
        "stop_atr": 1.5,
        "target_atr": 2.0,
    }

    configs = [
        {
            "ema_fast": fast,
            "ema_slow": slow,
            "ema_trend": trend,
            "stop_atr": stop,
            "target_atr": target,
        }
        for fast, slow, trend, stop, target in itertools.product(
            FAST_EMAS,
            SLOW_EMAS,
            TREND_EMAS,
            STOP_ATR_MULTIPLIERS,
            TARGET_ATR_MULTIPLIERS,
        )
        if fast < slow
    ]

    # Ensure the current baseline is included exactly once.
    if baseline not in configs:
        configs.append(baseline)

    development_results = []
    for config in configs:
        apply_config(config)
        result = bt.run_backtest(development, timeframe_minutes=5)
        development_results.append({
            "config": config,
            "development": metrics(result),
        })

    # Rank only on the first 70%; do not use holdout results for selection.
    def rank_key(item):
        report = item["development"]
        pf = report["profit_factor"]
        return (
            pf if pf is not None else -1.0,
            report["compounded_return_pct"],
            -report["max_drawdown_pct"],
        )

    development_results.sort(key=rank_key, reverse=True)
    eligible = [
        item for item in development_results
        if item["development"]["trades"] >= MIN_DEVELOPMENT_TRADES
    ]
    selected = eligible[:TOP_CANDIDATES_FOR_HOLDOUT]

    holdout_results = []
    for item in selected:
        apply_config(item["config"])
        result = bt.run_backtest(candles, timeframe_minutes=5, evaluation_start_index=split)
        holdout_results.append({
            "config": item["config"],
            "development": item["development"],
            "out_of_sample": metrics(result),
        })

    # Add the baseline's holdout result even if it did not rank in the top group.
    if not any(item["config"] == baseline for item in holdout_results):
        apply_config(baseline)
        result = bt.run_backtest(candles, timeframe_minutes=5, evaluation_start_index=split)
        baseline_dev = next(
            item["development"] for item in development_results
            if item["config"] == baseline
        )
        holdout_results.append({
            "config": baseline,
            "development": baseline_dev,
            "out_of_sample": metrics(result),
            "baseline_reference": True,
        })

    report = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "exchange": "Bitget",
        "symbol": bt.SYMBOL,
        "timeframe": "5m",
        "history_days_requested": bt.HISTORY_DAYS,
        "candles_total": len(candles),
        "development_candles": len(development),
        "holdout_candles": len(holdout),
        "development_end_utc": datetime.fromtimestamp(
            development[-1]["timestamp"] / 1000, timezone.utc
        ).isoformat(),
        "holdout_start_utc": datetime.fromtimestamp(
            holdout[0]["timestamp"] / 1000, timezone.utc
        ).isoformat(),
        "estimated_round_trip_cost_pct": bt.ROUND_TRIP_COST_PCT,
        "configs_tested_on_development": len(configs),
        "selection_method": (
            "Predefined grid ranked on the first 70% by profit factor, then compounded "
            "return, then lower drawdown. The final 30% is held out and is not used "
            "to rank candidates. Development indicators warm up within the development slice. Holdout indicators use prior history for warmup, but simulated trades begin only at the holdout boundary."
        ),
        "baseline_config": baseline,
        "top_development_candidates": development_results[:10],
        "holdout_results_for_selected_candidates": holdout_results,
        "trading_enabled": False,
        "warning": (
            "Research-only parameter comparison, not a recommendation to trade. "
            "Selecting candidates can still overfit the development period; a positive "
            "single holdout is not proof of robustness. Fees/slippage are estimates, "
            "funding is excluded, and intrabar order is unknown from OHLC candles. "
            "Holdout trades are evaluated only after the split while indicators may use prior candles for warmup."
        ),
    }

    with open("strategy-lab-report.json", "w", encoding="utf-8") as output:
        json.dump(report, output, indent=2, ensure_ascii=False)

    compact = {
        "checked_at_utc": report["checked_at_utc"],
        "candles_total": report["candles_total"],
        "configs_tested_on_development": report["configs_tested_on_development"],
        "estimated_round_trip_cost_pct": report["estimated_round_trip_cost_pct"],
        "top_development_candidates": [
            {
                "config": item["config"],
                "development": item["development"],
            }
            for item in report["top_development_candidates"][:5]
        ],
        "holdout_results_for_selected_candidates": holdout_results,
        "trading_enabled": False,
    }
    print("\n===== BITGET STRATEGY LAB (READ-ONLY) =====")
    print(json.dumps(compact, indent=2, ensure_ascii=False))
    print("\nSafety: public market data only. No orders are placed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        raise
