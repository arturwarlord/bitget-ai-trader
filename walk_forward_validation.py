"""Rolling walk-forward validation of strategy selection. Read-only; never places orders."""
import json
from datetime import datetime, timezone
from pathlib import Path

import backtest as bt
import market_universe_lab as lab

OUTPUT = Path("walk-forward-report.json")
FOLDS = 4
INITIAL_TRAIN_FRACTION = 0.50
MIN_TRAIN_TRADES = 12
MIN_TEST_TRADES = 5
MIN_TEST_WINDOW_CANDLES = 200  # 90-day 1H history yields about 268 candles per fold
MIN_POSITIVE_FOLDS = 3
COST_MULTIPLIERS = (1.0, 1.5, 2.0)


def compact(report):
    return {
        "trades": report["trades"],
        "win_rate_pct": report["win_rate_pct"],
        "profit_factor": report["profit_factor"],
        "compounded_return_pct": report["compounded_unleveraged_return_pct"],
        "max_drawdown_pct": report["max_drawdown_pct"],
    }


def score(item):
    m = item["metrics"]
    pf = m["profit_factor"]
    return (
        float(pf) if isinstance(pf, (int, float)) else -1.0,
        m["compounded_return_pct"],
        -m["max_drawdown_pct"],
    )


def evaluate_market(symbol, timeframe, minutes):
    bt.SYMBOL = symbol
    bt.TIMEFRAME = timeframe
    bt.TIMEFRAME_MS = minutes * 60 * 1000
    bt.HISTORY_DAYS = lab.HISTORY_DAYS
    bt.MAX_PAGES = 55
    candles = bt.get_candles()
    quality = lab.validate_candles(candles, bt.TIMEFRAME_MS)
    n = len(candles)
    first_test = int(n * INITIAL_TRAIN_FRACTION)
    test_width = (n - first_test) // FOLDS
    if test_width < MIN_TEST_WINDOW_CANDLES:
        raise RuntimeError(
            f"Insufficient history for {FOLDS} folds: {n} candles; "
            f"each test window needs at least {MIN_TEST_WINDOW_CANDLES} candles."
        )

    baseline = {"ema_fast": 9, "ema_slow": 21, "ema_trend": 200,
                "stop_atr": 1.5, "target_atr": 2.0}
    configs = lab.candidate_configs()
    if baseline not in configs:
        configs.append(baseline)
    folds = []
    for fold_idx in range(FOLDS):
        test_start = first_test + fold_idx * test_width
        test_end = n if fold_idx == FOLDS - 1 else min(n, test_start + test_width)
        if test_end - test_start < MIN_TEST_WINDOW_CANDLES:
            continue
        training = candles[:test_start]
        selected = []
        for config in configs:
            lab.apply_config(config)
            metrics = compact(bt.run_backtest(training, timeframe_minutes=minutes))
            if metrics["trades"] >= MIN_TRAIN_TRADES:
                selected.append({"config": config, "metrics": metrics})
        if not selected:
            folds.append({
                "fold": fold_idx + 1, "train_candles": len(training),
                "test_candles": test_end - test_start, "selected_config": None,
                "test_metrics": None, "positive_after_2x_cost": False,
                "status": "no_train_candidate_with_minimum_trades",
            })
            continue
        selected.sort(key=score, reverse=True)
        config = selected[0]["config"]
        scenarios = []
        base_cost = 2 * (bt.TAKER_FEE_PER_SIDE + bt.SLIPPAGE_PER_SIDE) * 100
        for multiplier in COST_MULTIPLIERS:
            lab.apply_config(config)
            bt.ROUND_TRIP_COST_PCT = base_cost * multiplier
            result = compact(bt.run_backtest(
                candles[:test_end], timeframe_minutes=minutes,
                evaluation_start_index=test_start,
            ))
            scenarios.append({"cost_multiplier": multiplier, "metrics": result})
        normal = scenarios[0]["metrics"]
        stressed = scenarios[-1]["metrics"]
        pf = stressed["profit_factor"]
        positive = (
            stressed["trades"] >= MIN_TEST_TRADES
            and stressed["compounded_return_pct"] > 0
            and isinstance(pf, (int, float)) and pf > 1.0
        )
        folds.append({
            "fold": fold_idx + 1,
            "train_start_utc": datetime.fromtimestamp(candles[0]["timestamp"] / 1000, timezone.utc).isoformat(),
            "train_end_utc": datetime.fromtimestamp(candles[test_start - 1]["timestamp"] / 1000, timezone.utc).isoformat(),
            "test_start_utc": datetime.fromtimestamp(candles[test_start]["timestamp"] / 1000, timezone.utc).isoformat(),
            "test_end_utc": datetime.fromtimestamp(candles[test_end - 1]["timestamp"] / 1000, timezone.utc).isoformat(),
            "train_candles": len(training), "test_candles": test_end - test_start,
            "selected_config": config, "test_metrics": normal,
            "stress_1_5x": scenarios[1]["metrics"], "stress_2x": stressed,
            "positive_after_2x_cost": positive, "status": "evaluated",
        })

    evaluated = [f for f in folds if f.get("status") == "evaluated"]
    positives = sum(f["positive_after_2x_cost"] for f in evaluated)
    all_have_min_trades = len(evaluated) == FOLDS and all(
        f["stress_2x"]["trades"] >= MIN_TEST_TRADES for f in evaluated
    )
    return {
        "symbol": symbol, "timeframe": timeframe, "candles_total": n,
        "candle_quality": quality, "folds": folds,
        "evaluated_folds": len(evaluated),
        "positive_folds_after_2x_cost": positives,
        "candidate_for_forward_paper": (
            all_have_min_trades and positives >= MIN_POSITIVE_FOLDS
        ),
        "trading_enabled": False,
    }


def main():
    bt.ROUND_TRIP_COST_PCT = 2 * (bt.TAKER_FEE_PER_SIDE + bt.SLIPPAGE_PER_SIDE) * 100
    results, skipped = [], []
    for symbol in lab.SYMBOLS:
        for timeframe, minutes in lab.TIMEFRAMES:
            print(f"Walk-forward: {symbol} {timeframe}", flush=True)
            try:
                results.append(evaluate_market(symbol, timeframe, minutes))
            except Exception as exc:
                skipped.append({"symbol": symbol, "timeframe": timeframe,
                                "error": f"{type(exc).__name__}: {exc}"})
                print(f"SKIPPED {symbol} {timeframe}: {type(exc).__name__}: {exc}", flush=True)
    bt.ROUND_TRIP_COST_PCT = 2 * (bt.TAKER_FEE_PER_SIDE + bt.SLIPPAGE_PER_SIDE) * 100
    report = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Expanding-window walk-forward selection with next-window-only evaluation.",
        "folds_requested": FOLDS,
        "initial_train_fraction": INITIAL_TRAIN_FRACTION,
        "cost_multipliers": list(COST_MULTIPLIERS),
        "markets_tested": len(results),
        "skipped_markets": skipped,
        "candidates_for_forward_paper": sum(r["candidate_for_forward_paper"] for r in results),
        "results": results,
        "warnings": [
            "Strategy parameters are selected using each fold's training window only; the next window is held out.",
            "A candidate label is a paper-monitoring gate, not evidence of profitability.",
            "Funding is excluded; fee and slippage estimates may differ from actual execution.",
            "Small trade counts remain statistically weak even when a fold passes.",
            "No orders are placed; trading_enabled remains false.",
        ],
        "trading_enabled": False,
    }
    OUTPUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("===== BITGET WALK-FORWARD VALIDATION (READ-ONLY) =====", flush=True)
    print(json.dumps({
        "markets_tested": len(results), "skipped_markets": skipped,
        "candidates_for_forward_paper": report["candidates_for_forward_paper"],
        "summary": [{
            "symbol": r["symbol"], "timeframe": r["timeframe"],
            "evaluated_folds": r["evaluated_folds"],
            "positive_folds_after_2x_cost": r["positive_folds_after_2x_cost"],
            "candidate_for_forward_paper": r["candidate_for_forward_paper"],
        } for r in results],
        "trading_enabled": False,
    }, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
