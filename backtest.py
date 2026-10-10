import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE_URL = "https://api.bitget.com"
SYMBOL = "BTCUSDT"
PRODUCT_TYPE = "USDT-FUTURES"
TIMEFRAME = "5m"
CANDLE_LIMIT = 200  # Bitget historical-candles endpoint maximum
TIMEFRAME_MS = 5 * 60 * 1000
HISTORY_DAYS = 90
MAX_PAGES = 500  # 90 days of 5m candles needs about 130 pages at 200 candles/page

# Illustrative estimates, not your actual Bitget fee tier.
TAKER_FEE_PER_SIDE = 0.0006
SLIPPAGE_PER_SIDE = 0.0002
ROUND_TRIP_COST_PCT = 2 * (TAKER_FEE_PER_SIDE + SLIPPAGE_PER_SIDE) * 100

EMA_FAST = 9
EMA_SLOW = 21
EMA_TREND = 200
RSI_PERIOD = 14
ATR_PERIOD = 14
STOP_ATR_MULTIPLIER = 1.5
TAKE_PROFIT_ATR_MULTIPLIER = 2.0
MAX_HOLD_CANDLES = 48  # 4 hours on a 5-minute chart


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
        url,
        headers={"User-Agent": "bitget-ai-trader-backtest/0.4", "Accept": "application/json"},
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

    for _ in range(MAX_PAGES):
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
    if len(candles) < 1000:
        raise RuntimeError(
            f"Only {len(candles)} closed candles collected; expected a larger sample. "
            "Check Bitget API pagination or try again later."
        )
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


def rsi_series(closes, period=14):
    result = [None] * len(closes)
    if len(closes) <= period:
        return result

    gains = []
    losses = []
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    def calculate_rsi(gain, loss):
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        return 100 - 100 / (1 + gain / loss)

    result[period] = calculate_rsi(avg_gain, avg_loss)
    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        result[i] = calculate_rsi(avg_gain, avg_loss)
    return result


def atr_series(candles, period=14):
    result = [None] * len(candles)
    if len(candles) <= period:
        return result

    true_ranges = [None] * len(candles)
    for i in range(1, len(candles)):
        true_ranges[i] = max(
            candles[i]["high"] - candles[i]["low"],
            abs(candles[i]["high"] - candles[i - 1]["close"]),
            abs(candles[i]["low"] - candles[i - 1]["close"]),
        )

    current = sum(true_ranges[1:period + 1]) / period
    result[period] = current
    for i in range(period + 1, len(candles)):
        current = (current * (period - 1) + true_ranges[i]) / period
        result[i] = current
    return result


def make_signals(candles):
    closes = [c["close"] for c in candles]
    fast = ema_series(closes, EMA_FAST)
    slow = ema_series(closes, EMA_SLOW)
    trend = ema_series(closes, EMA_TREND)
    strength = rsi_series(closes, RSI_PERIOD)
    atr = atr_series(candles, ATR_PERIOD)

    # None means "no new signal". Only EMA crossovers can open/reverse a trade;
    # this avoids exiting simply because RSI drifts outside its entry band.
    signals = [None] * len(candles)
    for i in range(1, len(candles)):
        if any(series[i] is None or series[i - 1] is None for series in (fast, slow, trend, strength, atr)):
            continue

        bullish_cross = fast[i - 1] <= slow[i - 1] and fast[i] > slow[i]
        bearish_cross = fast[i - 1] >= slow[i - 1] and fast[i] < slow[i]

        if bullish_cross:
            # On a bullish crossover, close an existing short even if entry filters fail.
            signals[i] = 0
            if closes[i] > trend[i] and 52 <= strength[i] < 68:
                signals[i] = 1
        elif bearish_cross:
            # On a bearish crossover, close an existing long even if entry filters fail.
            signals[i] = 0
            if closes[i] < trend[i] and 32 < strength[i] <= 48:
                signals[i] = -1

    return signals, atr


def run_backtest(candles, timeframe_minutes=5):
    signals, atr_values = make_signals(candles)
    position = None
    pending_side = None  # None means no scheduled action; 0 means scheduled exit.
    pending_atr = None
    trades = []
    equity = 1.0
    equity_peak = 1.0
    max_drawdown_pct = 0.0
    total_cost_pct_points = 0.0

    def update_drawdown(marked_equity=None):
        # Track peak equity and drawdown using mark-to-market equity, not only
        # realized equity after a trade closes.
        nonlocal equity_peak, max_drawdown_pct
        observed_equity = equity if marked_equity is None else max(0.0, marked_equity)
        equity_peak = max(equity_peak, observed_equity)
        if equity_peak > 0:
            drawdown = (equity_peak - observed_equity) / equity_peak * 100
            max_drawdown_pct = max(max_drawdown_pct, drawdown)

    def close_position(exit_price, exit_timestamp, exit_reason):
        nonlocal position, equity, total_cost_pct_points
        if position is None:
            return

        side = position["side"]
        gross_pct = side * (exit_price / position["entry_price"] - 1) * 100
        net_pct = gross_pct - ROUND_TRIP_COST_PCT
        # Unleveraged, fully compounded model: position notional equals current equity.
        equity *= max(0.0, 1 + net_pct / 100)
        total_cost_pct_points += ROUND_TRIP_COST_PCT
        trades.append({
            "side": "LONG" if side == 1 else "SHORT",
            "entry_price": round(position["entry_price"], 2),
            "exit_price": round(exit_price, 2),
            "entry_candle_utc": datetime.fromtimestamp(
                position["entry_timestamp"] / 1000, timezone.utc
            ).isoformat(),
            "exit_candle_utc": datetime.fromtimestamp(
                exit_timestamp / 1000, timezone.utc
            ).isoformat(),
            "bars_held": position["bars_held"],
            "gross_return_pct": round(gross_pct, 4),
            "estimated_cost_pct": round(ROUND_TRIP_COST_PCT, 4),
            "net_return_pct": round(net_pct, 4),
            "exit_reason": exit_reason,
            "result": "WIN" if net_pct > 0 else "LOSS",
        })
        position = None
        update_drawdown()

    warmup = EMA_TREND + 2
    max_hold_candles_for_run = max(1, round(MAX_HOLD_CANDLES * 5 / timeframe_minutes))
    for i in range(warmup, len(candles)):
        candle = candles[i]
        exited_this_candle = False

        # Apply the decision made at the previous candle close at this candle's open.
        if pending_side is not None:
            if pending_side != (position["side"] if position else 0):
                if position is not None:
                    close_position(candle["open"], candle["timestamp"], "signal_change")
                    exited_this_candle = True
                if pending_side != 0 and pending_atr is not None and pending_atr > 0:
                    distance = STOP_ATR_MULTIPLIER * pending_atr
                    entry = candle["open"]
                    position = {
                        "side": pending_side,
                        "entry_price": entry,
                        "entry_timestamp": candle["timestamp"],
                        "stop_price": entry - distance if pending_side == 1 else entry + distance,
                        "target_price": entry + TAKE_PROFIT_ATR_MULTIPLIER * pending_atr
                        if pending_side == 1 else entry - TAKE_PROFIT_ATR_MULTIPLIER * pending_atr,
                        "bars_held": 0,
                    }

        pending_side = None
        pending_atr = None

        # Evaluate stops and targets using this candle's range. If both are touched,
        # assume the stop was hit first (conservative; OHLC data cannot show the path).
        if position is not None:
            position["bars_held"] += 1
            side = position["side"]
            stop = position["stop_price"]
            target = position["target_price"]
            exit_price = None
            exit_reason = None

            if side == 1:
                if candle["open"] <= stop:
                    exit_price, exit_reason = candle["open"], "stop_gap"
                elif candle["open"] >= target:
                    exit_price, exit_reason = candle["open"], "take_profit_gap"
                elif candle["low"] <= stop:
                    exit_price, exit_reason = stop, "stop_loss"
                elif candle["high"] >= target:
                    exit_price, exit_reason = target, "take_profit"
            else:
                if candle["open"] >= stop:
                    exit_price, exit_reason = candle["open"], "stop_gap"
                elif candle["open"] <= target:
                    exit_price, exit_reason = candle["open"], "take_profit_gap"
                elif candle["high"] >= stop:
                    exit_price, exit_reason = stop, "stop_loss"
                elif candle["low"] <= target:
                    exit_price, exit_reason = target, "take_profit"

            if exit_price is not None:
                close_position(exit_price, candle["timestamp"], exit_reason)
                exited_this_candle = True
            elif position is not None and position["bars_held"] >= max_hold_candles_for_run:
                close_position(candle["close"], candle["timestamp"], "max_holding_time")
                exited_this_candle = True

        # Mark open-position risk at the adverse intrabar extreme. This is a
        # conservative drawdown estimate; it does not change realized trade P&L.
        if position is not None:
            side = position["side"]
            adverse_price = candle["low"] if side == 1 else candle["high"]
            unrealized_pct = side * (adverse_price / position["entry_price"] - 1) * 100
            marked_equity = equity * max(0.0, 1 + unrealized_pct / 100)
            update_drawdown(marked_equity)
        else:
            update_drawdown()

        # Signals are events, not a required state: None means keep current position.
        if not exited_this_candle and signals[i] is not None:
            desired = signals[i]
            current_side = position["side"] if position else 0
            if desired != current_side:
                pending_side = desired
                pending_atr = atr_values[i]

    if position is not None:
        close_position(candles[-1]["close"], candles[-1]["timestamp"], "end_of_test")

    wins = sum(1 for trade in trades if trade["net_return_pct"] > 0)
    gross_sum = sum(trade["gross_return_pct"] for trade in trades)
    net_sum = sum(trade["net_return_pct"] for trade in trades)
    net_wins_sum = sum(t["net_return_pct"] for t in trades if t["net_return_pct"] > 0)
    net_losses_sum = -sum(t["net_return_pct"] for t in trades if t["net_return_pct"] < 0)

    def summarize_trades(subset, cost_pct=ROUND_TRIP_COST_PCT):
        adjusted = [t["gross_return_pct"] - cost_pct for t in subset]
        positive = sum(value for value in adjusted if value > 0)
        negative = -sum(value for value in adjusted if value < 0)
        capital = 1.0
        for value in adjusted:
            capital *= max(0.0, 1 + value / 100)
        return {
            "trades": len(subset),
            "win_rate_pct": round(sum(value > 0 for value in adjusted) / len(adjusted) * 100, 2) if adjusted else 0.0,
            "profit_factor": round(positive / negative, 4) if negative else None,
            "compounded_return_pct": round((capital - 1) * 100, 4),
        }

    cost_sensitivity = []
    for scenario_cost in (0.08, 0.12, 0.16, 0.20, 0.24):
        cost_sensitivity.append({
            "round_trip_cost_pct": scenario_cost,
            **summarize_trades(trades, scenario_cost),
        })

    by_side = {
        "LONG": summarize_trades([t for t in trades if t["side"] == "LONG"]),
        "SHORT": summarize_trades([t for t in trades if t["side"] == "SHORT"]),
    }

    # Diagnostic breakdown: identify whether losses come primarily from stops,
    # signal reversals, time exits, or other close conditions.
    exit_reason_breakdown = {}
    for reason in sorted({t["exit_reason"] for t in trades}):
        subset = [t for t in trades if t["exit_reason"] == reason]
        gross_wins = sum(t["gross_return_pct"] for t in subset if t["gross_return_pct"] > 0)
        gross_losses = -sum(t["gross_return_pct"] for t in subset if t["gross_return_pct"] < 0)
        exit_reason_breakdown[reason] = {
            "trades": len(subset),
            "win_rate_net_pct": round(
                sum(t["net_return_pct"] > 0 for t in subset) / len(subset) * 100, 2
            ) if subset else 0.0,
            "gross_profit_sum_pct_points": round(gross_wins, 4),
            "gross_loss_sum_pct_points": round(gross_losses, 4),
            "gross_net_sum_pct_points": round(
                sum(t["gross_return_pct"] for t in subset), 4
            ),
            "net_sum_pct_points": round(
                sum(t["net_return_pct"] for t in subset), 4
            ),
            "average_gross_trade_return_pct": round(
                sum(t["gross_return_pct"] for t in subset) / len(subset), 4
            ) if subset else 0.0,
            "average_bars_held": round(
                sum(t["bars_held"] for t in subset) / len(subset), 2
            ) if subset else 0.0,
        }

    gross_winning_trades_sum = sum(t["gross_return_pct"] for t in trades if t["gross_return_pct"] > 0)
    gross_losing_trades_sum = -sum(t["gross_return_pct"] for t in trades if t["gross_return_pct"] < 0)
    average_bars_held = (
        sum(t["bars_held"] for t in trades) / len(trades) if trades else 0.0
    )

    first_close = candles[warmup]["close"]
    last_close = candles[-1]["close"]
    buy_hold_gross = (last_close / first_close - 1) * 100
    buy_hold_net = buy_hold_gross - ROUND_TRIP_COST_PCT

    return {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "exchange": "Bitget",
        "market": "USDT Futures",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME if timeframe_minutes == 5 else f"{timeframe_minutes}m",
        "strategy": "EMA 9/21 crossover events + EMA 200 trend filter + RSI entry confirmation + ATR stop/target",
        "history_days_requested": HISTORY_DAYS,
        "candles_tested": len(candles),
        "first_candle_utc": datetime.fromtimestamp(candles[0]["timestamp"] / 1000, timezone.utc).isoformat(),
        "last_candle_utc": datetime.fromtimestamp(candles[-1]["timestamp"] / 1000, timezone.utc).isoformat(),
        "estimated_taker_fee_per_side_pct": round(TAKER_FEE_PER_SIDE * 100, 4),
        "estimated_slippage_per_side_pct": round(SLIPPAGE_PER_SIDE * 100, 4),
        "estimated_round_trip_cost_pct": round(ROUND_TRIP_COST_PCT, 4),
        "strategy_parameters": {
            "ema_fast": EMA_FAST,
            "ema_slow": EMA_SLOW,
            "ema_trend_filter": EMA_TREND,
            "rsi_period": RSI_PERIOD,
            "long_rsi_range": "52 <= RSI < 68",
            "short_rsi_range": "32 < RSI <= 48",
            "stop_loss_atr_multiplier": STOP_ATR_MULTIPLIER,
            "take_profit_atr_multiplier": TAKE_PROFIT_ATR_MULTIPLIER,
            "max_holding_candles": max_hold_candles_for_run,
            "max_holding_minutes": max_hold_candles_for_run * timeframe_minutes,
            "position_sizing": "unleveraged; notional equals current equity",
        },
        "trades": len(trades),
        "winning_trades": wins,
        "losing_trades": len(trades) - wins,
        "win_rate_pct": round(wins / len(trades) * 100, 2) if trades else 0.0,
        "profit_factor": round(net_wins_sum / net_losses_sum, 4) if net_losses_sum else None,
        "performance_by_side": by_side,
        "cost_sensitivity": cost_sensitivity,
        "exit_reason_breakdown": exit_reason_breakdown,
        "gross_winning_trades_sum_pct_points": round(gross_winning_trades_sum, 4),
        "gross_losing_trades_sum_pct_points": round(gross_losing_trades_sum, 4),
        "average_bars_held": round(average_bars_held, 2),
        "average_holding_minutes": round(average_bars_held * timeframe_minutes, 2),
        "average_net_trade_return_pct": round(net_sum / len(trades), 4) if trades else 0.0,
        "gross_sum_of_trade_returns_pct": round(gross_sum, 4),
        "net_sum_of_trade_returns_pct": round(net_sum, 4),
        "estimated_cost_sum_pct_points": round(total_cost_pct_points, 4),
        "compounded_unleveraged_return_pct": round((equity - 1) * 100, 4),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        "buy_and_hold_gross_return_pct": round(buy_hold_gross, 4),
        "buy_and_hold_after_one_round_trip_cost_pct": round(buy_hold_net, 4),
        "recent_trades": trades[-10:],
        "trading_enabled": False,
        "warning": (
            "Historical simulation only. Fee and slippage values are estimates; funding is excluded. "
            "OHLC candles cannot reveal intrabar price order, so if stop and target are both touched "
            "the stop is assumed first. Entries are at the next candle open. Maximum drawdown includes "
            "conservative adverse intrabar mark-to-market for open positions. The 70/30 holdout is "
            "preliminary only; results do not predict future performance."
        ),
    }


def aggregate_candles(candles, source_minutes=5, target_minutes=15):
    """Aggregate closed source candles into aligned, complete target candles."""
    if target_minutes % source_minutes != 0:
        raise ValueError("Target timeframe must be a multiple of source timeframe.")
    ratio = target_minutes // source_minutes
    buckets = {}
    target_ms = target_minutes * 60 * 1000
    source_ms = source_minutes * 60 * 1000

    for candle in candles:
        bucket_start = (candle["timestamp"] // target_ms) * target_ms
        buckets.setdefault(bucket_start, []).append(candle)

    aggregated = []
    for bucket_start in sorted(buckets):
        group = sorted(buckets[bucket_start], key=lambda c: c["timestamp"])
        # Require every expected source candle and exact alignment. This avoids
        # creating a 15m candle from a partial group near data gaps.
        if len(group) != ratio:
            continue
        expected = [bucket_start + i * source_ms for i in range(ratio)]
        if [c["timestamp"] for c in group] != expected:
            continue
        aggregated.append({
            "timestamp": bucket_start,
            "open": group[0]["open"],
            "high": max(c["high"] for c in group),
            "low": min(c["low"] for c in group),
            "close": group[-1]["close"],
        })
    return aggregated


def compact_comparison(report):
    return {
        "timeframe": report["timeframe"],
        "candles_tested": report["candles_tested"],
        "trades": report["trades"],
        "win_rate_pct": report["win_rate_pct"],
        "profit_factor": report["profit_factor"],
        "compounded_unleveraged_return_pct": report["compounded_unleveraged_return_pct"],
        "max_drawdown_pct": report["max_drawdown_pct"],
        "average_holding_minutes": report["average_holding_minutes"],
        "exit_reason_breakdown": report["exit_reason_breakdown"],
        "validation": report.get("validation"),
    }


def main():
    candles = get_candles()
    report = run_backtest(candles, timeframe_minutes=5)

    # Compare a slower 15m version using only fully formed groups of three
    # 5m candles. The max holding horizon remains four hours (16 x 15m bars).
    candles_15m = aggregate_candles(candles, source_minutes=5, target_minutes=15)
    if len(candles_15m) < 500:
        raise RuntimeError(
            f"Only {len(candles_15m)} complete 15m candles available; "
            "cannot run a meaningful 15m comparison."
        )
    report_15m = run_backtest(candles_15m, timeframe_minutes=15)

    report["timeframe_comparison"] = {
        "purpose": "Diagnostic comparison only; no parameter optimization performed.",
        "holding_horizon_minutes": MAX_HOLD_CANDLES * 5,
        "5m": compact_comparison(report),
        "15m": compact_comparison(report_15m),
        "method_note": (
            "15m candles are aggregated from complete aligned groups of three 5m candles. "
            "EMA/RSI/ATR periods remain the same number of bars, so their time horizons differ. "
            "The max holding horizon is kept at four hours. Same estimated round-trip cost is "
            "applied per trade; actual execution costs can differ by timeframe and venue conditions."
        ),
    }

    # Chronological holdout for both timeframes: the final 30% is evaluated
    # separately, with indicator warmup recalculated on each holdout slice.
    def add_validation(target_report, target_candles, timeframe_minutes):
        split = int(len(target_candles) * 0.70)
        holdout = target_candles[split:]
        held_report = run_backtest(holdout, timeframe_minutes=timeframe_minutes)
        target_report["validation"] = {
            "method": "chronological 70/30 split; final 30% held out",
            "development_period_end_utc": datetime.fromtimestamp(
                target_candles[split - 1]["timestamp"] / 1000, timezone.utc
            ).isoformat(),
            "out_of_sample_start_utc": held_report["first_candle_utc"],
            "out_of_sample_end_utc": held_report["last_candle_utc"],
            "out_of_sample_candles_including_indicator_warmup": held_report["candles_tested"],
            "out_of_sample_trades": held_report["trades"],
            "out_of_sample_win_rate_pct": held_report["win_rate_pct"],
            "out_of_sample_profit_factor": held_report["profit_factor"],
            "out_of_sample_compounded_unleveraged_return_pct": held_report["compounded_unleveraged_return_pct"],
            "out_of_sample_max_drawdown_pct": held_report["max_drawdown_pct"],
            "out_of_sample_buy_and_hold_gross_return_pct": held_report["buy_and_hold_gross_return_pct"],
            "note": "Preliminary 30-day holdout only; not proof of robustness. Indicators are recalculated on the holdout slice."
        }
        return held_report

    add_validation(report, candles, timeframe_minutes=5)
    add_validation(report_15m, candles_15m, timeframe_minutes=15)
    report["timeframe_comparison"]["5m"] = compact_comparison(report)
    report["timeframe_comparison"]["15m"] = compact_comparison(report_15m)

    with open("backtest-report.json", "w", encoding="utf-8") as report_file:
        json.dump(report, report_file, indent=2, ensure_ascii=False)

    print("\n===== BITGET HISTORICAL BACKTEST (READ-ONLY) =====")
    print(json.dumps(report, indent=2, ensure_ascii=False))

    # Compact summary makes it easy to compare both timeframes without copying
    # the entire verbose report from GitHub Actions logs.
    summary = {
        "history_days_requested": HISTORY_DAYS,
        "history_start_utc": report["first_candle_utc"],
        "history_end_utc": report["last_candle_utc"],
        "estimated_round_trip_cost_pct": ROUND_TRIP_COST_PCT,
        "timeframes": {
            "5m": {
                "candles": report["candles_tested"],
                "trades": report["trades"],
                "win_rate_pct": report["win_rate_pct"],
                "profit_factor": report["profit_factor"],
                "compounded_unleveraged_return_pct": report["compounded_unleveraged_return_pct"],
                "max_drawdown_pct": report["max_drawdown_pct"],
                "validation": report.get("validation"),
            },
            "15m": report["timeframe_comparison"]["15m"],
        },
        "trading_enabled": False,
    }
    print("\\n===== COMPACT SUMMARY: COPY THIS BLOCK =====")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("\nSafety: this script only reads public market data. It does not place orders.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        raise
