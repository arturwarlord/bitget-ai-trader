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
TAKER_FEE_PER_SIDE = 0.0006
SLIPPAGE_PER_SIDE = 0.0002
ROUND_TRIP_COST_PCT = 2 * (TAKER_FEE_PER_SIDE + SLIPPAGE_PER_SIDE) * 100
DONCHIAN_PERIOD = 20
EMA_TREND_PERIOD = 200
ATR_PERIOD = 14
ADX_PERIOD = 14
ADX_THRESHOLDS = [15, 20, 25, 30]
STOP_ATR_MULTIPLIER = 1.5
TARGET_ATR_MULTIPLIER = 2.5
MAX_HOLD_CANDLES = 48
TRADING_ENABLED = False


def request_candles(end_time_ms=None):
    params = {"symbol": SYMBOL, "productType": PRODUCT_TYPE, "granularity": TIMEFRAME, "limit": str(CANDLE_LIMIT)}
    if end_time_ms is not None:
        params["endTime"] = str(end_time_ms)
    url = f"{BASE_URL}/api/v2/mix/market/history-candles?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "bitget-ai-trader-trend-filter/1.0", "Accept": "application/json"})
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


def atr_adx_series(candles, period):
    n = len(candles)
    atr = [None] * n
    adx = [None] * n
    tr = [None] * n
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        up = candles[i]["high"] - candles[i - 1]["high"]
        down = candles[i - 1]["low"] - candles[i]["low"]
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0
        tr[i] = max(candles[i]["high"] - candles[i]["low"],
                    abs(candles[i]["high"] - candles[i - 1]["close"]),
                    abs(candles[i]["low"] - candles[i - 1]["close"]))
    if n <= 2 * period:
        return atr, adx
    sm_tr = sum(tr[1:period + 1])
    sm_plus = sum(plus_dm[1:period + 1])
    sm_minus = sum(minus_dm[1:period + 1])
    dx = [None] * n
    for i in range(period, n):
        if i > period:
            sm_tr = sm_tr - sm_tr / period + tr[i]
            sm_plus = sm_plus - sm_plus / period + plus_dm[i]
            sm_minus = sm_minus - sm_minus / period + minus_dm[i]
        atr[i] = sm_tr / period
        plus_di = 100 * sm_plus / sm_tr if sm_tr else 0
        minus_di = 100 * sm_minus / sm_tr if sm_tr else 0
        denom = plus_di + minus_di
        dx[i] = 100 * abs(plus_di - minus_di) / denom if denom else 0
    first = 2 * period - 1
    valid = [x for x in dx[period:first + 1] if x is not None]
    if len(valid) == period:
        adx[first] = sum(valid) / period
        for i in range(first + 1, n):
            adx[i] = ((adx[i - 1] * (period - 1)) + dx[i]) / period if dx[i] is not None else None
    return atr, adx


def prepare(candles):
    closes = [c["close"] for c in candles]
    ema = ema_series(closes, EMA_TREND_PERIOD)
    atr, adx = atr_adx_series(candles, ADX_PERIOD)
    raw = [None] * len(candles)
    for i in range(DONCHIAN_PERIOD, len(candles)):
        if ema[i] is None:
            continue
        prior_high = max(c["high"] for c in candles[i - DONCHIAN_PERIOD:i])
        prior_low = min(c["low"] for c in candles[i - DONCHIAN_PERIOD:i])
        if closes[i] > prior_high and closes[i] > ema[i]:
            raw[i] = 1
        elif closes[i] < prior_low and closes[i] < ema[i]:
            raw[i] = -1
    return raw, atr, adx


def simulate(candles, raw_signals, atr_values, adx_values, threshold, evaluation_start=0):
    start = max(evaluation_start, EMA_TREND_PERIOD + 1, DONCHIAN_PERIOD + 1, 2 * ADX_PERIOD)
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
        trades.append({"side": "LONG" if side == 1 else "SHORT",
                       "entry_utc": datetime.fromtimestamp(position["entry_timestamp"] / 1000, timezone.utc).isoformat(),
                       "exit_utc": datetime.fromtimestamp(timestamp / 1000, timezone.utc).isoformat(),
                       "gross_return_pct": round(gross, 5), "net_return_pct": round(net, 5),
                       "bars_held": position["bars_held"], "exit_reason": reason})
        position = None

    for i in range(start, len(candles)):
        c = candles[i]
        if pending is not None:
            side, signal_atr = pending
            if position is None and signal_atr and signal_atr > 0:
                entry = c["open"]
                sd = STOP_ATR_MULTIPLIER * signal_atr
                td = TARGET_ATR_MULTIPLIER * signal_atr
                position = {"side": side, "entry_price": entry, "entry_timestamp": c["timestamp"],
                            "stop": entry - sd if side == 1 else entry + sd,
                            "target": entry + td if side == 1 else entry - td, "bars_held": 0}
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
        # ADX filter is checked on signal candle close; entry is next candle open.
        if position is None and raw_signals[i] in (-1, 1) and atr_values[i] is not None and adx_values[i] is not None and adx_values[i] >= threshold:
            pending = (raw_signals[i], atr_values[i])
    returns = [t["net_return_pct"] for t in trades]
    wins = sum(x for x in returns if x > 0)
    losses = -sum(x for x in returns if x < 0)
    capital = 1.0
    for x in returns:
        capital *= max(0.0, 1 + x / 100)
    return {"adx_minimum": threshold, "candles_evaluated": len(candles) - start,
            "evaluation_start_utc": datetime.fromtimestamp(candles[start]["timestamp"] / 1000, timezone.utc).isoformat(),
            "evaluation_end_utc": datetime.fromtimestamp(candles[-1]["timestamp"] / 1000, timezone.utc).isoformat(),
            "trades": len(trades),
            "win_rate_pct": round(sum(x > 0 for x in returns) / len(returns) * 100, 2) if returns else 0,
            "profit_factor": round(wins / losses, 4) if losses else (None if not wins else "infinite"),
            "net_sum_pct_points": round(sum(returns), 4),
            "compounded_unleveraged_return_pct": round((capital - 1) * 100, 4),
            "max_drawdown_pct": round(max_dd, 4),
            "long_trades": sum(t["side"] == "LONG" for t in trades),
            "short_trades": sum(t["side"] == "SHORT" for t in trades)}


def main():
    candles = get_candles()
    raw, atr, adx = prepare(candles)
    split = int(len(candles) * 0.70)
    results = []
    for threshold in ADX_THRESHOLDS:
        full = simulate(candles, raw, atr, adx, threshold, 0)
        oos = simulate(candles, raw, atr, adx, threshold, split)
        results.append({"full_period_diagnostic": full, "out_of_sample_holdout": {
            "method": "chronological 70/30 split; last 30% simulated only after split; prior candles used for indicator warmup",
            "split_candle_utc": datetime.fromtimestamp(candles[split]["timestamp"] / 1000, timezone.utc).isoformat(),
            **oos}})
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "exchange": "Bitget",
              "market": "USDT Futures", "symbol": SYMBOL, "timeframe": TIMEFRAME,
              "strategy": "Donchian breakout + EMA200 direction + ADX trend-strength filter",
              "history_days_requested": HISTORY_DAYS, "candles_collected": len(candles),
              "estimated_round_trip_cost_pct": ROUND_TRIP_COST_PCT,
              "parameters": {"donchian_period": DONCHIAN_PERIOD, "ema_trend_period": EMA_TREND_PERIOD,
                "adx_period": ADX_PERIOD, "tested_adx_minimums": ADX_THRESHOLDS,
                "atr_period": ATR_PERIOD, "stop_atr_multiplier": STOP_ATR_MULTIPLIER,
                "target_atr_multiplier": TARGET_ATR_MULTIPLIER, "max_hold_candles": MAX_HOLD_CANDLES,
                "entry": "signal on closed candle; entry at next candle open",
                "position_sizing": "unleveraged; no leverage applied"},
              "results": results,
              "warnings": ["Read-only historical experiment; no orders are placed.",
                "Fees and slippage are illustrative estimates; funding is excluded.",
                "If stop and target are both touched within one OHLC candle, stop is assumed first.",
                "Full-period figures are diagnostic and not clean in-sample estimates.",
                "Chronological holdout is preliminary and does not establish future profitability."],
              "trading_enabled": TRADING_ENABLED}
    with open("trend-filter-report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print("\n===== BITGET TREND FILTER EXPERIMENT (READ-ONLY) =====")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print("\nSafety: public market data only; no trading orders are placed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        raise
