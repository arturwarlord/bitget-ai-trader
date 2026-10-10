# Bitget AI Trader

A step-by-step crypto trading bot project. The current version is **read-only**: it fetches public BTCUSDT USDT-Futures candles from Bitget and evaluates technical-analysis strategies.

## Current features

- Market: Bitget USDT Futures, BTCUSDT
- Market analysis: EMA 9, EMA 21, RSI 14
- Historical backtest: EMA 9/21 crossover, EMA 200 trend filter, RSI confirmation, ATR stop-loss/take-profit
- Historical data: up to 90 days of closed 5-minute candles
- Diagnostic comparison: 5m and aggregated 15m candles
- Strategy Lab: predefined EMA/ATR parameter grid ranked on the first 70% of data, with the final 30% held out for evaluation
- GitHub Actions: manual workflows for historical backtest and Strategy Lab
- No Bitget API keys required for current read-only tests
- **No orders are placed; trading is disabled**

## Run historical backtest

1. Open the **Actions** tab.
2. Select **Bitget Historical Backtest**.
3. Click **Run workflow**, then confirm.
4. Open the new run and expand **Backtest BTCUSDT strategy**.
5. The full report is also saved as a downloadable artifact.

## Run Strategy Lab

1. Open the **Actions** tab.
2. Select **Bitget Strategy Lab**.
3. Click **Run workflow**, then confirm.
4. Open the run and expand **Compare strategy parameters (read-only)**.
5. Review the compact results in the logs and download the **bitget-strategy-lab-report** artifact for the complete JSON report.

Strategy Lab compares a predefined set of EMA and ATR settings using the first 70% of the historical candles, then evaluates only the best development-period candidates on the final 30%. The baseline strategy is also included for comparison. The holdout is not used to rank candidates.

## Important risk notes

- A successful GitHub Actions run means the code executed; it does **not** mean the strategy is profitable.
- Historical performance does not predict future results. A single holdout period is not proof of robustness.
- Fee and slippage assumptions are estimates; funding is excluded.
- OHLC candles cannot reveal the exact intrabar price path. The backtest assumes the stop is hit first when stop and target are both touched.
- Do not enable live trading based on one backtest. Further walk-forward validation and demo-only testing are required first.
- Never put exchange API keys in source files or commit them to Git. If demo credentials are added later, store them in GitHub Actions Secrets and use demo-only permissions.

## Planned next steps

1. Compare the current baseline with the predefined Strategy Lab candidates.
2. Review out-of-sample results and avoid selecting a strategy based only on in-sample returns.
3. Improve validation with additional chronological walk-forward periods.
4. Only after sufficient testing, consider demo-only execution with strict risk limits. Live trading remains disabled.
