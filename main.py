import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone


BASE_URL = "https://api.bitget.com"
SYMBOL = "BTCUSDT"
PRODUCT_TYPE = "USDT-FUTURES"
TIMEFRAME = "5m"
CANDLE_LIMIT = 100


def get_candles():
    params = urllib.parse.urlencode({
        "symbol": SYMBOL,
        "productType": PRODUCT_TYPE,
        "granularity": TIMEFRAME,
        "limit": str(CANDLE_LIMIT),
    })
    url = f"{BASE_URL}/api/v2/mix/market/candles?{params}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "bitget-ai-trader/0.1", "Accept": "application/json"},
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if payload.get("code") != "00000":
        raise RuntimeError(f"Bitget API error: {payload.get('msg', 'unknown error')}")

    rows = payload.get("data", [])
    if not rows:
        raise RuntimeError("Bitget returned no candle data")

    # API returns newest candles first; indicators need oldest first.
    rows = list(reversed(rows))
    candles = []
    for row in rows:
        if len(row) < 5:
            continue
        candles.append({
            "timestamp": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
        })

    if len(candles) < 30:
        raise RuntimeError(f"Not enough candle data: received {len(candles)}")

    return candles


def ema(values, period):
    if len(values) < period:
        raise ValueError(f"Need at least {period} values for EMA")
    alpha = 2 / (period + 1)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = value * alpha + result * (1 - alpha)
    return result


def rsi(values, period=14):
    if len(values) <= period:
        raise ValueError(f"Need more than {period} values for RSI")

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
    relative_strength = avg_gain / avg_loss
    return 100 - (100 / (1 + relative_strength))


def analyze():
    candles = get_candles()
    closes = [candle["close"] for candle in candles]
    last = candles[-1]
    ema_9 = ema(closes, 9)
    ema_21 = ema(closes, 21)
    rsi_14 = rsi(closes, 14)

    if ema_9 > ema_21 and 50 <= rsi_14 < 70:
        signal = "BULLISH_BIAS"
        explanation = "EMA 9 выше EMA 21, RSI подтверждает умеренный импульс вверх."
    elif ema_9 < ema_21 and 30 < rsi_14 <= 50:
        signal = "BEARISH_BIAS"
        explanation = "EMA 9 ниже EMA 21, RSI указывает на слабость импульса."
    else:
        signal = "NEUTRAL"
        explanation = "Условия не дают достаточно согласованного сигнала."

    result = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "exchange": "Bitget",
        "market": "USDT Futures",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "latest_candle_utc": datetime.fromtimestamp(
            last["timestamp"] / 1000, timezone.utc
        ).isoformat(),
        "last_price": last["close"],
        "ema_9": round(ema_9, 4),
        "ema_21": round(ema_21, 4),
        "rsi_14": round(rsi_14, 2),
        "signal": signal,
        "explanation": explanation,
        "trading_enabled": False,
    }

    print("\n===== BITGET MARKET ANALYSIS (READ-ONLY) =====")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("\nSafety: this version only reads public market data. It does not place orders.")
    return result


if __name__ == "__main__":
    try:
        analyze()
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        raise
