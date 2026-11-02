"""Cross-sectional 7-day relative-momentum research. Read-only; no orders."""

import json
import statistics
from datetime import datetime, timezone

import backtest as bt
import market_universe_lab as universe

OUTPUT = "cross-sectional-momentum-report.json"
DAY_MS = 24 * 60 * 60 * 1000
LOOKBACK_DAYS = 7
HOLD_DAYS = 1
TOP_BOTTOM_COUNT = 2
INITIAL_HISTORY_FRACTION = 0.50
FOLD_COUNT = 4
MIN_DAYS_PER_FOLD = 5
WEIGHT_PER_LEG = 0.25
COST_MULTIPLIERS = (1.0, 1.5, 2.0)


def build_daily_observations(
    markets,
    timeframe_ms,
    lookback_days=LOOKBACK_DAYS,
    hold_days=HOLD_DAYS,
    top_bottom_count=TOP_BOTTOM_COUNT,
):
    """Rank only completed bars; enter next open and exit one day later."""
    if not isinstance(timeframe_ms, int) or timeframe_ms <= 0:
        raise ValueError("timeframe_ms must be a positive integer")
    if not isinstance(lookback_days, int) or lookback_days < 1:
        raise ValueError("lookback_days must be a positive integer")
    if not isinstance(hold_days, int) or hold_days < 1:
        raise ValueError("hold_days must be a positive integer")
    if not isinstance(top_bottom_count, int) or top_bottom_count < 1:
        raise ValueError("top_bottom_count must be a positive integer")

    symbols = sorted(markets)
    if len(symbols) < 2 * top_bottom_count:
        raise ValueError("universe must contain at least twice top_bottom_count symbols")

    maps = {symbol: markets[symbol] for symbol in symbols}
    common_timestamps = set.intersection(*(set(items) for items in maps.values()))
    signal_times = sorted(
        timestamp for timestamp in common_timestamps
        if (timestamp + timeframe_ms) % DAY_MS == 0
    )

    observations = []
    skipped = 0
    lookback_ms = lookback_days * DAY_MS
    hold_ms = hold_days * DAY_MS

    for signal_timestamp in signal_times:
        lookback_timestamp = signal_timestamp - lookback_ms
        entry_timestamp = signal_timestamp + timeframe_ms
        exit_timestamp = entry_timestamp + hold_ms
        required = (lookback_timestamp, signal_timestamp, entry_timestamp, exit_timestamp)

        if any(
            timestamp not in maps[symbol]
            for symbol in symbols
            for timestamp in required
        ):
            skipped += 1
            continue

        momentum = {}
        entry_prices = {}
        exit_prices = {}
        valid = True
        for symbol in symbols:
            prior_close = float(maps[symbol][lookback_timestamp]["close"])
            signal_close = float(maps[symbol][signal_timestamp]["close"])
            entry_open = float(maps[symbol][entry_timestamp]["open"])
            exit_open = float(maps[symbol][exit_timestamp]["open"])
            if min(prior_close, signal_close, entry_open, exit_open) <= 0:
                valid = False
                break
            momentum[symbol] = signal_close / prior_close - 1.0
            entry_prices[symbol] = entry_open
            exit_prices[symbol] = exit_open

        if not valid:
            skipped += 1
            continue

        ranked = sorted(symbols, key=lambda symbol: (momentum[symbol], symbol))
        short_symbols = ranked[:top_bottom_count]
        long_symbols = ranked[-top_bottom_count:]
        observations.append({
            "signal_timestamp": signal_timestamp,
            "entry_timestamp": entry_timestamp,
            "exit_timestamp": exit_timestamp,
            "long_symbols": long_symbols,
            "short_symbols": short_symbols,
            "momentum": momentum,
            "entry_prices": entry_prices,
            "exit_prices": exit_prices,
        })

    return observations, {
        "candidate_rebalance_dates": len(signal_times),
        "skipped_rebalance_dates": skipped,
        "valid_rebalance_dates": len(observations),
    }


def portfolio_return_pct(observation, round_trip_cost_pct):
    """100% gross exposure: 50% long and 50% short, equal-weighted."""
    gross_return_pct = 0.0
    for symbol in observation["long_symbols"]:
        gross_return_pct += WEIGHT_PER_LEG * (
            observation["exit_prices"][symbol] / observation["entry_prices"][symbol] - 1.0
        ) * 100.0
    for symbol in observation["short_symbols"]:
        gross_return_pct += WEIGHT_PER_LEG * (
            1.0 - observation["exit_prices"][symbol] / observation["entry_prices"][symbol]
        ) * 100.0
    # Cost is charged on the full 100% gross portfolio notional per daily round trip.
    return gross_return_pct - round_trip_cost_pct


def portfolio_metrics(observations, round_trip_cost_pct):
    daily_returns = [
        portfolio_return_pct(observation, round_trip_cost_pct)
        for observation in observations
    ]
    positive_sum = sum(value for value in daily_returns if value > 0)
    negative_sum = -sum(value for value in daily_returns if value < 0)

    equity = 1.0
    peak = 1.0
    max_drawdown_pct = 0.0
    for value in daily_returns:
        equity *= max(0.0, 1.0 + value / 100.0)
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown_pct = max(
                max_drawdown_pct, (peak - equity) / peak * 100.0
            )

    return {
        "rebalance_days": len(daily_returns),
        "position_legs": len(daily_returns) * 2 * TOP_BOTTOM_COUNT,
        "win_rate_pct": round(
            sum(value > 0 for value in daily_returns) / len(daily_returns) * 100.0, 2
        ) if daily_returns else 0.0,
        "profit_factor": round(positive_sum / negative_sum, 4) if negative_sum else None,
        "mean_daily_return_pct": round(
            statistics.mean(daily_returns), 6
        ) if daily_returns else 0.0,
        "compounded_return_pct": round((equity - 1.0) * 100.0, 4),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
    }


def chronological_folds(observation_count):
    if observation_count < 2:
        raise ValueError("at least two observations are required")
    first_test = int(observation_count * INITIAL_HISTORY_FRACTION)
    remaining = observation_count - first_test
    width = remaining // FOLD_COUNT
    if width < MIN_DAYS_PER_FOLD:
        raise RuntimeError(
            f"insufficient OOS rebalance days: {remaining} total, "
            f"{width} per fold; need at least {MIN_DAYS_PER_FOLD}"
        )

    folds = []
    for index in range(FOLD_COUNT):
        start = first_test + index * width
        end = observation_count if index == FOLD_COUNT - 1 else start + width
        if end - start < MIN_DAYS_PER_FOLD:
            raise RuntimeError(f"fold {index + 1} has too few rebalance days")
        folds.append((start, end))
    return folds


def evaluate_observations(observations, base_round_trip_cost_pct):
    ranges = chronological_folds(len(observations))
    folds = []
    for index, (start, end) in enumerate(ranges, start=1):
        selected = observations[start:end]
        scenarios = []
        for multiplier in COST_MULTIPLIERS:
            cost = base_round_trip_cost_pct * multiplier
            scenarios.append({
                "multiplier": multiplier,
                "round_trip_cost_pct": round(cost, 6),
                "metrics": portfolio_metrics(selected, cost),
            })
        stressed = scenarios[-1]["metrics"]
        qualifies = (
            stressed["rebalance_days"] >= MIN_DAYS_PER_FOLD
            and stressed["compounded_return_pct"] > 0
            and isinstance(stressed["profit_factor"], (int, float))
            and stressed["profit_factor"] > 1.0
        )
        folds.append({
            "fold": index,
            "rebalance_days": end - start,
            "start_signal_utc": datetime.fromtimestamp(
                selected[0]["signal_timestamp"] / 1000, timezone.utc
            ).isoformat(),
            "end_signal_utc": datetime.fromtimestamp(
                selected[-1]["signal_timestamp"] / 1000, timezone.utc
            ).isoformat(),
            "base_cost": scenarios[0]["metrics"],
            "stress_1_5x": scenarios[1]["metrics"],
            "stress_2x": stressed,
            "qualifies_at_2x_cost": qualifies,
        })

    positive_folds = sum(item["qualifies_at_2x_cost"] for item in folds)
    return {
        "initial_history_observations": ranges[0][0],
        "oos_folds": folds,
        "mean_fold_return_at_base_cost_pct": round(
            statistics.mean(item["base_cost"]["compounded_return_pct"] for item in folds), 4
        ),
        "mean_fold_return_at_1_5x_cost_pct": round(
            statistics.mean(item["stress_1_5x"]["compounded_return_pct"] for item in folds), 4
        ),
        "mean_fold_return_at_2x_cost_pct": round(
            statistics.mean(item["stress_2x"]["compounded_return_pct"] for item in folds), 4
        ),
        "qualifying_folds_at_2x_cost": positive_folds,
        "candidate_for_forward_paper": universe.qualifies_for_portfolio_forward_paper(
            folds, min_positive_folds=3
        ),
    }


def load_timeframe_markets(timeframe, minutes):
    markets = {}
    quality = {}
    bt.TIMEFRAME = timeframe
    bt.TIMEFRAME_MS = minutes * 60 * 1000
    bt.HISTORY_DAYS = universe.HISTORY_DAYS
    bt.MAX_PAGES = 55

    for symbol in universe.SYMBOLS:
        bt.SYMBOL = symbol
        candles = bt.get_candles()
        quality[symbol] = universe.validate_candles(candles, bt.TIMEFRAME_MS)
        markets[symbol] = {
            int(candle["timestamp"]): candle for candle in candles
        }
    return markets, quality


def main():
    base_cost = 2 * (bt.TAKER_FEE_PER_SIDE + bt.SLIPPAGE_PER_SIDE) * 100.0
    timeframe_results = []
    failures = []

    for timeframe, minutes in universe.TIMEFRAMES:
        label = f"{timeframe}"
        print(f"Cross-sectional momentum research: {label}", flush=True)
        try:
            markets, quality = load_timeframe_markets(timeframe, minutes)
            observations, alignment = build_daily_observations(
                markets, minutes * 60 * 1000
            )
            evaluated = evaluate_observations(observations, base_cost)
            timeframe_results.append({
                "timeframe": timeframe,
                "minutes": minutes,
                "symbols": list(universe.SYMBOLS),
                "markets_loaded": len(markets),
                "market_data_quality": quality,
                "alignment": alignment,
                "lookback_days": LOOKBACK_DAYS,
                "holding_days": HOLD_DAYS,
                "top_bottom_count": TOP_BOTTOM_COUNT,
                "gross_exposure_pct": 100.0,
                "net_exposure_pct": 0.0,
                "weight_per_position_pct": WEIGHT_PER_LEG * 100.0,
                **evaluated,
                "trading_enabled": False,
            })
        except Exception as exc:
            failures.append({
                "timeframe": timeframe,
                "error": f"{type(exc).__name__}: {exc}",
            })
            print(f"FAILED {label}: {type(exc).__name__}: {exc}", flush=True)

    if not timeframe_results:
        raise RuntimeError("No timeframe completed; see failures.")

    report = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": (
            "Predeclared cross-sectional 7-day relative-momentum portfolio: "
            "long top two and short bottom two of ten USDT futures symbols, "
            "rebalance daily, hold one day."
        ),
        "exchange": "Bitget",
        "market_type": "USDT-FUTURES",
        "symbols": list(universe.SYMBOLS),
        "timeframes": [item["timeframe"] for item in timeframe_results],
        "history_days_requested": universe.HISTORY_DAYS,
        "timeframes_completed": len(timeframe_results),
        "failures": failures,
        "strategy_parameters": {
            "lookback_days": LOOKBACK_DAYS,
            "holding_days": HOLD_DAYS,
            "long_positions": TOP_BOTTOM_COUNT,
            "short_positions": TOP_BOTTOM_COUNT,
            "gross_exposure_pct": 100.0,
            "net_exposure_pct": 0.0,
            "rebalance": "once per UTC day after the final completed candle",
            "entry": "next candle open",
            "exit": "open exactly one day after entry",
            "position_weight_pct": WEIGHT_PER_LEG * 100.0,
            "parameters_optimized_on_test_data": False,
        },
        "cost_scenarios": [
            {
                "multiplier": multiplier,
                "round_trip_cost_pct": round(base_cost * multiplier, 6),
            }
            for multiplier in COST_MULTIPLIERS
        ],
        "initial_history_fraction": INITIAL_HISTORY_FRACTION,
        "fold_count": FOLD_COUNT,
        "minimum_rebalance_days_per_fold": MIN_DAYS_PER_FOLD,
        "results": timeframe_results,
        "candidate_count": sum(
            item["candidate_for_forward_paper"] for item in timeframe_results
        ),
        "warnings": [
            "This is a portfolio-level long/short result and is not directly comparable to per-symbol strategy returns.",
            "All rankings use only completed candles; entries occur at the next candle open.",
            "The same ten-symbol universe and fixed 7-day lookback / 1-day holding rule are used on both timeframes.",
            "Fees and slippage are illustrative; funding is excluded even though daily futures holds may cross funding timestamps.",
            "The model assumes equal 25% gross-notional weights per leg, no leverage, and full daily round-trip turnover.",
            "The same 90-day history has been used in earlier research; multiple-testing bias remains and a fresh forward period is required.",
            "No exchange orders are placed and no candidate is promoted automatically.",
        ],
        "trading_enabled": False,
    }
    with open(OUTPUT, "w", encoding="utf-8") as output:
        json.dump(report, output, indent=2, ensure_ascii=False)

    print("===== BITGET CROSS-SECTIONAL MOMENTUM (READ-ONLY) =====", flush=True)
    print(json.dumps({
        "checked_at_utc": report["checked_at_utc"],
        "timeframes_completed": report["timeframes_completed"],
        "failures": failures,
        "candidate_count": report["candidate_count"],
        "summary": [
            {
                "timeframe": item["timeframe"],
                "valid_rebalance_dates": item["alignment"]["valid_rebalance_dates"],
                "mean_fold_return_at_base_cost_pct": item["mean_fold_return_at_base_cost_pct"],
                "mean_fold_return_at_2x_cost_pct": item["mean_fold_return_at_2x_cost_pct"],
                "qualifying_folds_at_2x_cost": item["qualifying_folds_at_2x_cost"],
                "candidate_for_forward_paper": item["candidate_for_forward_paper"],
            }
            for item in timeframe_results
        ],
        "trading_enabled": False,
    }, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
