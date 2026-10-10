import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE_URL = "https://api.bitget.com"
SYMBOL = "BTCUSDT"
PRODUCT_TYPE = "USDT-FUTURES"
TIMEFRAME = "5m"
TIMEFRAME_MS = 5 * 60 * 1000
CANDLE_LIMIT = 200
HISTORY_DAYS = 90
MAX_PAGES = 500

# Illustrative cost assumptions, not your personal Bitget fee tier.
TAKER_FEE_PER_SIDE = 0.0006
SLIPPAGE_PER_SIDE = 0.0002
ROUND_TRIP_COST_PCT = 2 * (TAKER_FEE_PER_SIDE + SLIPPAGE_PER_SIDE) * 100

DONCHIAN_PERIOD = 20
EMA_TREND_PERIOD = 200
ATR_PERIOD = 14
STOP_ATR_MULTIPLIER = 1.5
TARGET_ATR_MULTIPLIER = 2.5
MAX_HOLD_CANDLES = 48
TRADING_ENABLED = False


def request_candles(end_time_ms=None):
    params = {
        "symbol": SYMBOL,
        "productType": PRODUCT_TYPE,
        "granularity": TIMEFRAME,
        "limit": str(CANDLE_LIMIT),
    }
    if end_time_ms is not None:
        params["endTime"] = str(end_time_ms)
    url = f"{BASE_URL}/api/v2/mix/market/history-candles?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url, headers={"User-Agent": "bitget-ai-trader-donchian-experiment/1.0", "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("code") != "00000":
        raise RuntimeError(f"Bitget API error: {payload.get('msg', 'unknown error')}")
    candles = []
    for row in payload.get("data", []):
        if len(row) >= 5:
            candles.append({
                "timestamp": int(row[0]), "open": float(row[1]),
                "high": float(row[2]), "low": float(row[3]), "close": float(row[4])
            })
    return candles


def get_candles():
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    earliest = now_ms - HISTORY_DAYS * 24 * 60 * 60 * 1000
    by_timestamp = {}
    end_time_ms = now_ms
    for _ in range(MAX_PAGES):
        batch = request_candles(end_time_ms)
        if not batch:
            break
        for candle in batch:
            if earliest <= candle["timestamp"] and candle["timestamp"] + TIMEFRAME_MS <= now_ms:
                by_timestamp[candle["timestamp"]] = candle
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
    result = [None] * len(values)
    if len(values) < period:
        return result
    current = sum(values[:period]) / period
    result[period - 1] = current
    alpha = 2 / (period + 1)
    for i in range(period, len(values)):
        current = values[i] * alpha + current * (1 - alpha)
        result[i] = current
    return result


def atr_series(candles, period):
    result = [None] * len(candles)
    if len(candles) <= period:
        return result
    ranges = [None] * len(candles)
    for i in range(1, len(candles)):
        ranges[i] = max(
            candles[i]["high"] - candles[i]["low"],
            abs(candles[i]["high"] - candles[i - 1]["close"]),
            abs(candles[i]["low"] - candles[i - 1]["close"]),
        )
    current = sum(ranges[1:period + 1]) / period
    result[period] = current
    for i in range(period + 1, len(candles)):
        current = (current * (period - 1) + ranges[i]) / period
        result[i] = current
    return result


def make_signals(candles):
    closes = [c["close"] for c in candles]
    trend = ema_series(closes, EMA_TREND_PERIOD)
    atr = atr_series(candles, ATR_PERIOD)
    signals = [None] * len(candles)
    for i in range(DONCHIAN_PERIOD, len(candles)):
        if trend[i] is None or atr[i] is None:
            continue
        # Channel is calculated from candles strictly before the signal candle.
        prior_high = max(c["high"] for c in candles[i - DONCHIAN_PERIOD:i])
        prior_low = min(c["low"] for c in candles[i - DONCHIAN_PERIOD:i])
        if closes[i] > prior_high and closes[i] > trend[i]:
            signals[i] = 1
        elif closes[i] < prior_low and closes[i] < trend[i]:
            signals[i] = -1
    return signals, atr


def simulate(candles, evaluation_start=0):
    signals, atr_values = make_signals(candles)
    start = max(evaluation_start, EMA_TREND_PERIOD + 1, DONCHIAN_PERIOD + 1, ATR_PERIOD + 1)
    position = None
    pending = None
    trades = []
    equity = 1.0
    peak = 1.0
    max_dd = 0.0

    def close_position(price, timestamp, reason):
        nonlocal position, equity
        side = position["side"]
        gross = side * (price / position["entry_price"] - 1) * 100
        net = gross - ROUND_TRIP_COST_PCT
        equity *= max(0.0, 1 + net / 100)
        trades.append({
            "side": "LONG" if side == 1 else "SHORT",
            "entry_utc": datetime.fromtimestamp(position["entry_timestamp"] / 1000, timezone.utc).isoformat(),
            "exit_utc": datetime.fromtimestamp(timestamp / 1000, timezone.utc).isoformat(),
            "entry_price": round(position["entry_price"], 2),
            "exit_price": round(price, 2),
            "gross_return_pct": round(gross, 5),
            "net_return_pct": round(net, 5),
            "bars_held": position["bars_held"],
            "exit_reason": reason,
        })
        position = None

    for i in range(start, len(candles)):
        candle = candles[i]
        if pending is not None:
            side, signal_atr = pending
            if side in (-1, 1) and position is None and signal_atr and signal_atr > 0:
                entry = candle["open"]
                distance = STOP_ATR_MULTIPLIER * signal_atr
                target = TARGET_ATR_MULTIPLIER * signal_atr
                position = {
                    "side": side, "entry_price": entry, "entry_timestamp": candle["timestamp"],
                    "stop": entry - distance if side == 1 else entry + distance,
                    "target": entry + target if side == 1 else entry - target,
                    "bars_held": 0,
                }
            pending = None

        if position is not None:
            position["bars_held"] += 1
            side, stop, target = position["side"], position["stop"], position["target"]
            exit_price, reason = None, None
            # Conservative handling: stop wins if stop and target are both touched.
            if side == 1:
                if candle["open"] <= stop:
                    exit_price, reason = candle["open"], "stop_gap"
                elif candle["open"] >= target:
                    exit_price, reason = candle["open"], "target_gap"
                elif candle["low"] <= stop:
                    exit_price, reason = stop, "stop_loss"
                elif candle["high"] >= target:
                    exit_price, reason = target, "take_profit"
            else:
                if candle["open"] >= stop:
                    exit_price, reason = candle["open"], "stop_gap"
                elif candle["open"] <= target:
                    exit_price, reason = candle["open"], "target_gap"
                elif candle["high"] >= stop:
                    exit_price, reason = stop, "stop_loss"
                elif candle["low"] <= target:
                    exit_price, reason = target, "take_profit"
            if exit_price is not None:
                close_position(exit_price, candle["timestamp"], reason)
            elif position is not None and position["bars_held"] >= MAX_HOLD_CANDLES:
                close_position(candle["close"], candle["timestamp"], "max_holding_time")

        if position is not None:
            adverse = candle["low"] if position["side"] == 1 else candle["high"]
            marked = equity * max(0.0, 1 + position["side"] * (adverse / position["entry_price"] - 1))
        else:
            marked = equity
        peak = max(peak, marked)
        if peak > 0:
            max_dd = max(max_dd, (peak - marked) / peak * 100)

        # Signal on candle close; if flat, enter at the next candle open.
        if position is None and signals[i] in (-1, 1) and atr_values[i] is not None:
            pending = (signals[i], atr_values[i])

    if position is not None:
        close_position(candles[-1]["close"], candles[-1]["timestamp"], "end_of_test")

    def summarize(subset):
        returns = [t["gross_return_pct"] - ROUND_TRIP_COST_PCT for t in subset]
        profits = sum(x for x in returns if x > 0)
        losses = -sum(x for x in returns if x < 0)
        capital = 1.0
        for value in returns:
            capital *= max(0.0, 1 + value / 100)
        return {
            "trades": len(subset),
            "win_rate_pct": round(sum(x > 0 for x in returns) / len(returns) * 100, 2) if returns else 0,
            "profit_factor": round(profits / losses, 4) if losses else (None if not profits else "infinite"),
            "net_sum_pct_points": round(sum(returns), 4),
            "compounded_unleveraged_return_pct": round((capital - 1) * 100, 4),
            "average_net_trade_pct": round(sum(returns) / len(returns), 5) if returns else 0,
        }

    by_side = {
        side: summarize([t for t in trades if t["side"] == side])
        for side in ("LONG", "SHORT")
    }
    by_exit = {
        reason: summarize([t for t in trades if t["exit_reason"] == reason])
        for reason in sorted({t["exit_reason"] for t in trades})
    }
    return {
        "candles_evaluated": len(candles) - start,
        "evaluation_start_utc": datetime.fromtimestamp(candles[start]["timestamp"] / 1000, timezone.utc).isoformat(),
        "evaluation_end_utc": datetime.fromtimestamp(candles[-1]["timestamp"] / 1000, timezone.utc).isoformat(),
        "trades": len(trades),
        "win_rate_pct": summarize(trades)["win_rate_pct"],
        "profit_factor": summarize(trades)["profit_factor"],
        "net_sum_pct_points": summarize(trades)["net_sum_pct_points"],
        "compounded_unleveraged_return_pct": summarize(trades)["compounded_unleveraged_return_pct"],
        "max_drawdown_pct": round(max_dd, 4),
        "performance_by_side": by_side,
        "performance_by_exit_reason": by_exit,
        "recent_trades": trades[-10:],
    }


def main():
    candles = get_candles()
    split = int(len(candles) * 0.70)
    # Full history is supplied to indicators for warmup, but simulation starts at
    # the requested boundary. OOS trades are only simulated after the split.
    development = simulate(candles, evaluation_start=0)
    oos = simulate(candles, evaluation_start=split)
    report = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "exchange": "Bitget",
        "market": "USDT Futures",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "strategy": "Donchian breakout 20 candles + EMA 200 direction filter + ATR stop/target",
        "history_days_requested": HISTORY_DAYS,
        "candles_collected": len(candles),
        "estimated_round_trip_cost_pct": ROUND_TRIP_COST_PCT,
        "parameters": {
            "donchian_period": DONCHIAN_PERIOD,
            "ema_trend_period": EMA_TREND_PERIOD,
            "atr_period": ATR_PERIOD,
            "stop_atr_multiplier": STOP_ATR_MULTIPLIER,
            "target_atr_multiplier": TARGET_ATR_MULTIPLIER,
            "max_hold_candles": MAX_HOLD_CANDLES,
            "max_hold_minutes": MAX_HOLD_CANDLES * 5,
            "entry": "breakout confirmed at candle close; entry at next candle open",
            "position_sizing": "unleveraged; notional equals current equity",
        },
        "development_full_period_diagnostic": development,
        "out_of_sample_holdout": {
            "method": "chronological 70/30 split; last 30% is held out",
            "split_candle_utc": datetime.fromtimestamp(candles[split]["timestamp"] / 1000, timezone.utc).isoformat(),
            **oos,
        },
        "warnings": [
            "Read-only historical experiment; no orders are placed.",
            "Fees and slippage are illustrative estimates; funding is excluded.",
            "If stop and target are both touched within one OHLC candle, stop is assumed first.",
            "Development metrics cover the full period for diagnostic context and are not a clean in-sample-only estimate.",
            "The chronological holdout is preliminary and does not establish future profitability.",
        ],
        "trading_enabled": TRADING_ENABLED,
    }
    with open("donchian-report.json", "w", encoding="utf-8") as output:
        json.dump(report, output, indent=2, ensure_ascii=False)
    print("\n===== BITGET DONCHIAN BREAKOUT EXPERIMENT (READ-ONLY) =====")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print("\nSafety: public market data only; no trading orders are placed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        raise
