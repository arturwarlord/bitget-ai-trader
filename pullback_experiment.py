import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE_URL = "https://api.bitget.com"
SYMBOL = "BTCUSDT"
PRODUCT_TYPE = "USDT-FUTURES"
TIMEFRAME = "15m"
TIMEFRAME_MS = 15 * 60 * 1000
CANDLE_LIMIT = 200
HISTORY_DAYS = 180
MAX_PAGES = 500
TAKER_FEE_PER_SIDE = 0.0006
SLIPPAGE_PER_SIDE = 0.0002
ROUND_TRIP_COST_PCT = 2 * (TAKER_FEE_PER_SIDE + SLIPPAGE_PER_SIDE) * 100
EMA_FAST = 20
EMA_TREND = 50
EMA_SLOW = 200
RSI_PERIOD = 14
ATR_PERIOD = 14
RSI_LONG_MAX = 42
RSI_SHORT_MIN = 58
STOP_ATR_MULTIPLIERS = [1.5, 2.0]
TARGET_R_MULTIPLIERS = [1.5, 2.0, 2.5]
MAX_HOLD_CANDLES = 32
TRADING_ENABLED = False


def request_candles(end_time_ms=None):
    params = {"symbol": SYMBOL, "productType": PRODUCT_TYPE, "granularity": TIMEFRAME, "limit": str(CANDLE_LIMIT)}
    if end_time_ms is not None:
        params["endTime"] = str(end_time_ms)
    url = f"{BASE_URL}/api/v2/mix/market/history-candles?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "bitget-ai-trader-pullback-test/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("code") != "00000":
        raise RuntimeError(f"Bitget API error: {payload.get('msg', 'unknown error')}")
    return [{"timestamp": int(r[0]), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4])}
            for r in payload.get("data", []) if len(r) >= 5]


def get_candles():
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    earliest = now_ms - HISTORY_DAYS * 86400000
    by_timestamp = {}
    end_time_ms = now_ms
    for _ in range(MAX_PAGES):
        batch = request_candles(end_time_ms)
        if not batch:
            break
        for c in batch:
            if earliest <= c["timestamp"] and c["timestamp"] + TIMEFRAME_MS <= now_ms:
                by_timestamp[c["timestamp"]] = c
        oldest = min(c["timestamp"] for c in batch)
        if oldest <= earliest:
            break
        if oldest >= end_time_ms:
            raise RuntimeError("Bitget candle pagination did not move backwards.")
        end_time_ms = oldest - 1
        time.sleep(0.2)
    candles = [by_timestamp[k] for k in sorted(by_timestamp)]
    if len(candles) < 1000:
        raise RuntimeError(f"Only {len(candles)} closed candles collected; expected at least 1000.")
    return candles


def ema_series(values, period):
    out = [None] * len(values)
    if len(values) < period:
        return out
    current = sum(values[:period]) / period
    out[period - 1] = current
    alpha = 2 / (period + 1)
    for i in range(period, len(values)):
        current = values[i] * alpha + current * (1 - alpha)
        out[i] = current
    return out


def rsi_series(values, period):
    out = [None] * len(values)
    if len(values) <= period:
        return out
    gains, losses = [], []
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    out[period] = 100.0 if avg_loss == 0 and avg_gain > 0 else (50.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss))
    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[i] = 100.0 if avg_loss == 0 and avg_gain > 0 else (50.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss))
    return out


def atr_series(candles, period):
    out = [None] * len(candles)
    ranges = [None] * len(candles)
    for i in range(1, len(candles)):
        ranges[i] = max(candles[i]["high"] - candles[i]["low"],
                        abs(candles[i]["high"] - candles[i - 1]["close"]),
                        abs(candles[i]["low"] - candles[i - 1]["close"]))
    if len(candles) <= period:
        return out
    current = sum(ranges[1:period + 1]) / period
    out[period] = current
    for i in range(period + 1, len(candles)):
        current = (current * (period - 1) + ranges[i]) / period
        out[i] = current
    return out


def build_signals(candles):
    closes = [c["close"] for c in candles]
    fast = ema_series(closes, EMA_FAST)
    trend = ema_series(closes, EMA_TREND)
    slow = ema_series(closes, EMA_SLOW)
    rsi = rsi_series(closes, RSI_PERIOD)
    atr = atr_series(candles, ATR_PERIOD)
    signals = [None] * len(candles)
    for i in range(EMA_SLOW + 1, len(candles)):
        if any(x[i] is None for x in (fast, trend, slow, rsi, atr)) or atr[i] <= 0:
            continue
        # Trend-following pullback: trend alignment plus price crossing back through EMA20.
        crossed_up = closes[i] > fast[i] and closes[i - 1] <= fast[i - 1]
        crossed_down = closes[i] < fast[i] and closes[i - 1] >= fast[i - 1]
        # RSI confirms a recent pullback, while EMA20 recross confirms recovery.
        # Check the current candle and previous 4 closed candles rather than requiring
        # RSI to remain oversold/overbought on the exact recross candle.
        recent_rsi = [value for value in rsi[max(EMA_SLOW + 1, i - 4):i + 1] if value is not None]
        had_long_pullback = any(value <= RSI_LONG_MAX for value in recent_rsi)
        had_short_pullback = any(value >= RSI_SHORT_MIN for value in recent_rsi)
        if trend[i] > slow[i] and closes[i] > slow[i] and crossed_up and had_long_pullback:
            signals[i] = 1
        elif trend[i] < slow[i] and closes[i] < slow[i] and crossed_down and had_short_pullback:
            signals[i] = -1
    return signals, atr, trend, slow


def percentile(values, fraction):
    clean = sorted(v for v in values if v is not None)
    if not clean:
        return None
    index = (len(clean) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(clean) - 1)
    weight = index - lower
    return clean[lower] * (1 - weight) + clean[upper] * weight


def summarize_regime_trades(trades):
    def stats(items):
        returns = [t["net_return_pct"] for t in items]
        profits = sum(v for v in returns if v > 0)
        losses = -sum(v for v in returns if v < 0)
        capital = 1.0
        for value in returns:
            capital *= max(0.0, 1 + value / 100)
        return {
            "trades": len(items),
            "win_rate_pct": round(sum(v > 0 for v in returns) / len(returns) * 100, 2) if returns else 0,
            "profit_factor": round(profits / losses, 4) if losses else (None if not profits else "infinite"),
            "compounded_unleveraged_return_pct": round((capital - 1) * 100, 4),
            "average_net_return_pct": round(sum(returns) / len(returns), 4) if returns else 0,
        }

    report = {"by_side": {}, "by_trend_strength": {}, "by_volatility": {}}
    for key, group_key in (("by_side", "side"), ("by_trend_strength", "trend_regime"), ("by_volatility", "volatility_regime")):
        for group in sorted({t[group_key] for t in trades if t.get(group_key) is not None}):
            report[key][group] = stats([t for t in trades if t.get(group_key) == group])
    return report


def trend_regime_at(index, atr_values, trend_values, slow_values):
    atr_value = atr_values[index]
    trend_value = trend_values[index]
    slow_value = slow_values[index]
    if atr_value is None or atr_value <= 0 or trend_value is None or slow_value is None:
        return None
    gap = abs(trend_value - slow_value) / atr_value
    return "weak" if gap < 1.0 else ("moderate" if gap < 2.0 else "strong")


def simulate(candles, signals, atr_values, trend_values, slow_values, volatility_values, volatility_cutoffs, stop_mult, target_r, evaluation_start, direction="both", allowed_trend_regimes=None):
    start = max(evaluation_start, EMA_SLOW + 2)
    position = None
    pending = None
    trades = []
    equity = peak = 1.0
    max_dd = 0.0

    def close_position(price, timestamp, reason):
        nonlocal position, equity
        side = position["side"]
        gross = side * (price / position["entry_price"] - 1) * 100
        net = gross - ROUND_TRIP_COST_PCT
        equity *= max(0.0, 1 + net / 100)
        trades.append({"side": "LONG" if side == 1 else "SHORT", "net_return_pct": net,
                       "exit_reason": reason, "bars_held": position["bars_held"],
                       "trend_regime": position.get("trend_regime"),
                       "volatility_regime": position.get("volatility_regime")})
        position = None

    for i in range(start, len(candles)):
        c = candles[i]
        if pending is not None:
            side, signal_atr, signal_index = pending
            if position is None and signal_atr is not None and signal_atr > 0:
                entry = c["open"]
                risk = stop_mult * signal_atr
                reward = target_r * risk
                trend_regime = trend_regime_at(signal_index, atr_values, trend_values, slow_values)
                vol = volatility_values[signal_index]
                volatility_regime = "low" if vol is not None and volatility_cutoffs[0] is not None and vol <= volatility_cutoffs[0] else ("medium" if vol is not None and volatility_cutoffs[1] is not None and vol <= volatility_cutoffs[1] else ("high" if vol is not None else None))
                position = {"side": side, "entry_price": entry, "entry_timestamp": c["timestamp"],
                            "stop": entry - risk if side == 1 else entry + risk,
                            "target": entry + reward if side == 1 else entry - reward, "bars_held": 0,
                            "trend_regime": trend_regime, "volatility_regime": volatility_regime}
            pending = None
        if position is not None:
            position["bars_held"] += 1
            side, stop, target = position["side"], position["stop"], position["target"]
            exit_price = reason = None
            if side == 1:
                if c["open"] <= stop: exit_price, reason = c["open"], "stop_gap"
                elif c["open"] >= target: exit_price, reason = c["open"], "target_gap"
                elif c["low"] <= stop: exit_price, reason = stop, "stop_loss"
                elif c["high"] >= target: exit_price, reason = target, "take_profit"
            else:
                if c["open"] >= stop: exit_price, reason = c["open"], "stop_gap"
                elif c["open"] <= target: exit_price, reason = c["open"], "target_gap"
                elif c["high"] >= stop: exit_price, reason = stop, "stop_loss"
                elif c["low"] <= target: exit_price, reason = target, "take_profit"
            if exit_price is not None:
                close_position(exit_price, c["timestamp"], reason)
            elif position is not None and position["bars_held"] >= MAX_HOLD_CANDLES:
                close_position(c["close"], c["timestamp"], "max_holding_time")
        if position is not None:
            adverse = c["low"] if position["side"] == 1 else c["high"]
            marked = equity * max(0.0, 1 + position["side"] * (adverse / position["entry_price"] - 1))
        else:
            marked = equity
        peak = max(peak, marked)
        if peak > 0:
            max_dd = max(max_dd, (peak - marked) / peak * 100)
        signal = signals[i]
        direction_allowed = direction == "both" or (direction == "long_only" and signal == 1) or (direction == "short_only" and signal == -1)
        signal_regime = trend_regime_at(i, atr_values, trend_values, slow_values)
        regime_allowed = allowed_trend_regimes is None or signal_regime in allowed_trend_regimes
        if position is None and signal in (-1, 1) and direction_allowed and regime_allowed and atr_values[i] is not None:
            pending = (signal, atr_values[i], i)
    if position is not None:
        close_position(candles[-1]["close"], candles[-1]["timestamp"], "end_of_test")
    returns = [t["net_return_pct"] for t in trades]
    profits = sum(x for x in returns if x > 0)
    losses = -sum(x for x in returns if x < 0)
    capital = 1.0
    for x in returns:
        capital *= max(0.0, 1 + x / 100)
    return {"trades": len(trades),
            "win_rate_pct": round(sum(x > 0 for x in returns) / len(returns) * 100, 2) if returns else 0,
            "profit_factor": round(profits / losses, 4) if losses else (None if not profits else "infinite"),
            "net_sum_pct_points": round(sum(returns), 4),
            "compounded_unleveraged_return_pct": round((capital - 1) * 100, 4),
            "max_drawdown_pct": round(max_dd, 4),
            "long_trades": sum(t["side"] == "LONG" for t in trades),
            "short_trades": sum(t["side"] == "SHORT" for t in trades),
            "regime_breakdown": summarize_regime_trades(trades)}


def main():
    candles = get_candles()
    signals, atr, trend, slow = build_signals(candles)
    volatility = [((atr[i] / candles[i]["close"]) * 100) if atr[i] is not None and candles[i]["close"] else None for i in range(len(candles))]
    split = int(len(candles) * 0.70)
    development_volatility = [volatility[i] for i in range(split)]
    volatility_cutoffs = (percentile(development_volatility, 1 / 3), percentile(development_volatility, 2 / 3))
    results = []
    for direction in ("both", "long_only", "short_only"):
        for stop_mult in STOP_ATR_MULTIPLIERS:
            for target_r in TARGET_R_MULTIPLIERS:
                full = simulate(candles, signals, atr, trend, slow, volatility, volatility_cutoffs, stop_mult, target_r, 0, direction)
                oos = simulate(candles, signals, atr, trend, slow, volatility, volatility_cutoffs, stop_mult, target_r, split, direction)
                if direction != "short_only" or stop_mult != 1.5 or target_r != 1.5:
                    full.pop("regime_breakdown", None)
                    oos.pop("regime_breakdown", None)
                results.append({"direction": direction, "stop_atr_multiplier": stop_mult, "target_r_multiple": target_r,
                                "full_period_diagnostic": full,
                                "out_of_sample_holdout": {"split_candle_utc": datetime.fromtimestamp(candles[split]["timestamp"] / 1000, timezone.utc).isoformat(), **oos}})
    # Pre-specified diagnostic comparison: SHORT-only baseline vs excluding moderate trend.
    # This filter is exploratory because the prior report suggested regime differences;
    # it must be validated on new forward data before any use.
    regime_filter_comparison = {}
    for label, allowed in (("baseline_all_regimes", None), ("exclude_moderate_trend", {"weak", "strong"})):
        regime_filter_comparison[label] = {
            "allowed_trend_regimes": sorted(allowed) if allowed is not None else "all",
            "development": simulate(candles, signals, atr, trend, slow, volatility, volatility_cutoffs, 1.5, 1.5, 0, "short_only", allowed),
            "out_of_sample_holdout": simulate(candles, signals, atr, trend, slow, volatility, volatility_cutoffs, 1.5, 1.5, split, "short_only", allowed),
        }
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "exchange": "Bitget",
              "market": "USDT Futures", "symbol": SYMBOL, "timeframe": TIMEFRAME,
              "strategy": "15m trend pullback: EMA20 recross, EMA50/EMA200 alignment, recent RSI pullback, ATR stop/target; both/long-only/short-only comparison",
              "history_days_requested": HISTORY_DAYS, "candles_collected": len(candles),
              "estimated_round_trip_cost_pct": ROUND_TRIP_COST_PCT,
              "regime_analysis": {"baseline": "SHORT-only, stop 1.5x ATR, target 1.5R",
                                  "trend_strength_buckets": "EMA50-EMA200 distance divided by ATR: weak <1, moderate 1-2, strong >=2",
                                  "volatility_buckets": "ATR/close percentage split into thirds using development-period thresholds only",
                                  "development_volatility_tercile_cutoffs_pct": [round(x, 6) if x is not None else None for x in volatility_cutoffs]},
              "parameters": {"ema_fast": EMA_FAST, "ema_trend": EMA_TREND, "ema_slow": EMA_SLOW,
                             "rsi_period": RSI_PERIOD, "rsi_long_max": RSI_LONG_MAX, "rsi_short_min": RSI_SHORT_MIN,
                             "atr_period": ATR_PERIOD, "stop_atr_multipliers": STOP_ATR_MULTIPLIERS,
                             "target_r_multiples": TARGET_R_MULTIPLIERS, "max_hold_candles": MAX_HOLD_CANDLES,
                             "entry": "signal on closed candle; entry at next candle open",
                             "position_sizing": "unleveraged; no leverage applied"},
              "results": results,
              "regime_filter_comparison": regime_filter_comparison,
              "warnings": ["Read-only historical experiment; no orders are placed.",
                           "Fees and slippage are illustrative estimates; funding is excluded.",
                           "Stop is assumed first when stop and target are both touched in one candle.",
                           "Full-period results are diagnostic; OOS is a preliminary chronological holdout, not proof of future profitability."],
              "trading_enabled": TRADING_ENABLED}
    with open("pullback-report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print("\n===== BITGET 15M TREND PULLBACK EXPERIMENT (READ-ONLY) =====")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print("\nSafety: public market data only; no trading orders are placed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        raise
