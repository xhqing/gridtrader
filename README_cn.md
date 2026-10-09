<p align="center">
  <img src="assets/logo.svg" width="320" alt="gridtrader logo" />
</p>

<h1 align="center">gridtrader —— 网格交易策略工具集</h1>

<p align="center">
  <a href="LICENSE.md"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT" /></a>
  <img src="https://img.shields.io/badge/Version-0.1.0-blue.svg" alt="Version: 0.1.0" />
  <img src="https://img.shields.io/badge/Type-Quant%20Tool-7C3AED.svg" alt="Type: Quant Tool" />
</p>

<p align="center">🌐 <a href="README.md">English</a></p>

**gridtrader** 是基于 [backtrader](https://github.com/mementum/backtrader) 的网格交易策略开发与回测工具集：用 baostock 拉 A 股日 K（`get_data_scripts/get_data.py`），从 CSV 数据跑网格回测（`main.py` 的 `gridrun()` + `gridtrader.py` 的 `GridStrategy`），`data/` 下附样例数据集（沪深 300 指数、单只 A 股、币安分段序列）。依赖用 Pipenv 管理。

> 本项目是 [QuantStrategistAgent](https://github.com/xhqing/QuantStrategistAgent)（Markowitz）的子项目——xhqing AI agent 团队的量化工作线。仅为研究工具：**不用于实盘、不构成投资建议。**

## 工作原理

`GridStrategy` 用过去 1440 根 K 线的最高价 / 最低价推出中轨价，在中轨上下 ±2.5% 范围按 0.5% 间距铺 11 个价格档位。每个档位对应一个目标仓位（顶部最轻、底部满仓）；收盘价每当越过一个档位，就把仓位调回该档位对应的目标——经典网格逻辑（跌买涨卖），用 `order_target_percent` 实现。

## 快速开始

需要 Python（项目声明 3.7）与 Pipenv：

```bash
pip install pipenv
pipenv install
pipenv run python main.py
```

全部设置都在 `main.py` 的 `__main__` 块里：

- `filename` —— 输入 CSV 路径（默认 `./data/binance-segment.csv`）；
- `timeframe` —— 数据的 backtrader 周期（默认 `Minutes`；字典给出索引 → 名称映射）；
- `all_cash` —— 起始资金（默认 `100000.00`）。

CSV 必须严格按顺序包含这些列：`['time', 'open', 'high', 'low', 'close', 'volume']`（pandas CSV 格式，`time` 解析为日期时间列）。

运行后会打印起始与期末账户价值。

## 目录结构

```
gridtrader/
├── main.py                  # 回测入口：gridrun() + 全部设置
├── gridtrader.py            # GridStrategy（backtrader 策略）实现
├── get_data_scripts/
│   └── get_data.py          # 样例 A 股日 K 拉取脚本（baostock）
├── data/                    # 样例数据（CSV）：沪深 300、单只 A 股、币安分段
├── Pipfile / Pipfile.lock   # 依赖（pandas / backtrader / baostock）
└── .claude/skills/quant/    # 量化 skill（QuantStrategistAgent 的超集副本）
```

## 版权与署名

本项目以 MIT 许可证发布，额外请求使用者在**使用、二次分发或基于本项目构建衍生作品**时，注明作者并引用项目地址：

- **作者：** All Contributors
- **项目：** gridtrader —— 网格交易策略开发及回测工具集（QuantStrategistAgent 子项目）
- **主仓库：** https://github.com/xhqing/QuantStrategistAgent

若你引用代码或基于本仓库二次开发，请在文档 / README / 致谢中保留以上出处。
