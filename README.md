# Bitget AI Trader

A step-by-step crypto trading bot project. The current version is **read-only**: it fetches public BTCUSDT USDT-Futures candles from Bitget and calculates EMA 9, EMA 21, and RSI 14.

## Current features

- Market: Bitget USDT Futures, BTCUSDT
- Candle timeframe: 5 minutes
- Indicators: EMA 9, EMA 21, RSI 14
- Output: latest price, indicator values, and an informational market bias
- GitHub Actions: manual run and scheduled run every 15 minutes
- **No API keys required**
- **No orders are placed; trading is disabled**

## Run in GitHub

1. Open the **Actions** tab.
2. Select **Bitget Market Analysis**.
3. Click **Run workflow**, then confirm with **Run workflow**.
4. Open the new run and expand **Run read-only market analysis** to view the output.

The workflow uses public market data only. It does not need Bitget API credentials or GitHub Secrets.

## Important

Signals are basic technical indicators, not financial advice and not a guarantee of future results. This starter does not place trades. Do not add exchange secrets to source files or commit them to Git.

## Planned next steps

1. Verify the workflow runs successfully.
2. Save analysis history and improve signal validation.
3. Configure Bitget Demo API credentials through GitHub Actions Secrets only when needed.
4. Add risk limits and demo-only order execution after explicit testing.
