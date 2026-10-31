# DMI/ADX cross-asset findings

Updated: 2026-10-31

## Scope and execution

The completed read-only workflow (Actions run [41136283935](https://github.com/arturwarlord/bitget-ai-trader/actions/runs/41136283935), checked at 2026-10-30T22:14:18Z) evaluated the fixed DMI/ADX hypothesis against four fixed baselines:

- 10 Bitget USDT-futures symbols: BTC, ETH, SOL, XRP, BNB, DOGE, ADA, LINK, AVAX and LTC.
- 15m and 1H candles: 20 market/timeframe combinations.
- 90 days of closed candles per combination, split into four chronological non-overlapping OOS folds after the initial history period.
- 80 OOS folds per strategy, with the same candle snapshot, next-open entries and shared simulator within each market/timeframe.
- Round-trip cost scenarios: 0.16%, 0.24% and 0.32% (estimated taker fees plus slippage).
- Shared exits: 1.5 ATR stop, 2 ATR target, four-hour maximum hold; stop-first assumption if OHLC touches stop and target in one candle.

All 20 market/timeframe downloads completed. No market was skipped. Average missing-interval rate was 0.4748%; the worst combination had 42 missing intervals (0.4862%) and a maximum gap of two intervals. The data-quality validator allowed these small gaps but reported them.

## Fixed DMI/ADX hypothesis

- Long: +DI(14) crosses above -DI(14), ADX(14) >= 25, and close > EMA200.
- Short: -DI(14) crosses above +DI(14), ADX(14) >= 25, and close < EMA200.
- Signal is confirmed at candle close; entry is at the next candle open.
- Parameters were declared before evaluation and were not optimized on the test folds.

## Results

The table reports the **mean of each market/timeframe's four fold-average compounded returns, averaged equally across 20 combinations**. These are not portfolio returns and not one compounded 90-day return.

| Strategy | 0.16% costs | 0.24% costs | 0.32% costs | Raw positive OOS folds at 0.32% | Folds meeting return + PF>1 + >=5 trades | Trades at 0.32% | Median fold PF at 0.32% | Qualified market/timeframes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| EMA 9/21 | -2.0204% | -3.0765% | -4.1176% | 10/80 | 5/80 | 1,098 | 0.3618 | 0/20 |
| Donchian 20 + EMA200 | -3.7542% | -5.6225% | -7.4439% | 9/80 | 9/80 | 1,985 | 0.4254 | 0/20 |
| Trend Pullback | -1.1678% | -1.8639% | -2.5533% | 18/80 | 1/80 | 713 | 0.3914 | 0/20 |
| Donchian after ATR compression | -2.0657% | -2.8363% | -3.5989% | 8/80 | 2/80 | 795 | 0.2486 | 0/20 |
| **DMI/ADX 14, ADX>=25, EMA200** | **-0.5131%** | **-0.7710%** | **-1.0282%** | **19/80** | **3/80** | **260** | **0.1649** | **0/20** |

DMI/ADX had the least negative aggregate mean among these five candidates and the lowest trade count. However:
- Its aggregate return remained negative at all three cost levels.
- Only 19/80 folds had positive raw returns at 2x costs.
- Only 3/80 folds met all per-fold conditions (at least five trades, positive return and profit factor above 1).
- Median fold profit factor at 2x costs was only 0.1649.
- Its median number of trades per fold was 3, so many folds did not meet the minimum sample requirement.
- **0 of 20 market/timeframe combinations passed the full predeclared gate**: at least five trades in every fold and at least three of four folds with positive return and PF > 1 at 2x costs.

DMI/ADX was less negative on 1H than 15m (mean of market-level fold averages at 2x costs: -0.2920% vs -1.7643%), but this does not establish an edge. A few individual market/timeframe averages were positive, but they were based on only 6-18 trades over four folds and failed the per-fold consistency gate.

## Validation

- Complete unit-test suite: **50 tests passed** in the DMI research workflow.
- DMI/ADX workflow: [success](https://github.com/arturwarlord/bitget-ai-trader/actions/runs/41136283935).
- Unit-test workflow: [success](https://github.com/arturwarlord/bitget-ai-trader/actions/runs/41136283750).
- JSON artifact: [bitget-dmi-adx-cross-asset-report](https://github.com/arturwarlord/bitget-ai-trader/actions/runs/41136283935/artifacts/13016442636).

Tests cover Wilder warmup, directional dominance, ADX threshold, EMA200 direction, fixed parameters, and future-candle independence.

## Conclusion and limitations

**DMI/ADX is not qualified for forward paper.** It is the least negative aggregate candidate in this particular sample, not a profitable or validated strategy. Do not select DOGE/LINK/LTC 1H/15m subgroups from this same test set; doing so after inspecting the results would add selection bias.

The same 90-day window has now been reused across several hypotheses, so multiple-testing risk is material. Fees and slippage are estimates; funding is excluded; OHLC data cannot reconstruct the intrabar path; the stop-first rule is conservative but not a complete fill model. The equal-weighted fold averages do not model portfolio sizing, cross-market correlation or simultaneous exposure.

A meaningful next step is a predeclared volume-confirmed breakout test using actual candle volume and the same market universe, folds, exits and cost scenarios. It must be compared against the existing baselines and must not be promoted automatically.

## Safety

Read-only public market data only. No exchange orders or trading API keys. `trading_enabled: false`. No strategy is promoted automatically. This report is on the research branch, not main.
