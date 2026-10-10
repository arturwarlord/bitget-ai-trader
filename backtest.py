import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE_URL = "https://api.bitget.com"
SYMBOL = "BTCUSDT"
PRODUCT_TYPE = "USDT-FUTURES"
TIMEFRAME = "5m"
CANDLE_LIMIT = 1000
TIMEFRAME_MS = 5 * 60 * 1000
HISTORY_DAYS = 30
MAX_PAGES = 12

# Illustrative estimates, not your actual Bitget fee tier.
TAKER_FEE_PER_SIDE = 0.0006
SLIPPAGE_PER_SIDE = 0.0002
ROUND_TRIP_COST_PCT = 2 * (TAKER_FEE_PER_SIDE + SLIPPAGE_PER_SIDE) * 100


def request_candles(end_time_ms=None):
    params = {
        "symbol": SYMBOL,
        "productType": PRODUCT_TYPE,
        "granularity": TIMEFRAME,
        "limit": str(CANDLE_LIMIT),
    }
    if end_time_ms is not None:
        params["endTime"] = str(end_time_ms)

    url = f"{BASE_URL}/api/v2/mix/market/candles?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "bitget-ai-trader-backtest/0.2", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if payload.get("code") != "00000":
        raise RuntimeError(f"Bitget API error: {payload.get('msg', 'unknown error')}")

    result = []
    for row in payload.get("data", []):
        if len(row) < 5:
            continue
        result.append({
            "timestamp": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
        })
    return result


def get_candles():
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    earliest_needed = now_ms - HISTORY_DAYS * 24 * 60 * 60 * 1000
    all_candles = {}
    end_time_ms = now_ms

    # Page backwards to collect a longer historical sample than a single 1,000-candle response.
    for page in range(MAX_PAGES):
        batch = request_candles(end_time_ms=end_time_ms)
        if not batch:
            break

        for candle in batch:
            if earliest_needed <= candle["timestamp"] and candle["timestamp"] + TIMEFRAME_MS <= now_ms:
                all_candles[candle["timestamp"]] = candle

        oldest_timestamp = min(c["timestamp"] for c in batch)
        if oldest_timestamp <= earliest_needed:
            break
        if oldest_timestamp >= end_time_ms:
            raise RuntimeError("Bitget candle pagination did not move backwards.")
        end_time_ms = oldest_timestamp - 1
        time.sleep(0.2)

    candles = [all_candles[key] for key in sorted(all_candles)]
    if len(candles) < 500:
        raise RuntimeError(
            f"Only {len(candles)} closed candles collected; expected a larger sample. "
            "Check Bitget API pagination or try again later."
        )
    return candles


def ema(values, period):
    alpha = 2 / (period + 1)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = value * alpha + result * (1 - alpha)
    return result


def rsi(values, period=14):
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def signal_for(closes):
    if len(closes) < 30:
        return 0
    fast = ema(closes, 9)
    slow = ema(closes, 21)
    strength = rsi(closes, 14)
    if fast > slow and 50 <= strength < 70:
        return 1
    if fast < slow and 30 < strength <= 50:
        return -1
    return 0


def run_backtest(candles):
    closes = [c["close"] for c in candles]
    position = 0
    entry_price = None
    trades = []
    equity = 1.0
    equity_peak = 1.0
    max_drawdown_pct = 0.0

    def close_position(exit_price, exit_timestamp):
        nonlocal equity, equity_peak, max_drawdown_pct
        market_return_pct = position * (exit_price / entry_price - 1) * 100
        net_return_pct = market_return_pct - ROUND_TRIP_COST_PCT
        trades.append({
            "side": "LONG" if position == 1 else "SHORT",
            "entry_price": round(entry_price, 2),
            "exit_price": round(exit_price, 2),
            "exit_candle_utc": datetime.fromtimestamp(
                exit_timestamp / 1000, timezone.utc
            ).isoformat(),
            "gross_return_pct": round(market_return_pct, 4),
            "estimated_cost_pct": round(ROUND_TRIP_COST_PCT, 4),
            "net_return_pct": round(net_return_pct, 4),
            "result": "WIN" if net_return_pct > 0 else "LOSS",
        })
        # Unleveraged, fully compounded illustrative equity curve.
        equity *= max(0.0, 1 + net_return_pct / 100)
        equity_peak = max(equity_peak, equity)
        if equity_peak > 0:
            drawdown = (equity_peak - equity) / equity_peak * 100
            max_drawdown_pct = max(max_drawdown_pct, drawdown)

    # A signal after candle i closes is executed at candle i+1 open.
    for i in range(29, len(candles) - 1):
        desired = signal_for(closes[:i + 1])
        next_candle = candles[i + 1]
        execution_price = next_candle["open"]

        if desired == position:
            continue

        if position != 0 and entry_price is not None:
            close_position(execution_price, next_candle["timestamp"])

        position = desired
        entry_price = execution_price if desired != 0 else None

    if position != 0 and entry_price is not None:
        close_position(candles[-1]["close"], candles[-1]["timestamp"])
        position = 0
        entry_price = None

    wins = sum(1 for trade in trades if trade["net_return_pct"] > 0)
    gross_sum = sum(trade["gross_return_pct"] for trade in trades)
    net_sum = sum(trade["net_return_pct"] for trade in trades)

    return {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "exchange": "Bitget",
        "market": "USDT Futures",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "strategy": "EMA 9/21 + RSI 14; long and short; enter at next candle open",
        "history_days_requested": HISTORY_DAYS,
        "candles_tested": len(candles),
        "first_candle_utc": datetime.fromtimestamp(candles[0]["timestamp"] / 1000, timezone.utc).isoformat(),
        "last_candle_utc": datetime.fromtimestamp(candles[-1]["timestamp"] / 1000, timezone.utc).isoformat(),
        "estimated_taker_fee_per_side_pct": round(TAKER_FEE_PER_SIDE * 100, 4),
        "estimated_slippage_per_side_pct": round(SLIPPAGE_PER_SIDE * 100, 4),
        "estimated_round_trip_cost_pct": round(ROUND_TRIP_COST_PCT, 4),
        "trades": len(trades),
        "winning_trades": wins,
        "losing_trades": len(trades) - wins,
        "win_rate_pct": round(wins / len(trades) * 100, 2) if trades else 0.0,
        "gross_sum_of_trade_returns_pct": round(gross_sum, 4),
        "net_sum_of_trade_returns_pct": round(net_sum, 4),
        "compounded_unleveraged_return_pct": round((equity - 1) * 100, 4),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        "recent_trades": trades[-10:],
        "trading_enabled": False,
        "warning": (
            "Historical simulation only. Fee and slippage values are estimates, funding is excluded, "
            "and the model assumes unleveraged exposure with next-candle-open execution. "
            "Results do not predict future performance."
        ),
    }


def main():
    candles = get_candles()
    report = run_backtest(candles)
    with open("backtest-report.json", "w", encoding="utf-8") as report_file:
        json.dump(report, report_file, indent=2, ensure_ascii=False)

    print("\n===== BITGET HISTORICAL BACKTEST (READ-ONLY) =====")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print("\nSafety: this script only reads public market data. It does not place orders.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        raise
