"""Funding-aware cross-asset walk-forward audit. Public data only; no orders."""

import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import backtest as bt
import funding_rate_lab as funding
import market_universe_lab as lab
import volume_breakout_lab as candidates

OUTPUT = Path("funding-aware-cross-asset-report.json")
COST_MULTIPLIERS = (1.0, 1.5, 2.0)
DAY_MS = 24 * 60 * 60 * 1000
FUNDING_LOOKBACK_DAYS = 60
STRATEGIES = candidates.STRATEGIES


def compact_backtest(report):
    return {
        "trades": report["trades"],
        "win_rate_pct": report["win_rate_pct"],
        "profit_factor": report["profit_factor"],
        "compounded_return_pct": report["compounded_unleveraged_return_pct"],
        "simulator_mark_to_market_max_drawdown_pct": report["max_drawdown_pct"],
    }


def run_candidate_with_history(candles, minutes, start, end, cost, strategy):
    """Capture every closed trade without changing the candidate signal logic."""
    original_run_backtest = bt.run_backtest

    def include_history(*args, **kwargs):
        kwargs["include_trade_history"] = True
        return original_run_backtest(*args, **kwargs)

    with mock.patch.object(bt, "run_backtest", side_effect=include_history):
        return candidates.run_candidate(
            candles, minutes, start, end, cost, strategy
        )


def evaluate_market(symbol, timeframe, minutes, funding_records, funding_quality):
    bt.SYMBOL = symbol
    bt.TIMEFRAME = timeframe
    bt.TIMEFRAME_MS = minutes * 60 * 1000
    bt.HISTORY_DAYS = lab.HISTORY_DAYS
    bt.MAX_PAGES = 55
    candles = bt.get_candles()
    candle_quality = lab.validate_candles(candles, bt.TIMEFRAME_MS)

    first_test = int(len(candles) * candidates.baseline_lab.INITIAL_HISTORY_FRACTION)
    width = (len(candles) - first_test) // candidates.baseline_lab.FOLD_COUNT
    if width < candidates.baseline_lab.MIN_TEST_WINDOW_CANDLES:
        raise RuntimeError(
            f"Insufficient history for {candidates.baseline_lab.FOLD_COUNT} folds: "
            f"{len(candles)} candles, test width {width}."
        )

    funding_start_ms = int(candles[first_test]["timestamp"])
    funding_end_ms = int(candles[-1]["timestamp"] + bt.TIMEFRAME_MS)
    coverage = funding.validate_funding_coverage(
        funding_records, funding_start_ms, funding_end_ms
    )

    base_cost = 2 * (bt.TAKER_FEE_PER_SIDE + bt.SLIPPAGE_PER_SIDE) * 100.0
    folds_by_strategy = {name: [] for name in STRATEGIES}

    for fold_index in range(candidates.baseline_lab.FOLD_COUNT):
        start = first_test + fold_index * width
        end = len(candles) if fold_index == candidates.baseline_lab.FOLD_COUNT - 1 else min(
            len(candles), start + width
        )
        if end - start < candidates.baseline_lab.MIN_TEST_WINDOW_CANDLES:
            raise RuntimeError(f"Fold {fold_index + 1} is too short.")

        for strategy in STRATEGIES:
            scenarios = []
            for multiplier in COST_MULTIPLIERS:
                round_trip_cost = base_cost * multiplier
                report = run_candidate_with_history(
                    candles, minutes, start, end, round_trip_cost, strategy
                )
                adjusted = funding.funding_adjusted_trade_metrics(
                    report, funding_records
                )
                adjusted.pop("trade_details", None)
                scenarios.append({
                    "multiplier": multiplier,
                    "round_trip_cost_pct": round(round_trip_cost, 6),
                    "without_funding": compact_backtest(report),
                    "with_funding": adjusted,
                })

            stressed = scenarios[-1]["with_funding"]
            pf = stressed["profit_factor"]
            positive = (
                stressed["trades"] >= candidates.baseline_lab.MIN_TEST_TRADES
                and stressed["compounded_return_pct"] > 0
                and isinstance(pf, (int, float))
                and pf > 1.0
            )
            folds_by_strategy[strategy].append({
                "fold": fold_index + 1,
                "test_candles": end - start,
                "test_start_utc": datetime.fromtimestamp(
                    candles[start]["timestamp"] / 1000, timezone.utc
                ).isoformat(),
                "test_end_utc": datetime.fromtimestamp(
                    candles[end - 1]["timestamp"] / 1000, timezone.utc
                ).isoformat(),
                "base_cost": scenarios[0],
                "stress_1_5x_cost": scenarios[1],
                "stress_2x_cost": scenarios[2],
                "qualifies_after_funding_at_2x_cost": positive,
            })

    summaries = {}
    for strategy, folds in folds_by_strategy.items():
        qualifying = sum(
            item["qualifies_after_funding_at_2x_cost"] for item in folds
        )
        enough_trades_each_fold = len(folds) == candidates.baseline_lab.FOLD_COUNT and all(
            item["stress_2x_cost"]["with_funding"]["trades"]
            >= candidates.baseline_lab.MIN_TEST_TRADES
            for item in folds
        )
        summaries[strategy] = {
            "evaluated_folds": len(folds),
            "qualifying_folds_after_funding_at_2x_cost": qualifying,
            "total_trades_at_2x_cost": sum(
                item["stress_2x_cost"]["with_funding"]["trades"]
                for item in folds
            ),
            "mean_fold_return_without_funding_base_cost_pct": round(
                statistics.mean(
                    item["base_cost"]["without_funding"]["compounded_return_pct"]
                    for item in folds
                ), 6
            ) if folds else None,
            "mean_fold_return_with_funding_base_cost_pct": round(
                statistics.mean(
                    item["base_cost"]["with_funding"]["compounded_return_pct"]
                    for item in folds
                ), 6
            ) if folds else None,
            "mean_fold_return_without_funding_2x_cost_pct": round(
                statistics.mean(
                    item["stress_2x_cost"]["without_funding"]["compounded_return_pct"]
                    for item in folds
                ), 6
            ) if folds else None,
            "mean_fold_return_with_funding_2x_cost_pct": round(
                statistics.mean(
                    item["stress_2x_cost"]["with_funding"]["compounded_return_pct"]
                    for item in folds
                ), 6
            ) if folds else None,
            "funding_cost_sum_pct_points_at_2x_cost": round(sum(
                item["stress_2x_cost"]["with_funding"]["funding_cost_sum_pct_points"]
                for item in folds
            ), 8),
            "funding_events_at_2x_cost": sum(
                item["stress_2x_cost"]["with_funding"]["funding_events"]
                for item in folds
            ),
            "candidate_for_forward_paper": (
                enough_trades_each_fold
                and qualifying >= candidates.baseline_lab.MIN_POSITIVE_FOLDS
            ),
            "folds": folds,
        }

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles_total": len(candles),
        "candle_quality": candle_quality,
        "funding_coverage": coverage,
        "funding_records_available": len(funding_records),
        "oos_funding_start_ms": funding_start_ms,
        "oos_funding_end_ms": funding_end_ms,
        "strategies": summaries,
        "trading_enabled": False,
    }


def main():
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    funding_start_request_ms = now_ms - FUNDING_LOOKBACK_DAYS * DAY_MS
    funding_cache = {}
    funding_errors = {}
    results = []
    skipped = []

    for symbol in lab.SYMBOLS:
        print(f"Loading public funding history: {symbol}", flush=True)
        try:
            fetched = funding.fetch_funding_history(
                symbol, funding_start_request_ms, now_ms
            )
            funding_cache[symbol] = fetched["records"]
            funding_errors.pop(symbol, None)
            print(json.dumps({
                "symbol": symbol,
                "funding_records": len(fetched["records"]),
                "pages_fetched": fetched["pages_fetched"],
                "reached_start_boundary": fetched["reached_start_boundary"],
            }), flush=True)
        except Exception as exc:
            funding_errors[symbol] = f"{type(exc).__name__}: {exc}"
            print(f"FUNDING DATA FAILED {symbol}: {funding_errors[symbol]}", flush=True)
            continue

        for timeframe, minutes in lab.TIMEFRAMES:
            label = f"{symbol} {timeframe}"
            print(f"Funding-aware walk-forward: {label}", flush=True)
            try:
                result = evaluate_market(
                    symbol, timeframe, minutes, funding_cache[symbol], None
                )
                results.append(result)
                print(json.dumps({
                    "market": label,
                    "strategies": {
                        name: {
                            key: value for key, value in summary.items()
                            if key != "folds"
                        }
                        for name, summary in result["strategies"].items()
                    },
                }, ensure_ascii=False), flush=True)
            except Exception as exc:
                skipped.append({
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                print(f"SKIPPED {label}: {type(exc).__name__}: {exc}", flush=True)

    aggregate = {}
    for strategy in STRATEGIES:
        market_summaries = [
            item["strategies"][strategy]
            for item in results
            if strategy in item["strategies"]
        ]
        aggregate[strategy] = {
            "market_timeframes_completed": len(market_summaries),
            "mean_market_fold_return_without_funding_base_cost_pct": round(
                statistics.mean(
                    item["mean_fold_return_without_funding_base_cost_pct"]
                    for item in market_summaries
                ), 6
            ) if market_summaries else None,
            "mean_market_fold_return_with_funding_base_cost_pct": round(
                statistics.mean(
                    item["mean_fold_return_with_funding_base_cost_pct"]
                    for item in market_summaries
                ), 6
            ) if market_summaries else None,
            "mean_market_fold_return_without_funding_2x_cost_pct": round(
                statistics.mean(
                    item["mean_fold_return_without_funding_2x_cost_pct"]
                    for item in market_summaries
                ), 6
            ) if market_summaries else None,
            "mean_market_fold_return_with_funding_2x_cost_pct": round(
                statistics.mean(
                    item["mean_fold_return_with_funding_2x_cost_pct"]
                    for item in market_summaries
                ), 6
            ) if market_summaries else None,
            "market_timeframes_qualifying_after_funding": sum(
                item["candidate_for_forward_paper"] for item in market_summaries
            ),
        }

    report = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": (
            "Funding-aware walk-forward re-evaluation of six fixed per-symbol "
            "strategies using Bitget's settled historical funding rates."
        ),
        "symbols_requested": list(lab.SYMBOLS),
        "timeframes_requested": [item[0] for item in lab.TIMEFRAMES],
        "markets_tested": len(results),
        "markets_expected": len(lab.SYMBOLS) * len(lab.TIMEFRAMES),
        "skipped_markets": skipped,
        "funding_data_failures": funding_errors,
        "funding_source": (
            "Public Bitget API v2 GET /api/v2/mix/market/history-fund-rate; "
            "USDT-FUTURES; fundingRate and fundingTime fields."
        ),
        "funding_request_lookback_days": FUNDING_LOOKBACK_DAYS,
        "cost_scenarios": [
            {
                "multiplier": multiplier,
                "round_trip_fee_and_slippage_pct": round(
                    2 * (bt.TAKER_FEE_PER_SIDE + bt.SLIPPAGE_PER_SIDE) * 100.0
                    * multiplier, 6
                ),
                "funding": "actual historical settlement rates, applied separately",
            }
            for multiplier in COST_MULTIPLIERS
        ],
        "strategies": list(STRATEGIES),
        "aggregate_summary": aggregate,
        "results": results,
        "candidate_count": sum(
            summary["candidate_for_forward_paper"]
            for item in results
            for summary in item["strategies"].values()
        ),
        "methodology": {
            "same_candles_folds_entries_exits_and_fee_slippage_scenarios": True,
            "initial_history_fraction": candidates.baseline_lab.INITIAL_HISTORY_FRACTION,
            "oos_fold_count": candidates.baseline_lab.FOLD_COUNT,
            "entry": "next candle open after close-confirmed signal",
            "exits": "shared simulator: 1.5 ATR stop, 2 ATR target, four-hour maximum hold",
            "funding_sign": "positive rate: longs pay, shorts receive; negative rate reverses cash flow",
            "settlement_window": "entry_timestamp < funding_timestamp <= conservative_exit_timestamp",
            "intrabar_exit_policy": (
                "For intrabar stop/target and close-based exits, funding exposure is "
                "conservatively extended to the exit candle close; open-executed "
                "signal/gap exits use the exact candle-open timestamp."
            ),
            "funding_is_not_used_for_signals": True,
            "parameters_optimized_on_test_data": False,
            "trading_enabled": False,
        },
        "warnings": [
            "Funding is applied from actual historical settlement records; estimated taker fees and slippage remain illustrative.",
            "Funding history coverage is validated per symbol/timeframe; markets with sparse or truncated records are skipped rather than treated as zero funding.",
            "Funding-adjusted drawdown is a realized trade-sequence measure. The original simulator mark-to-market drawdown is also reported before funding; this script does not reconstruct funding cash flows at every intrabar mark.",
            "The exit-candle-close funding bound is conservative for intrabar stop/target exits and may slightly overcharge positions that exited earlier within that candle.",
            "OHLC data cannot reconstruct intrabar path; the shared simulator assumes stop first if stop and target are both touched.",
            "The same rolling 90-day candle window has been used in previous hypotheses; multiple-testing bias remains material and fresh forward evidence is required.",
            "Public market data only. No API keys, orders, automatic promotion, or live trading.",
        ],
        "trading_enabled": False,
    }
    OUTPUT.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print("===== BITGET FUNDING-AWARE CROSS-ASSET RESEARCH (READ-ONLY) =====", flush=True)
    print(json.dumps({
        "checked_at_utc": report["checked_at_utc"],
        "markets_tested": report["markets_tested"],
        "markets_expected": report["markets_expected"],
        "skipped_markets": skipped,
        "funding_data_failures": funding_errors,
        "aggregate_summary": aggregate,
        "candidate_count": report["candidate_count"],
        "trading_enabled": False,
    }, indent=2, ensure_ascii=False), flush=True)

    if not results:
        raise RuntimeError("No market/timeframe completed with validated funding history.")


if __name__ == "__main__":
    main()
