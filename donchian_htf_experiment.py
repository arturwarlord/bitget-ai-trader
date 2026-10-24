"""Read-only experiment: 5m Donchian breakout confirmed by completed 1h EMA200."""
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import donchian_experiment as base

HOUR_MS = 60 * 60 * 1000
TRADING_ENABLED = False


def fetch_hourly_candles():
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    earliest = now_ms - base.HISTORY_DAYS * 24 * 60 * 60 * 1000
    by_timestamp = {}
    end_time_ms = now_ms
    for _ in range(base.MAX_PAGES):
        params = {
            "symbol": base.SYMBOL,
            "productType": base.PRODUCT_TYPE,
            "granularity": "1H",
            "limit": str(base.CANDLE_LIMIT),
            "endTime": str(end_time_ms),
        }
        url = f"{base.BASE_URL}/api/v2/mix/market/history-candles?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers={"User-Agent": "read-only-research/1.0"})
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("code") != "00000":
            raise RuntimeError(f"Bitget hourly API error: {payload}")
        batch = []
        for row in payload.get("data", []):
            candle = {
                "timestamp": int(row[0]), "open": float(row[1]),
                "high": float(row[2]), "low": float(row[3]), "close": float(row[4]),
            }
            batch.append(candle)
            if earliest <= candle["timestamp"] and candle["timestamp"] + HOUR_MS <= now_ms:
                by_timestamp[candle["timestamp"]] = candle
        if not batch:
            break
        oldest = min(c["timestamp"] for c in batch)
        if oldest <= earliest:
            break
        if oldest >= end_time_ms:
            raise RuntimeError("Bitget hourly pagination did not move backwards.")
        end_time_ms = oldest - 1
        time.sleep(0.2)
    candles = [by_timestamp[k] for k in sorted(by_timestamp)]
    if len(candles) < 220:
        raise RuntimeError(f"Only {len(candles)} completed hourly candles collected; need at least 220.")
    return candles


def htf_signals(candles, hourly):
    hourly_ema = base.ema_series([c["close"] for c in hourly], base.EMA_TREND_PERIOD)
    # A 1h candle is usable only after its close time. Map each 5m candle's
    # close to the latest fully completed 1h candle; no current-hour lookahead.
    completed = []
    j = -1
    signals = [None] * len(candles)
    atr = base.atr_series(candles, base.ATR_PERIOD)
    closes = [c["close"] for c in candles]
    for i, candle in enumerate(candles):
        signal_close_ms = candle["timestamp"] + base.TIMEFRAME_MS
        while j + 1 < len(hourly) and hourly[j + 1]["timestamp"] + HOUR_MS <= signal_close_ms:
            j += 1
        completed.append(j)
        if i < base.DONCHIAN_PERIOD or atr[i] is None or j < 0 or hourly_ema[j] is None:
            continue
        prior_high = max(c["high"] for c in candles[i-base.DONCHIAN_PERIOD:i])
        prior_low = min(c["low"] for c in candles[i-base.DONCHIAN_PERIOD:i])
        trend = hourly_ema[j]
        if closes[i] > prior_high and closes[i] > trend:
            signals[i] = 1
        elif closes[i] < prior_low and closes[i] < trend:
            signals[i] = -1
    return signals, atr


def main():
    candles = base.get_candles()
    hourly = fetch_hourly_candles()
    split = int(len(candles) * 0.70)

    # Baseline and HTF experiment share the exact same 5m data, split,
    # execution simulator, ATR stops/targets, fees and slippage.
    baseline_full = base.simulate(candles, evaluation_start=0)
    baseline_oos = base.simulate(candles, evaluation_start=split)

    original_make_signals = base.make_signals
    try:
        base.make_signals = lambda rows: htf_signals(rows, hourly)
        htf_full = base.simulate(candles, evaluation_start=0)
        htf_oos = base.simulate(candles, evaluation_start=split)
    finally:
        base.make_signals = original_make_signals

    start_price = candles[split]["open"]
    end_price = candles[-1]["close"]
    buy_hold_gross = (end_price / start_price - 1) * 100
    buy_hold_net = buy_hold_gross - base.ROUND_TRIP_COST_PCT

    report = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "exchange": "Bitget", "market": "USDT Futures", "symbol": base.SYMBOL,
        "entry_timeframe": "5m", "confirmation_timeframe": "1h",
        "strategy": "Donchian 20 breakout + EMA200 from last fully completed 1h candle",
        "history_days_requested": base.HISTORY_DAYS,
        "candles_5m": len(candles), "completed_candles_1h": len(hourly),
        "estimated_round_trip_cost_pct": base.ROUND_TRIP_COST_PCT,
        "parameters": {
            "donchian_period_5m": base.DONCHIAN_PERIOD,
            "ema_period_1h": base.EMA_TREND_PERIOD,
            "atr_period_5m": base.ATR_PERIOD,
            "stop_atr_multiplier": base.STOP_ATR_MULTIPLIER,
            "target_atr_multiplier": base.TARGET_ATR_MULTIPLIER,
            "max_hold_candles_5m": base.MAX_HOLD_CANDLES,
            "entry": "breakout confirmed at 5m close; enter next 5m candle open",
            "confirmation": "only hourly candles with close timestamp <= 5m signal candle close",
            "position_sizing": "unleveraged; notional equals current equity",
        },
        "out_of_sample_holdout": {
            "method": "chronological 70/30 split; last 30% is held out",
            "split_candle_utc": datetime.fromtimestamp(candles[split]["timestamp"]/1000, timezone.utc).isoformat(),
            "baseline_5m_ema200": baseline_oos,
            "donchian_with_1h_ema200": htf_oos,
            "buy_and_hold_btc": {
                "start_price": round(start_price, 2), "end_price": round(end_price, 2),
                "gross_return_pct": round(buy_hold_gross, 4),
                "net_return_after_one_round_trip_cost_pct": round(buy_hold_net, 4),
            },
        },
        "development_full_period_diagnostic": {
            "baseline_5m_ema200": baseline_full,
            "donchian_with_1h_ema200": htf_full,
        },
        "warnings": [
            "Read-only historical experiment; no orders are placed.",
            "Fees and slippage are illustrative estimates; funding is excluded.",
            "If stop and target are both touched in one OHLC candle, stop is assumed first.",
            "Development metrics cover the full period for diagnostic context, not a clean in-sample-only estimate.",
            "Holdout results are preliminary and do not establish future profitability.",
        ],
        "trading_enabled": TRADING_ENABLED,
    }
    with open("donchian-htf-report.json", "w", encoding="utf-8") as output:
        json.dump(report, output, indent=2, ensure_ascii=False)
    print("\n===== DONCHIAN + 1H EMA200 (READ-ONLY) =====")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print("\nSafety: public market data only; no orders are placed.")


if __name__ == "__main__":
    main()
