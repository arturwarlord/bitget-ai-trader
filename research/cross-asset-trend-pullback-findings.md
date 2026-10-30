# Cross-asset Trend Pullback findings

Updated: 2026-10-30

## Scope

The corrected fixed-candidate run (Actions run 40957196280, checked at 2026-10-29T21:43:25Z) covered 10 Bitget USDT-futures symbols on 15m and 1H candles: 20 market/timeframe combinations, 90 days of history, four chronological non-overlapping test folds per combination, and three round-trip cost scenarios: 0.16%, 0.24%, and 0.32%. This produces 80 out-of-sample fold observations per strategy.

Entries are at the next candle open after a close-confirmed signal. Exits use a 1.5 ATR stop, 2 ATR target, and a four-hour holding cap. The shared simulator conservatively assumes stop-first when OHLC data touches both stop and target in one candle. Funding is excluded.

Parameters were declared before the test folds and were not optimized on them. The figures below are simple averages of the 80 fold-level compounded returns, not a combined portfolio backtest and not a single compounded 90-day return.

## Corrected walk-forward results

| Strategy | Mean fold return at 0.16% costs | Mean fold return at 0.32% costs | Positive folds at 0.32% | Trades at 0.32% | Market/timeframes passing gate |
|---|---:|---:|---:|---:|---:|
| EMA 9/21 baseline | -2.0977% | -4.1794% | 8 / 80 | 1,092 | 0 / 20 |
| Donchian 20 + EMA200 | -3.1760% | -6.9005% | 10 / 80 | 1,994 | 0 / 20 |
| Trend Pullback | **-1.0910%** | **-2.4847%** | 1 / 80 | 717 | 0 / 20 |
| Donchian after ATR compression | -1.8000% | -3.3583% | 2 / 80 | 807 | 0 / 20 |

The Trend Pullback candidate had the least negative average return, but only one of 80 folds was positive at twice the base estimated cost. The ATR-compression candidate also remained negative on average and was positive in only two folds. No market/timeframe passed the predeclared gate: at least five trades in every fold and at least three of four folds with positive return and profit factor above 1 at 2x costs.

The corrected ATR-compression threshold uses the 100 ATR/close observations strictly before the bar being classified. The classification observation itself is excluded from its reference percentile window. The regression test asserts the exact 100-value window, and the corrected workflow completed successfully.

**Conclusion:** none of the four candidates has demonstrated a robust edge. The lower losses of Trend Pullback and ATR-compression are relative comparisons, not evidence of profitability. None is promoted to forward paper.

## Validation and artifacts

- Full multi-asset research run: [Actions run 40957196280](https://github.com/arturwarlord/bitget-ai-trader/actions/runs/40957196280)
- Correctness tests: [Actions run 40957196224](https://github.com/arturwarlord/bitget-ai-trader/actions/runs/40957196224)
- Cross-asset report artifact: [market universe report](https://github.com/arturwarlord/bitget-ai-trader/actions/runs/40957196280/artifacts/12934419423)
- Walk-forward report artifact: [four-strategy walk-forward report](https://github.com/arturwarlord/bitget-ai-trader/actions/runs/40957196280/artifacts/12935666320)
- Corrected ATR-compression report artifact: [volatility-squeeze report](https://github.com/arturwarlord/bitget-ai-trader/actions/runs/40957196280/artifacts/12935336902)

## Data and execution limitations

- The market-universe snapshot contained 20 successful market/timeframe downloads and no skipped markets. The average missing-interval rate was approximately 0.475%; the worst market/timeframe had 42 missing intervals (0.4862%), with a maximum gap of two candle intervals.
- Fees/slippage are estimates and actual fills can be worse; funding is excluded.
- OHLC candles cannot reconstruct intrabar path; stop-first handling is conservative but is not a complete execution model.
- The same 90-day historical period has been inspected repeatedly across many hypotheses. Multiple-testing/selection bias remains a material risk. A fresh untouched forward period is needed before any candidate could be considered for paper promotion.
- Fold averages weight each market/timeframe fold equally; they do not model capital allocation, cross-market correlation, portfolio exposure, or concurrent positions.

## Safety

trading_enabled: false. No exchange orders or trading API keys are used. No candidate is promoted automatically. This report belongs to the research branch and is not merged into main.
