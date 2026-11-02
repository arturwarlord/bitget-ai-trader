# Funding-aware cross-asset validation

## Purpose

The previous cross-asset reports charged estimated taker fees and slippage but excluded perpetual-futures funding. This experiment replays settled Bitget funding cash flows against the same six fixed per-symbol strategies:

- EMA 9/21
- Donchian 20 + EMA200
- Trend Pullback
- Donchian after prior-bar ATR compression
- DMI/ADX + EMA200
- Volume-confirmed Donchian

It is a cost-accounting extension, not a newly optimized entry strategy.

## Fixed validation design

- Universe: the same ten USDT-Futures symbols used by the cross-asset research.
- Timeframes: 15 minutes and 1 hour.
- History: the existing rolling 90-day closed-candle snapshot.
- Split: first 50% initial history; remaining observations in four chronological, non-overlapping OOS folds.
- Execution: close-confirmed signal, entry at next candle open.
- Shared exits: 1.5 ATR stop, 2 ATR target, four-hour maximum hold.
- Estimated round-trip fee/slippage scenarios: 0.16%, 0.24%, 0.32%.
- Historical funding is charged separately from fee/slippage and is not used to generate entry signals.
- Parameters are fixed; no tuning on test folds.

## Funding source and accounting

The workflow uses Bitget's public v2 endpoint:

GET /api/v2/mix/market/history-fund-rate

Official documentation: https://www.bitget.com/api-doc/classic/contract/market/Get-History-Funding-Rate

The request uses productType=USDT-FUTURES, pageSize=100, and pageNo pagination. The API returns fundingRate and fundingTime for each settled funding event. The research fetches a 60-day window, validates coverage and settlement cadence per symbol/timeframe, and fails closed for incomplete histories.

For a position with direction d (+1 long, -1 short), a settled rate r produces a funding cost on position notional of:

funding_cost_pct = d * r * 100

Thus a positive rate is a cost to longs and a credit to shorts; a negative rate reverses the cash flow. Funding is applied only when:

entry_timestamp < funding_timestamp <= conservative_exit_timestamp

A settlement exactly at entry is excluded because the position was not open before settlement. A settlement at the exit bound is included. For intrabar stop/target exits, OHLC data cannot reveal the exact exit time, so the funding exposure is conservatively extended to the exit candle close. Open-executed signal/gap exits use their exact candle-open timestamp.

## Metrics and limitations

- The funding-adjusted compounded return, win rate and profit factor are recomputed from the complete trade history.
- The report shows funding event counts and funding cash-flow percentage points.
- It reports the original simulator's pre-funding intrabar mark-to-market drawdown separately from the funding-adjusted realized trade-sequence drawdown. The latter does not reconstruct funding at every intrabar mark and must not be mistaken for a fully funding-adjusted intrabar drawdown.
- Taker fees and slippage remain estimates; actual account tier, spread, market impact and execution may differ.
- Funding intervals vary by symbol. Coverage is validated from the observed settlement timestamps; a market is skipped if history is sparse or truncated.
- OHLC stop/target ordering remains conservative (stop first when both are touched).
- The same rolling 90-day price history has already been examined across several hypotheses. This is not an independent future sample; multiple-testing bias remains material.
- Funding history is public and settled. No API keys or exchange orders are used.

## Candidate gate

A market/timeframe/strategy combination is only marked as a paper candidate if, after funding and at 2x estimated fee/slippage, every OOS fold has at least five trades and at least three of four folds have positive compounded return and profit factor above 1. No candidate is promoted automatically.

trading_enabled remains false.
