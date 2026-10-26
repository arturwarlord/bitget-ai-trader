"""Re-evaluate shortlisted market strategies under higher fee/slippage assumptions.

Read-only research. This script does not place orders.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import backtest as bt
import market_universe_lab as lab

INPUT = Path("market-universe-report.json")
OUTPUT = Path("cost-stress-report.json")
COST_MULTIPLIERS = (1.0, 1.5, 2.0)
MAX_SPLIT_DRIFT_TIMEFRAMES = 3


def compact_metrics(report):
    return {
        "trades": report["trades"],
        "win_rate_pct": report["win_rate_pct"],
        "profit_factor": report["profit_factor"],
        "compounded_return_pct": report["compounded_unleveraged_return_pct"],
        "max_drawdown_pct": report["max_drawdown_pct"],
    }


def cost_scenarios(base_cost_pct):
    return [
        {"multiplier": multiplier, "round_trip_cost_pct": round(base_cost_pct * multiplier, 6)}
        for multiplier in COST_MULTIPLIERS
    ]


def main():
    source = json.loads(INPUT.read_text(encoding="utf-8"))
    results = []
    skipped = []
    base_cost = float(source["cost_assumptions"]["round_trip_pct"])

    for market in source["markets"]:
        symbol = market["symbol"]
        timeframe = market["timeframe"]
        minutes = dict(lab.TIMEFRAMES)[timeframe]
        bt.SYMBOL = symbol
        bt.TIMEFRAME = timeframe
        bt.TIMEFRAME_MS = minutes * 60 * 1000
        bt.HISTORY_DAYS = lab.HISTORY_DAYS
        bt.MAX_PAGES = 55

        try:
            candles = bt.get_candles()
            lab.validate_candles(candles, bt.TIMEFRAME_MS)
            split = int(len(candles) * lab.DEVELOPMENT_FRACTION)
            if split <= 0 or split >= len(candles):
                raise RuntimeError("Invalid chronological split.")
            expected_start = datetime.fromisoformat(
                market["holdout_start_utc"].replace("Z", "+00:00")
            ).timestamp() * 1000
            actual_start = candles[split]["timestamp"]
            drift = abs(actual_start - expected_start) / bt.TIMEFRAME_MS
            if drift > MAX_SPLIT_DRIFT_TIMEFRAMES:
                raise RuntimeError(
                    f"Holdout boundary shifted by {drift:.1f} candles; refusing a non-comparable cost test."
                )

            for row in market["holdout_results"]:
                config = row["config"]
                scenarios = []
                for scenario in cost_scenarios(base_cost):
                    lab.apply_config(config)
                    bt.ROUND_TRIP_COST_PCT = scenario["round_trip_cost_pct"]
                    report = bt.run_backtest(
                        candles, timeframe_minutes=minutes,
                        evaluation_start_index=split,
                    )
                    scenarios.append({
                        **scenario,
                        "metrics": compact_metrics(report),
                    })
                base = scenarios[0]["metrics"]
                stress = scenarios[-1]["metrics"]
                results.append({
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "config": config,
                    "baseline_oos": base,
                    "stress_1_5x": scenarios[1]["metrics"],
                    "stress_2x": stress,
                    "positive_after_2x_cost": (
                        stress["compounded_return_pct"] > 0
                        and isinstance(stress["profit_factor"], (int, float))
                        and stress["profit_factor"] > 1
                    ),
                    "scenarios": scenarios,
                })
        except Exception as exc:
            skipped.append({
                "symbol": symbol, "timeframe": timeframe,
                "error": f"{type(exc).__name__}: {exc}",
            })

    bt.ROUND_TRIP_COST_PCT = base_cost
    results.sort(
        key=lambda item: (
            item["positive_after_2x_cost"],
            item["stress_2x"]["profit_factor"]
                if isinstance(item["stress_2x"]["profit_factor"], (int, float)) else -1,
            item["stress_2x"]["compounded_return_pct"],
        ),
        reverse=True,
    )
    output = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Sensitivity analysis for estimated fees and slippage; not a profitability guarantee.",
        "cost_multipliers": list(COST_MULTIPLIERS),
        "markets_tested": len(source["markets"]),
        "candidate_configs_tested": len(results),
        "skipped_markets": skipped,
        "candidates_positive_after_2x_cost": sum(x["positive_after_2x_cost"] for x in results),
        "results": results,
        "warnings": [
            "Costs are stress assumptions, not exchange-specific quotes.",
            "Funding is excluded and real fills may differ from OHLC assumptions.",
            "This report re-tests the existing holdout shortlist; it is not an independent untouched test set.",
            "No orders are placed; trading_enabled remains false.",
        ],
        "trading_enabled": False,
    }
    OUTPUT.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("===== BITGET COST STRESS TEST (READ-ONLY) =====")
    print(json.dumps({
        "candidate_configs_tested": output["candidate_configs_tested"],
        "candidates_positive_after_2x_cost": output["candidates_positive_after_2x_cost"],
        "skipped_markets": skipped,
        "top_10": [
            {k: row[k] for k in (
                "symbol", "timeframe", "config", "baseline_oos",
                "stress_1_5x", "stress_2x", "positive_after_2x_cost"
            )}
            for row in results[:10]
        ],
        "trading_enabled": False,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
