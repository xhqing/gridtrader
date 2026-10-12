<p align="center">
  <img src="assets/logo.svg" width="320" alt="gridtrader logo" />
</p>

<h1 align="center">gridtrader — Grid-Trading Strategy Toolset</h1>

<p align="center">
  <a href="LICENSE.md"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT" /></a>
  <img src="https://img.shields.io/badge/Version-0.1.0-blue.svg" alt="Version: 0.1.0" />
  <img src="https://img.shields.io/badge/Type-Quant%20Tool-7C3AED.svg" alt="Type: Quant Tool" />
</p>

<p align="center">🌐 <a href="README_cn.md">简体中文</a></p>

**gridtrader** is a grid-trading strategy development and backtesting toolset built on [backtrader](https://github.com/mementum/backtrader): it fetches A-share daily-K data via baostock (`get_data_scripts/get_data.py`), runs grid backtests from CSV data (`main.py`'s `gridrun()` plus the `GridStrategy` in `gridtrader.py`), and ships sample datasets under `data/` (CSI 300 index, a single A-share, and a segmented Binance series). Dependencies are managed with Pipenv.

> A sub-project of [QuantStrategistAgent](https://github.com/xhqing/QuantStrategistAgent) (Markowitz), the quant-strategy agent of the xhqing AI agent team. Research tooling only: **not for live trading, not financial advice.**

## How it works

`GridStrategy` derives a mid price from the highest/lowest range of the past 1440 bars and builds 11 price levels spaced 0.5% apart across ±2.5% around that mid. Each level maps to a target position (lightest at the top, fully invested at the bottom); whenever the close crosses a level, the strategy re-balances to that level's target — classic grid logic (buy the dips, sell the rips), implemented with `order_target_percent`.

## Quick start

Requires Python (the project declares 3.7) and Pipenv:

```bash
pip install pipenv
pipenv install
pipenv run python main.py
```

All settings live in the `__main__` block of `main.py`:

- `filename` — input CSV path (default `./data/binance-segment.csv`);
- `timeframe` — the data's backtrader timeframe (default `Minutes`; the dict maps index → name);
- `all_cash` — starting cash (default `100000.00`).

The CSV must have exactly the columns `['time', 'open', 'high', 'low', 'close', 'volume']` in that order (pandas CSV format, `time` parsed as datetimes).

The run prints the starting and final portfolio value.

## Repository layout

```
gridtrader/
├── main.py                  # Backtest entry: gridrun() + all settings
├── gridtrader.py            # GridStrategy (the backtrader strategy) implementation
├── get_data_scripts/
│   └── get_data.py          # Sample A-share daily-K fetcher (baostock)
├── data/                    # Sample datasets (CSV): CSI 300, one A-share, segmented Binance
└── Pipfile / Pipfile.lock   # Dependencies (pandas / backtrader / baostock)
```

## License & attribution

This project is released under the MIT License, and you are additionally asked to **credit the author and cite the source** whenever you use, redistribute, or build upon it:

- **Author:** All Contributors
- **Project:** gridtrader — grid-trading strategy development & backtesting toolset (QuantStrategistAgent sub-project)
- **Main repository:** https://github.com/xhqing/QuantStrategistAgent

If you reference code or derive from this repository, please retain this attribution in your documentation, README, or acknowledgements.
