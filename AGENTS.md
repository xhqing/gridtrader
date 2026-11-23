# gridtrader（网格交易策略工具集）

**gridtrader** 是量化策略 agent **Mark**（[quant-agent](https://github.com/xhqing/quant-agent)）名下的子项目。2026-11-08 曾并入 quant-agent 成为目录，2026-11-23 抽回为独立仓库——两段历史都在本仓库提交记录里。默认分支为 **`main`**（2026-11-23 由 `master` 改名）。

网格交易策略开发及回测工具（Python / backtrader，Pipenv 管理依赖）。工具只做回测，**不下单、不接实盘**。

## 定位与边界

- 版本号与改动记录以本仓库根 `VERSION` / `CHANGELOG.md` 为准。
- 角色：本仓库任何分支 / worktree 的会话都是 **Worker**——只干当前 worktree 的这一件事，干完向 Mark 的 Leader 会话汇报（改了什么 / 验证结果 / PR 号 / 遗留问题）；不碰 local main、不建删 worktree。

## 数据契约（本目录最容易被搞坏的地方）

- `data/` 下的 CSV 是**入库的样例基准数据**：`binance-segment.csv`（币安分钟线）、`sh000300.csv`（沪深 300 指数日线）、`sh603690.csv`（单只 A 股日线）。它们既是示例也是回测基准——**不许覆盖、不许用新拉取的数据原地替换**；需要新样本就新起文件名并说明来源。
- `gridrun()` 要求输入 CSV 严格按顺序含 6 列：`time,open,high,low,close,volume`（`main.py` 按列位置取，顺序错了不报错、结果全错），`time` 要能被 `pandas.parse_dates` 解析。
- 两份 A 股 CSV 是 **baostock 原始导出（14 列，首列 `date`）**，不能直接喂给 `gridrun()`；要用它们回测，先另存成 6 列格式的新文件，别改原文件。
- 大文件放 `tmp/` 或仓库外，不入库；单文件超 5MB 前先想清楚是否真的要有。

## 依赖与环境

- 依赖由 **Pipenv** 管理（`Pipfile` 声明 Python 3.7；pandas / backtrader / baostock）。装依赖与跑脚本一律 `pipenv install` / `pipenv run python main.py`，**不要全局 pip install、不要污染系统 Python**。
- `.venv/`、`__pycache__/`、`tmp/` 已忽略，不入库。

## 策略口径

- `GridStrategy`（`gridtrader.py`）：以过去 1440 根 K 线最高价 / 最低价的中值为中轨，上下 ±2.5% 按 0.5% 间距铺 11 档，每档对应一个目标仓位（顶部最轻、底部满仓）；收盘越过档位就用 `order_target_percent` 把仓位调回该档目标。
- 改参数（周期 / 区间 / 档数 / 仓位映射）必须用同一份数据跑改动前后回测，把「起始 / 期末账户价值」对比写进提交说明与本仓库 `CHANGELOG.md`；不许只凭感觉调。
- `get_data_scripts/get_data.py` 是 baostock 取数样例（登录 → 查询 → `to_csv` → 登出），保持它能独立跑通；拉下来的数据不要直接覆盖 `data/` 里的基准文件。

## 运行

`pipenv run python main.py`——全部设置（`filename` / `timeframe` / `all_cash`）都在 `main.py` 的 `__main__` 块里；换标的就是把 `filename` 指向新的 6 列 CSV。

## 更多

`README_cn.md` / `README.md`（用法与工作原理）；通用工作规则见本仓库 `.pi/APPEND_SYSTEM.md` 与全局 `~/.pi/agent/AGENTS.md`。
