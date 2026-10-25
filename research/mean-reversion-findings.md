# Bollinger low-ADX mean-reversion hypothesis — research result

**Assessment date:** 2026-10-25  
**Source comparison run:** [GitHub Actions run 40239047059](https://github.com/arturwarlord/bitget-ai-trader/actions/runs/40239047059)  
**Code commit evaluated:** [83fda8fd939b31147743f69b3712bc8a2f09279e](https://github.com/arturwarlord/bitget-ai-trader/commit/83fda8fd939b31147743f69b3712bc8a2f09279e)  
**Research PR:** [#4 — Bollinger mean-reversion entries with low-ADX filter](https://github.com/arturwarlord/bitget-ai-trader/pull/4)

## Hypothesis fixed before evaluation

- Long entry: the closed candle crosses below the lower 20-bar Bollinger band (2 standard deviations), RSI(14) <= 30, and ADX(14) < 20.
- Short entry: the closed candle crosses above the upper band, RSI(14) >= 70, and ADX(14) < 20.
- Entry is at the next candle open. The shared simulator applies a 1.5 ATR stop, 2.0 ATR target, a four-hour maximum holding time, unleveraged compounding, and the same execution model as the other candidates.
- No parameters were optimized against out-of-sample folds.

## Data and validation

- Instrument: BTCUSDT USDT futures, public closed candles only.
- Raw sample: 25,791 five-minute candles, from 2026-07-27 22:15 UTC through 2026-10-25 22:05 UTC.
- Fifteen-minute sample: 8,511 complete aligned candles aggregated from the same five-minute snapshot, through 2026-10-25 21:45 UTC.
- Six chronological, non-overlapping test folds cover the final 60% of each sample; the initial 40% is available for indicator warmup.
- Round-trip cost scenarios: 0.16%, 0.20%, and 0.24%. The base estimate is 0.06% taker fee per side plus 0.02% slippage per side.
- CI completed successfully: 22 unit tests passed, including future-candle independence and the new RSI/ADX gating test. The comparison workflow produced JSON artifacts.

## Base-cost comparison

Mean compounded return across the six out-of-sample folds, at 0.16% round-trip cost. A fold with no trades contributes 0% return; therefore low-turnover candidates can appear less negative without demonstrating an edge.

| Candidate | 5m mean fold return | 5m profitable folds | 5m trades | 15m mean fold return | 15m profitable folds | 15m trades |
|---|---:|---:|---:|---:|---:|---:|
| EMA 9/21 | -10.2835% | 0/6 | 376 | -2.3961% | 0/6 | 112 |
| EMA 12/30 | -8.2924% | 0/6 | 285 | -2.3935% | 0/6 | 85 |
| Donchian 20 | -17.8030% | 0/6 | 649 | -5.4755% | 0/6 | 193 |
| Trend Pullback | -6.0120% | 0/6 | 230 | -1.5582% | 0/6 | 74 |
| Donchian + ADX >= 20 | -13.1249% | 0/6 | 456 | -3.3217% | 0/6 | 125 |
| **Bollinger mean-reversion entry** | **-0.4468%** | **0/6** | **11** | **-0.1561%** | **1/6** | **5** |

## Cost sensitivity

Mean compounded return across the same six test folds:

### Five-minute candles

| Candidate | 0.16% round trip | 0.20% round trip | 0.24% round trip |
|---|---:|---:|---:|
| EMA 9/21 | -10.2835% | -12.5052% | -14.6724% |
| EMA 12/30 | -8.2924% | -10.0203% | -11.7159% |
| Donchian 20 | -17.8030% | -21.2862% | -24.6227% |
| Trend Pullback | -6.0120% | -7.4429% | -8.8522% |
| Donchian + ADX >= 20 | -13.1249% | -15.7278% | -18.2533% |
| Bollinger mean-reversion entry | -0.4468% | -0.5198% | -0.5927% |

### Fifteen-minute candles

| Candidate | 0.16% round trip | 0.20% round trip | 0.24% round trip |
|---|---:|---:|---:|
| EMA 9/21 | -2.3961% | -3.1218% | -3.8424% |
| EMA 12/30 | -2.3935% | -2.9456% | -3.4947% |
| Donchian 20 | -5.4755% | -6.6857% | -7.8809% |
| Trend Pullback | -1.5582% | -2.0428% | -2.5250% |
| Donchian + ADX >= 20 | -3.3217% | -4.1256% | -4.9231% |
| Bollinger mean-reversion entry | -0.1561% | -0.1894% | -0.2228% |

## Fold-level diagnosis for the new hypothesis

At the base 0.16% cost:

- **5m:** trades by fold = 1, 3, 4, 2, 0, 1; returns = -0.0055%, -1.5183%, -0.4530%, -0.4019%, 0%, -0.3021%.
- **15m:** trades by fold = 1, 0, 0, 2, 1, 1; returns = -0.7709%, 0%, 0%, +0.4444%, -0.3039%, -0.3061%.

The 15m result has one positive fold but only five trades total. The apparently smaller average loss is largely explained by not trading in several folds. It is not evidence of a stable, statistically meaningful edge. Higher cost scenarios worsen the mean result.

## Conclusion and next step

**The hypothesis has not demonstrated profitability and is rejected as a proven edge.** The small number of trades makes its true expectancy uncertain, particularly on 15m. Do not promote it to paper/live selection and do not optimize its parameters on these already-viewed folds.

Next research should be a separately predeclared hypothesis and, where possible, an untouched later period or longer independent sample. Any future comparison must retain the same execution engine, chronological validation, costs, and look-ahead tests.

## Limitations and safety

- Fees and slippage are estimates, not a verified account-specific fee schedule.
- Funding is excluded.
- OHLC candles cannot reveal intrabar price order; the simulator assumes the stop is hit first if stop and target are both touched.
- This is historical simulation only. No orders are placed; trading remains disabled.
