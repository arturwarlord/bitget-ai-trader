"""Cross-asset, multi-timeframe strategy research. Public data only; never places orders."""
import itertools
import json
from datetime import datetime, timezone

import backtest as bt

SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
    "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "LTCUSDT",
)
TIMEFRAMES = (("15m", 15), ("1H", 60))
HISTORY_DAYS = 90
DEVELOPMENT_FRACTION = 0.70
MIN_DEVELOPMENT_TRADES = 20
MIN_DEVELOPMENT_CANDLES = 700
MIN_HOLDOUT_CANDLES = 300
TOP_CANDIDATES = 5

FAST_EMAS = (9, 12, 20)
SLOW_EMAS = (21, 30, 50)
TREND_EMAS = (100, 200)
STOP_ATR_MULTIPLIERS = (1.5, 2.0)
TARGET_ATR_MULTIPLIERS = (2.0, 2.5)


def candidate_configs():
    configs = []
    for fast, slow, trend, stop, target in itertools.product(
        FAST_EMAS, SLOW_EMAS, TREND_EMAS,
        STOP_ATR_MULTIPLIERS, TARGET_ATR_MULTIPLIERS,
    ):
        if fast < slow:
            configs.append({
                "ema_fast": fast,
                "ema_slow": slow,
                "ema_trend": trend,
                "stop_atr": stop,
                "target_atr": target,
            })
    return configs


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


def validate_candles(candles, timeframe_ms, now_ms=None):
    if not candles:
        raise RuntimeError("No candles received.")
    timestamps = [int(candle["timestamp"]) for candle in candles]
    if any(current <= previous for previous, current in zip(timestamps, timestamps[1:])):
        raise RuntimeError("Candle timestamps are not strictly increasing.")
    for previous, current in zip(timestamps, timestamps[1:]):
        if current - previous != timeframe_ms:
            raise RuntimeError("Gap detected in candle history; refusing to score this market/timeframe.")
    if now_ms is None:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    latest_closed_at = timestamps[-1] + timeframe_ms
    if latest_closed_at > now_ms:
        raise RuntimeError("Latest candle is not closed yet.")
    if now_ms - latest_closed_at > 3 * timeframe_ms:
        raise RuntimeError("Latest closed candle is stale by more than three timeframes.")
    return True


def rank_key(item):
    result = item["development"]
    pf = result["profit_factor"]
    return (
        float(pf) if isinstance(pf, (int, float)) else -1.0,
        result["compounded_return_pct"],
        -result["max_drawdown_pct"],
    )


def research_market(symbol, timeframe, minutes):
    bt.SYMBOL = symbol
    bt.TIMEFRAME = timeframe
    bt.TIMEFRAME_MS = minutes * 60 * 1000
    bt.HISTORY_DAYS = HISTORY_DAYS
    # 55 pages cover 90 days of 15m candles at 200 rows/page with some headroom.
    bt.MAX_PAGES = 55

    candles = bt.get_candles()
    validate_candles(candles, bt.TIMEFRAME_MS)
    split = int(len(candles) * DEVELOPMENT_FRACTION)
    development = candles[:split]
    holdout_count = len(candles) - split
    if len(development) < MIN_DEVELOPMENT_CANDLES or holdout_count < MIN_HOLDOUT_CANDLES:
        raise RuntimeError(
            f"Insufficient sample: {len(development)} development candles, "
            f"{holdout_count} holdout candles."
        )

    results = []
    configs = candidate_configs()
    baseline = {
        "ema_fast": 9, "ema_slow": 21, "ema_trend": 200,
        "stop_atr": 1.5, "target_atr": 2.0,
    }
    if baseline not in configs:
        configs.append(baseline)

    for config in configs:
        apply_config(config)
        dev_report = bt.run_backtest(development, timeframe_minutes=minutes)
        results.append({"config": config, "development": metrics(dev_report)})

    results.sort(key=rank_key, reverse=True)
    eligible = [
        item for item in results
        if item["development"]["trades"] >= MIN_DEVELOPMENT_TRADES
    ]
    selected = eligible[:TOP_CANDIDATES]
    if not any(item["config"] == baseline for item in selected):
        baseline_result = next(item for item in results if item["config"] == baseline)
        selected.append(baseline_result)

    holdout_results = []
    for item in selected:
        apply_config(item["config"])
        holdout_report = bt.run_backtest(
            candles, timeframe_minutes=minutes, evaluation_start_index=split
        )
        holdout_results.append({
            "config": item["config"],
            "development": item["development"],
            "out_of_sample": metrics(holdout_report),
            "eligible_for_forward_paper": (
                item["development"]["trades"] >= MIN_DEVELOPMENT_TRADES
                and metrics(holdout_report)["trades"] >= 10
                and metrics(holdout_report)["profit_factor"] is not None
                and metrics(holdout_report)["profit_factor"] > 1.0
                and metrics(holdout_report)["compounded_return_pct"] > 0
            ),
        })

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles_total": len(candles),
        "development_candles": len(development),
        "holdout_candles": holdout_count,
        "development_end_utc": datetime.fromtimestamp(
            development[-1]["timestamp"] / 1000, timezone.utc
        ).isoformat(),
        "holdout_start_utc": datetime.fromtimestamp(
            candles[split]["timestamp"] / 1000, timezone.utc
        ).isoformat(),
        "estimated_round_trip_cost_pct": bt.ROUND_TRIP_COST_PCT,
        "configs_tested": len(configs),
        "top_development_candidates": results[:5],
        "holdout_results": holdout_results,
        "trading_enabled": False,
    }


def main():
    # Keep the same illustrative execution costs across assets for fair comparison.
    bt.ROUND_TRIP_COST_PCT = 2 * (bt.TAKER_FEE_PER_SIDE + bt.SLIPPAGE_PER_SIDE) * 100
    successful = []
    failures = []
    for symbol in SYMBOLS:
        for timeframe, minutes in TIMEFRAMES:
            label = f"{symbol} {timeframe}"
            print(f"\n--- Researching {label} ---", flush=True)
            try:
                result = research_market(symbol, timeframe, minutes)
                successful.append(result)
                best = result["holdout_results"]
                print(json.dumps({
                    "market": label,
                    "candles": result["candles_total"],
                    "configs": result["configs_tested"],
                    "holdout_shortlist": [
                        {
                            "config": row["config"],
                            "oos": row["out_of_sample"],
                            "eligible_for_forward_paper": row["eligible_for_forward_paper"],
                        } for row in best
                    ],
                }, ensure_ascii=False), flush=True)
            except Exception as exc:
                failures.append({"symbol": symbol, "timeframe": timeframe,
                                 "error": f"{type(exc).__name__}: {exc}"})
                print(f"SKIPPED {label}: {type(exc).__name__}: {exc}", flush=True)

    if not successful:
        raise RuntimeError("No market/timeframe completed successfully; see errors above.")

    # Cross-market ranking uses OOS only as a screening stage, not as a final proof.
    leaderboard = []
    for market in successful:
        for row in market["holdout_results"]:
            oos = row["out_of_sample"]
            pf = oos["profit_factor"]
            leaderboard.append({
                "symbol": market["symbol"],
                "timeframe": market["timeframe"],
                "config": row["config"],
                "development": row["development"],
                "out_of_sample": oos,
                "eligible_for_forward_paper": row["eligible_for_forward_paper"],
                "screening_note": "Candidate only; requires walk-forward and forward paper validation.",
                "_rank": (
                    float(pf) if isinstance(pf, (int, float)) else -1.0,
                    oos["compounded_return_pct"],
                    -oos["max_drawdown_pct"],
                    oos["trades"],
                ),
            })
    leaderboard.sort(key=lambda item: item["_rank"], reverse=True)
    for item in leaderboard:
        item.pop("_rank", None)

    report = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "exchange": "Bitget",
        "market_type": "USDT-FUTURES",
        "symbols_requested": list(SYMBOLS),
        "timeframes_requested": [tf for tf, _ in TIMEFRAMES],
        "history_days_requested": HISTORY_DAYS,
        "successful_market_timeframes": len(successful),
        "failed_market_timeframes": failures,
        "total_configs_tested": sum(item["configs_tested"] for item in successful),
        "cost_assumptions": {
            "taker_fee_per_side_pct": bt.TAKER_FEE_PER_SIDE * 100,
            "slippage_per_side_pct": bt.SLIPPAGE_PER_SIDE * 100,
            "round_trip_pct": bt.ROUND_TRIP_COST_PCT,
            "funding_included": False,
        },
        "selection_method": (
            "Predefined EMA/ATR grid ranked on first 70% by development profit factor, "
            "then compounded return and lower drawdown. Only shortlisted configurations "
            "are evaluated on the chronological final 30%. Cross-market leaderboard is "
            "a screening aid, not a claim of profitability."
        ),
        "markets": successful,
        "cross_market_leaderboard": leaderboard[:30],
        "eligible_forward_paper_candidates": [
            item for item in leaderboard if item["eligible_for_forward_paper"]
        ],
        "warnings": [
            "Research-only; public market data only; no orders are placed.",
            "Costs are illustrative and identical across markets; actual fees, spread, slippage and funding vary.",
            "A single chronological holdout is not proof of an edge; repeated selection can overfit.",
            "The same fixed parameter grid is tested across markets; candidates need walk-forward and paper validation.",
            "Symbols that are unavailable or have insufficient history are recorded as failures and skipped.",
        ],
        "trading_enabled": False,
    }
    with open("market-universe-report.json", "w", encoding="utf-8") as output:
        json.dump(report, output, indent=2, ensure_ascii=False)
    print("\n===== BITGET MULTI-ASSET STRATEGY RESEARCH (READ-ONLY) =====")
    print(json.dumps({
        "checked_at_utc": report["checked_at_utc"],
        "successful_market_timeframes": report["successful_market_timeframes"],
        "failed_market_timeframes": failures,
        "total_configs_tested": report["total_configs_tested"],
        "cross_market_leaderboard": leaderboard[:15],
        "eligible_forward_paper_candidates": report["eligible_forward_paper_candidates"],
        "trading_enabled": False,
    }, indent=2, ensure_ascii=False))
    print("\nSafety: public market data only; no trading orders are placed.")


if __name__ == "__main__":
    main()
