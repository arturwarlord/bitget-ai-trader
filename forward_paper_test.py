import json
from datetime import datetime, timezone
from pathlib import Path

import pullback_experiment as exp

STATE_PATH = Path("data/forward_paper_state.json")
REPORT_PATH = Path("forward-paper-report.json")
ALLOWED_REGIMES = {"weak", "strong"}
STOP_ATR_MULT = 1.5
TARGET_R = 1.5


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return None


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def close_position(state, price, timestamp, reason):
    pos = state["position"]
    gross_pct = pos["side"] * (price / pos["entry_price"] - 1) * 100
    net_pct = gross_pct - exp.ROUND_TRIP_COST_PCT
    state["equity"] *= max(0.0, 1 + net_pct / 100)
    state["trades"].append({
        "side": "SHORT",
        "entry_timestamp": pos["entry_timestamp"],
        "exit_timestamp": timestamp,
        "entry_price": round(pos["entry_price"], 8),
        "exit_price": round(price, 8),
        "net_return_pct": round(net_pct, 5),
        "exit_reason": reason,
        "bars_held": pos["bars_held"],
        "trend_regime": pos["trend_regime"],
    })
    state["position"] = None


def trade_stats(trades):
    returns = [t["net_return_pct"] for t in trades]
    gains = sum(x for x in returns if x > 0)
    losses = -sum(x for x in returns if x < 0)
    capital = 1.0
    peak = 1.0
    max_dd = 0.0
    for x in returns:
        capital *= max(0.0, 1 + x / 100)
        peak = max(peak, capital)
        if peak:
            max_dd = max(max_dd, (peak - capital) / peak * 100)
    return {
        "closed_trades": len(returns),
        "win_rate_pct": round(sum(x > 0 for x in returns) / len(returns) * 100, 2) if returns else 0.0,
        "profit_factor": round(gains / losses, 4) if losses else ("infinite" if gains else None),
        "compounded_unleveraged_return_pct": round((capital - 1) * 100, 4),
        "trade_to_trade_max_drawdown_pct": round(max_dd, 4),
        "wins": sum(x > 0 for x in returns),
        "losses": sum(x < 0 for x in returns),
    }


def main():
    candles = exp.get_candles()
    signals, atr, trend, slow = exp.build_signals(candles)
    volatility = [((atr[i] / candles[i]["close"]) * 100) if atr[i] is not None and candles[i]["close"] else None for i in range(len(candles))]
    split = int(len(candles) * 0.70)
    cutoff_values = [volatility[i] for i in range(split)]
    vol_cutoffs = (exp.percentile(cutoff_values, 1 / 3), exp.percentile(cutoff_values, 2 / 3))
    state = load_state()

    if state is None:
        # First run deliberately starts tracking from now; historical candles are indicators only.
        state = {
            "created_at_utc": now_utc(),
            "last_processed_timestamp": candles[-1]["timestamp"],
            "position": None,
            "pending": None,
            "trades": [],
            "equity": 1.0,
            "strategy": "SHORT-only EMA20 pullback, EMA50<EMA200, recent RSI>=58, weak/strong trend only",
            "stop_atr_multiplier": STOP_ATR_MULT,
            "target_r_multiple": TARGET_R,
            "allowed_trend_regimes": sorted(ALLOWED_REGIMES),
            "estimated_round_trip_cost_pct": exp.ROUND_TRIP_COST_PCT,
        }
        save_json(STATE_PATH, state)
        report = {
            "checked_at_utc": now_utc(),
            "mode": "forward paper validation; initialization only",
            "message": "Tracking initialized. No historical trades were counted; new closed candles will be evaluated on later scheduled runs.",
            "latest_closed_candle_utc": datetime.fromtimestamp(candles[-1]["timestamp"] / 1000, timezone.utc).isoformat(),
            "trading_enabled": False,
            "statistics": trade_stats(state["trades"]),
        }
        save_json(REPORT_PATH, report)
        print(json.dumps(report, indent=2))
        return

    timestamp_to_index = {c["timestamp"]: i for i, c in enumerate(candles)}
    last_ts = int(state["last_processed_timestamp"])
    if last_ts not in timestamp_to_index:
        raise RuntimeError("Saved forward-test timestamp is outside downloaded history. Refusing to reset or silently skip data.")
    start_index = timestamp_to_index[last_ts] + 1
    processed = 0

    for i in range(start_index, len(candles)):
        c = candles[i]
        # Enter at the next candle open after a closed-candle signal.
        if state["pending"] is not None:
            pending = state["pending"]
            if state["position"] is None:
                entry = c["open"]
                risk = STOP_ATR_MULT * pending["atr"]
                state["position"] = {
                    "side": -1,
                    "entry_price": entry,
                    "entry_timestamp": c["timestamp"],
                    "stop": entry + risk,
                    "target": entry - TARGET_R * risk,
                    "bars_held": 0,
                    "trend_regime": pending.get("trend_regime"),
                }
            state["pending"] = None

        pos = state["position"]
        if pos is not None:
            pos["bars_held"] += 1
            exit_price = reason = None
            if c["open"] >= pos["stop"]:
                exit_price, reason = c["open"], "stop_gap"
            elif c["open"] <= pos["target"]:
                exit_price, reason = c["open"], "target_gap"
            elif c["high"] >= pos["stop"]:
                exit_price, reason = pos["stop"], "stop_loss"
            elif c["low"] <= pos["target"]:
                exit_price, reason = pos["target"], "take_profit"
            if exit_price is not None:
                close_position(state, exit_price, c["timestamp"], reason)
            elif pos["bars_held"] >= exp.MAX_HOLD_CANDLES:
                close_position(state, c["close"], c["timestamp"], "max_holding_time")

        signal = signals[i]
        regime = exp.trend_regime_at(i, atr, trend, slow)
        if state["position"] is None and state["pending"] is None and signal == -1 and regime in ALLOWED_REGIMES and atr[i] is not None and atr[i] > 0:
            state["pending"] = {"atr": atr[i], "trend_regime": regime}
        state["last_processed_timestamp"] = c["timestamp"]
        processed += 1

    state["updated_at_utc"] = now_utc()
    save_json(STATE_PATH, state)
    stats = trade_stats(state["trades"])
    report = {
        "checked_at_utc": now_utc(),
        "mode": "forward paper validation; no orders placed",
        "symbol": exp.SYMBOL,
        "timeframe": exp.TIMEFRAME,
        "strategy": state["strategy"],
        "stop_atr_multiplier": STOP_ATR_MULT,
        "target_r_multiple": TARGET_R,
        "allowed_trend_regimes": sorted(ALLOWED_REGIMES),
        "new_closed_candles_processed": processed,
        "last_processed_candle_utc": datetime.fromtimestamp(state["last_processed_timestamp"] / 1000, timezone.utc).isoformat(),
        "open_position": state["position"],
        "pending_signal": state["pending"] is not None,
        "statistics": stats,
        "recent_trades": state["trades"][-10:],
        "warnings": [
            "This is forward paper tracking; historical candles are used only to calculate indicators, not counted as trades.",
            "Estimated fees/slippage are applied; funding is excluded.",
            "Results are exploratory and require a meaningful sample of forward trades.",
        ],
        "trading_enabled": False,
    }
    save_json(REPORT_PATH, report)
    print("\n===== BITGET FORWARD PAPER VALIDATION (READ-ONLY) =====")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print("\nSafety: public market data only; no trading orders are placed.")


if __name__ == "__main__":
    main()
