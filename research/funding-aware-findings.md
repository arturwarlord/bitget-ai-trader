# Funding-aware cross-asset findings — preliminary run

Date: 2026-11-02 UTC

## Completed run

GitHub Actions run: https://github.com/arturwarlord/bitget-ai-trader/actions/runs/41533797113

Artifact: https://github.com/arturwarlord/bitget-ai-trader/actions/runs/41533797113/artifacts/13184565209

- 10 USDT-Futures symbols.
- 15m and 1H: all 20 market/timeframe combinations completed; no skipped markets.
- 180 settled funding records per symbol were retrieved from two public API pages (1,800 records total).
- Four chronological OOS folds per market/timeframe.
- Fee/slippage scenarios: 0.16%, 0.24%, 0.32% round trip; actual funding applied separately.
- The complete unit-test suite passed (89 tests on this run's commit).

## Mean fold-return comparison

Values are equal-weighted averages across 20 market/timeframe combinations of each combination's four OOS fold returns. They are not portfolio returns.

| Strategy | No funding, base cost | With funding, base cost | No funding, 2x cost | With funding, 2x cost |
|---|---:|---:|---:|---:|
| EMA 9/21 | -2.268905% | -2.269032% | -4.430944% | -4.431087% |
| Donchian Breakout | -3.807371% | -3.815543% | -7.431113% | -7.438892% |
| Trend Pullback | -1.121338% | -1.121406% | -2.592669% | -2.592731% |
| ATR Compression + Donchian | -2.061249% | -2.063879% | -3.595228% | -3.597800% |
| DMI/ADX + EMA200 | -0.516894% | -0.517113% | -1.021659% | -1.021868% |
| Volume-confirmed Donchian | -2.659899% | -2.663397% | -5.447312% | -5.450706% |

Funding changed average returns only slightly in this sample. The larger drag remains estimated fee/slippage and the underlying entry/exit performance. All six strategies remain negative at base and doubled costs.

## Candidate-gate correction

The preliminary report marked one combination as a paper candidate: ADAUSDT 1H EMA 9/21. Its mean OOS return after funding at 2x cost was -0.007802%, despite three qualifying folds. This exposed a flaw in the old gate: it counted three positive folds but did not require the mean of all four folds to be positive.

The research branch now requires:
- minimum trade count in every fold;
- at least three positive folds with PF > 1;
- positive arithmetic mean OOS return across all four folds.

The ADAUSDT 1H EMA result therefore must not be treated as a candidate. A corrected full run is queued on the latest commit; its candidate count supersedes the preliminary flag above.

## Limitations

- The price history is the same rolling 90-day sample already examined by multiple hypotheses; this is not independent future evidence.
- Funding is actual settled history, but fee/slippage remain estimates.
- Funding-adjusted drawdown is a realized trade-sequence metric. The simulator's intrabar mark-to-market drawdown is reported separately and is not fully replayed with funding cash flows at each intrabar mark.
- Intrabar stop/target exits conservatively include funding through the exit candle close.
- No API keys or orders are used; trading remains disabled.
