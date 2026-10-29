# Cross-asset Trend Pullback findings

Updated: 2026-10-29

## Scope

The fixed-candidate run at `2026-10-28T22:18:04Z` covered 10 Bitget USDT-futures symbols on 15m and 1H candles (20 market/timeframe combinations), 90 days of history, four chronological non-overlapping test folds per combination, and three round-trip cost scenarios: 0.16%, 0.24%, and 0.32%. Entries are at the next candle open after a close-confirmed signal. Exits use a 1.5 ATR stop, 2 ATR target, and a four-hour holding cap. The shared simulator conservatively assumes stop-first when OHLC data touches both stop and target in one candle. Funding is excluded.

Parameters were declared before the test folds and were not optimized on them. These are averages of market-level fold returns, not a portfolio backtest.

## Results from the run before the compression-threshold correction

| Strategy | Mean market fold return at 0.16% costs | Mean market fold return at 0.32% costs | Positive folds at 0.32% | Total trades at 0.32% | Markets qualifying for paper |
|---|---:|---:|---:|---:|---:|
| EMA 9/21 baseline | -2.0581% | -4.1580% | 10 / 80 | 1,101 | 0 / 20 |
| Donchian 20 + EMA200 | -3.4279% | -7.1657% | 11 / 80 | 2,007 | 0 / 20 |
| Trend Pullback | -1.1090% | -2.4787% | 1 / 80 | 704 | 0 / 20 |
| Donchian after ATR compression | -2.1494% | -3.7087% | 2 / 80 | 810 | 0 / 20 |

The Trend Pullback candidate was least negative in this run, but it was positive in only one of 80 folds at twice the base estimated cost. No market/timeframe passed the predeclared gate (at least five trades in every fold and at least three of four folds with positive return and profit factor above 1 at 2x costs). This is not evidence of a reliable edge.

## Correctness issue found on 2026-10-29

Reviewing `volatility_squeeze_lab.py` showed that the compression threshold included the prior bar being classified in its own percentile reference window. This did not use future data, but it made the predeclared compression filter less cleanly defined than intended. The reference window has now been corrected to use the 100 ATR/close observations strictly before the classified bar, and a regression test asserts the exact window. The squeeze results in the table above are therefore **provisional and must not be treated as final** until the corrected workflow finishes. EMA, Donchian, and Trend Pullback figures are unaffected by this specific correction.

## Data and execution limitations

- Average missing candle intervals were approximately 0.4748%; the worst market/timeframe had 42 missing intervals (0.4862%), with a maximum gap of two candle intervals.
- Fees/slippage are estimates and actual fills can be worse; funding is excluded.
- OHLC candles cannot reconstruct intrabar path; stop-first handling is conservative but not a complete execution model.
- Results are exploratory and subject to selection bias from repeated strategy experiments. A fresh untouched forward period is still needed before any paper promotion.

## Safety

`trading_enabled: false`. No exchange orders or trading API keys are used. No candidate is promoted automatically. This report belongs to the research branch and is not merged into `main`.
