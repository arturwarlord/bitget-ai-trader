"""Read-only Bitget USDT-Futures historical funding data and cash-flow helpers."""

import json
import math
import statistics
import time
import urllib.parse
import urllib.request

BASE_URL = "https://api.bitget.com"
PRODUCT_TYPE = "USDT-FUTURES"
PAGE_SIZE = 100
MAX_PAGES = 12
PAGE_DELAY_SECONDS = 0.10
MAX_GAP_MULTIPLE = 2.5


def parse_funding_payload(payload, expected_symbol):
    """Validate and normalize one Bitget v2 history-fund-rate page."""
    if not isinstance(payload, dict) or payload.get("code") != "00000":
        message = payload.get("msg", "invalid response") if isinstance(payload, dict) else "invalid response"
        raise RuntimeError(f"Bitget funding API error: {message}")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ValueError("funding API data must be a list")

    records = []
    for row in rows:
        if not isinstance(row, dict) or row.get("symbol") != expected_symbol:
            raise ValueError("funding record symbol does not match requested symbol")
        try:
            timestamp = int(row["fundingTime"])
            rate = float(row["fundingRate"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("funding record has invalid timestamp or rate") from exc
        if timestamp <= 0 or not math.isfinite(rate):
            raise ValueError("funding timestamp must be positive and rate finite")
        records.append({
            "symbol": expected_symbol,
            "funding_timestamp_ms": timestamp,
            "funding_rate": rate,
        })
    return sorted(records, key=lambda item: item["funding_timestamp_ms"])


def request_funding_page(symbol, page_no, page_size=PAGE_SIZE):
    params = {
        "symbol": symbol,
        "productType": PRODUCT_TYPE,
        "pageSize": str(page_size),
        "pageNo": str(page_no),
    }
    url = f"{BASE_URL}/api/v2/mix/market/history-fund-rate?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "bitget-ai-trader-funding-research/0.1",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return parse_funding_payload(payload, symbol)


def fetch_funding_history(
    symbol,
    start_ms,
    end_ms,
    page_fetcher=request_funding_page,
    sleep_fn=time.sleep,
    page_size=PAGE_SIZE,
    max_pages=MAX_PAGES,
):
    """Fetch enough pages to cover [start_ms, end_ms], failing on stalled pagination."""
    if not symbol or int(start_ms) <= 0 or int(end_ms) <= int(start_ms):
        raise ValueError("symbol and a valid increasing time range are required")

    collected = {}
    previous_oldest = None
    pages_fetched = 0
    reached_start = False

    if not isinstance(page_size, int) or page_size < 1:
        raise ValueError("page_size must be positive")
    if not isinstance(max_pages, int) or max_pages < 1:
        raise ValueError("max_pages must be positive")

    for page_no in range(1, max_pages + 1):
        batch = page_fetcher(symbol, page_no, page_size)
        pages_fetched += 1
        if not batch:
            break

        for record in batch:
            timestamp = int(record["funding_timestamp_ms"])
            rate = float(record["funding_rate"])
            if not math.isfinite(rate):
                raise ValueError("funding rate must be finite")
            previous = collected.get(timestamp)
            if previous is not None and previous != rate:
                raise ValueError("conflicting funding rates for the same settlement timestamp")
            collected[timestamp] = rate

        oldest = min(int(record["funding_timestamp_ms"]) for record in batch)
        if previous_oldest is not None and oldest >= previous_oldest:
            raise RuntimeError("Bitget funding pagination did not move backwards")
        if oldest <= int(start_ms):
            reached_start = True
            break
        if len(batch) < page_size:
            break
        previous_oldest = oldest
        sleep_fn(PAGE_DELAY_SECONDS)

    records = [
        {
            "symbol": symbol,
            "funding_timestamp_ms": timestamp,
            "funding_rate": rate,
        }
        for timestamp, rate in sorted(collected.items())
        if int(start_ms) <= timestamp <= int(end_ms)
    ]
    if not reached_start and not records:
        raise RuntimeError(f"No funding records returned for {symbol} in requested window")
    return {
        "symbol": symbol,
        "records": records,
        "pages_fetched": pages_fetched,
        "reached_start_boundary": reached_start,
        "requested_start_ms": int(start_ms),
        "requested_end_ms": int(end_ms),
    }


def validate_funding_coverage(records, start_ms, end_ms):
    """Fail closed on sparse or truncated settlement history."""
    selected = sorted(
        (
            int(item["funding_timestamp_ms"])
            for item in records
            if int(start_ms) <= int(item["funding_timestamp_ms"]) <= int(end_ms)
        )
    )
    if len(selected) < 3:
        raise RuntimeError("At least three funding settlements are required to validate coverage")

    gaps = [right - left for left, right in zip(selected, selected[1:])]
    median_gap = statistics.median(gaps)
    if median_gap <= 0:
        raise RuntimeError("Funding settlement timestamps are not strictly increasing")
    max_gap = max(gaps)
    boundary_limit = median_gap * MAX_GAP_MULTIPLE
    if max_gap > boundary_limit:
        raise RuntimeError(
            f"Funding history has a gap of {max_gap / 3600000:.2f}h; "
            f"median interval is {median_gap / 3600000:.2f}h"
        )
    if selected[0] - int(start_ms) > boundary_limit:
        raise RuntimeError("Funding history starts too late for the OOS window")
    if int(end_ms) - selected[-1] > boundary_limit:
        raise RuntimeError("Funding history ends too early for the OOS window")

    return {
        "settlements": len(selected),
        "median_interval_hours": round(median_gap / 3600000, 4),
        "max_gap_hours": round(max_gap / 3600000, 4),
        "first_settlement_ms": selected[0],
        "last_settlement_ms": selected[-1],
    }


def funding_cost_pct(side, entry_timestamp_ms, exit_timestamp_ms, records):
    """Return funding cost as % of position notional.

    Positive funding means longs pay and shorts receive. A settlement exactly at
    entry is excluded (the position was not open before settlement); a settlement
    at the conservative exit bound is included.
    """
    if side in ("LONG", 1):
        direction = 1
    elif side in ("SHORT", -1):
        direction = -1
    else:
        raise ValueError("side must be LONG/1 or SHORT/-1")

    entry = int(entry_timestamp_ms)
    exit_time = int(exit_timestamp_ms)
    if entry <= 0 or exit_time <= entry:
        raise ValueError("trade timestamps must define a positive holding interval")

    applicable = [
        float(item["funding_rate"])
        for item in records
        if entry < int(item["funding_timestamp_ms"]) <= exit_time
    ]
    if any(not math.isfinite(value) for value in applicable):
        raise ValueError("funding rates must be finite")
    return direction * sum(applicable) * 100.0, len(applicable)


def funding_adjusted_trade_metrics(backtest_report, funding_records):
    """Recompute trade-level performance after settled funding cash flows.

    Drawdown here is realized trade-sequence drawdown. It does not replace the
    simulator's intrabar mark-to-market drawdown because funding is not replayed
    against every intrabar mark.
    """
    trades = backtest_report.get("trade_history")
    if not isinstance(trades, list):
        raise ValueError("backtest report must include trade_history")

    adjusted_returns = []
    total_funding_cost = 0.0
    funding_events = 0
    per_trade = []

    for trade in trades:
        cost, event_count = funding_cost_pct(
            trade["side"],
            trade["entry_timestamp_ms"],
            trade["funding_exit_timestamp_ms"],
            funding_records,
        )
        net_before_funding = (
            float(trade["gross_return_pct"]) - float(trade["estimated_cost_pct"])
        )
        adjusted = net_before_funding - cost
        adjusted_returns.append(adjusted)
        total_funding_cost += cost
        funding_events += event_count
        per_trade.append({
            "side": trade["side"],
            "entry_timestamp_ms": int(trade["entry_timestamp_ms"]),
            "funding_exit_timestamp_ms": int(trade["funding_exit_timestamp_ms"]),
            "funding_events": event_count,
            "funding_cost_pct_of_position": round(cost, 8),
            "net_return_after_funding_pct": round(adjusted, 8),
        })

    positive = sum(value for value in adjusted_returns if value > 0)
    negative = -sum(value for value in adjusted_returns if value < 0)
    equity = 1.0
    peak = 1.0
    realized_drawdown = 0.0
    for value in adjusted_returns:
        equity *= max(0.0, 1.0 + value / 100.0)
        peak = max(peak, equity)
        if peak > 0:
            realized_drawdown = max(realized_drawdown, (peak - equity) / peak * 100.0)

    return {
        "trades": len(adjusted_returns),
        "win_rate_pct": round(
            sum(value > 0 for value in adjusted_returns) / len(adjusted_returns) * 100.0,
            2,
        ) if adjusted_returns else 0.0,
        "profit_factor": round(positive / negative, 6) if negative else None,
        "compounded_return_pct": round((equity - 1.0) * 100.0, 6),
        "realized_trade_sequence_max_drawdown_pct": round(realized_drawdown, 6),
        "simulator_pre_funding_mark_to_market_max_drawdown_pct": backtest_report.get(
            "max_drawdown_pct"
        ),
        "funding_cost_sum_pct_points": round(total_funding_cost, 8),
        "funding_events": funding_events,
        "mean_funding_cost_per_trade_pct": round(
            total_funding_cost / len(adjusted_returns), 8
        ) if adjusted_returns else 0.0,
        "trade_details": per_trade,
    }
