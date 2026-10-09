#!/usr/bin/env python3
"""回测驱动器：读 parquet 缓存 → 逐根喂策略 → 内置出场 → 算 R → 按类别统计 → 出「类别→可信度」查表。

口径（与 schema.md 协同，保证精准复现）：
- 策略是纯函数 `evaluate(bars, params) -> dict | None`，只输出「进场信号」，不输出置信度。
- 成交价假设：信号在 t 根收盘后产生 → t+1 根开盘价成交（保守，schema 4.4）。
- 出场规则框架内置（跨策略可比、防自欺）：止损（价格）/ 止盈（价格）/ 超时（最大持仓根数），
  任一先触发即平；出场价 = 触发根开盘价。
- 成本：滑点（bps）+ 佣金（bps）双向各一次；净 avg_R 才是真 edge（日内高频低幅，成本是生死线）。
- R = (exit - entry) / risk，risk = |entry - stop_loss_ref|（做多）。avg_R > 0 才有 edge。
- 类别统计 → confidence 表：win_rate / avg_R / samples / 净 avg_R；samples < 20 标 low_sample。

用法：
  python3 backtest.py run HK.02800 --strategy trend_breakout --ktype 5m
  python3 backtest.py run-all --strategy trend_breakout
  python3 backtest.py generalization HK.02800 --strategy trend_breakout   # train/test + 参数敏感性 + 随机基线
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from data import DATA_CACHE, DataError, load_cache, log, market_of

Q_DIR = Path(__file__).resolve().parent
STRATEGIES_DIR = Q_DIR / "strategies"
OUTPUT_DIR = Q_DIR / "output"                      # 回测运行时产物（已 gitignore）
CONFIDENCE_TABLE = Q_DIR / "confidence_table.json" # 「类别 → 可信度」查表（产物，入库）

# 交易时段（市场本地时间）：港股 09:30-12:00 / 13:00-16:00；美股 09:30-16:00
# 进场约束见 _allow_entry（开盘后、尾盘前 30 分钟）
SESSION_HK = ((dt.time(9, 30), dt.time(12, 0)), (dt.time(13, 0), dt.time(16, 0)))
SESSION_US = ((dt.time(9, 30), dt.time(16, 0)),)


def _session_times(market: str):
    return SESSION_HK if market == "HK" else SESSION_US

# 市场默认交易成本（单边 bps = 万分之一成交金额）：佣金 + 滑点 + 税费打包。
# 港股真实成本约 32-42 bps 双边（印花税买卖各 0.13% 是大头 + 佣金 + 滑点），单边取 18；
# 美股真实成本约 3-7 bps 双边（零佣金券商 + 滑点），单边取 3。
# 可用 params 显式覆盖（params={"cost_bps": X}），否则按标的所属市场自动选。
MARKET_COST_BPS = {"HK": 18.0, "US": 3.0}

# 默认回测参数（可按需覆盖）
DEFAULT_PARAMS = {
    "atr_period": 14,      # ATR 计算周期（根）
    "stop_atr": 2.0,       # 固定止损 = 进场价 ∓ stop_atr × ATR（触及即按止损价成交，R=−1）
    "take_atr": 3.0,       # 固定止盈 = 进场价 ± take_atr × ATR（触及即按止盈价成交，R=+盈亏比）
    "trail_atr": 0.0,      # 移动止损（可选，默认关）：>0 启用，止损随价格有利方向移动。日内固定止盈模式下不用
    "allow_reverse_exit": False,  # 反向信号止盈（可选，默认关）：日内固定止盈模式下不用
    "turtle_exit_bars": 0,  # 海龟式主动离场（可选，默认关）：>0 时做多收盘跌破过去 N 日最低价即主动离场
                           # （趋势反转确认，不等 trailing 回撤）；0=关（纯被动 trailing）
    "max_hold_bars": 60,   # 最大持仓 K 线数（超时兜底，用收盘价平）
    "min_risk_pct": 0.003, # risk 下限：|entry-stop| < 0.3% 价格的交易无统计意义，跳过
    "oos_split": 0.7,      # 泛化测试 train/test 切分比例（按时间）
    "sens_range": 0.2,     # 参数敏感性：±20% 网格
}

# 各市场交易时段
def _session_times(market: str):
    return SESSION_HK if market == "HK" else SESSION_US


# ---------------------------------------------------------------- 数据

def _load(bars, ktype: str) -> pd.DataFrame:
    """读缓存并规整：time 索引、数值列、按时区解读。"""
    df = bars.copy()
    df = df.sort_values("time").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df.set_index("time")
    return df


# ---------------------------------------------------------------- 指标（向量化预计算）

def add_indicators(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """向量化预计算指标（只用 ≤ t 的数据，无未来函数）。"""
    # ATR：真实波幅 TR = max(H-L, |H-prevC|, |L-prevC|)，ATR = TR 的 period 均值
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr_period = int(params["atr_period"])
    df["atr"] = tr.rolling(atr_period).mean()
    df["momentum"] = df["close"] / df["close"].shift(int(params.get("mom_period", 20))) - 1.0
    df["vol_ratio"] = df["volume"] / df["volume"].rolling(int(params.get("vol_period", 20))).mean()
    # RSI（均值回归策略用）：SMA 版，周期可配。100 − 100/(1+RS)，RS = 平均涨幅/平均跌幅
    rsi_period = int(params.get("rsi_period", 14))
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0).rolling(rsi_period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(rsi_period).mean()
    rs = gain / loss.replace(0, pd.NA)
    df["rsi"] = (100.0 - 100.0 / (1.0 + rs)).fillna(50.0)
    # MA + 斜率（多因子策略趋势过滤用）：斜率用 pct_change（相对，跨标的可比）
    ma_period = int(params.get("ma_period", 20))
    df["ma"] = df["close"].rolling(ma_period).mean()
    df["ma_slope"] = df["ma"].pct_change()
    # ADX（平均趋向指标，趋势强度，趋势过滤用）：Wilder 平滑。衡量「有无趋势」不分方向，
    # ADX<20 通常视为震荡市（无趋势可跟）。+DM/-DM/TR 平滑后算 DI，ADX = DX 的 Wilder 平滑。
    adx_period = int(params.get("adx_period", 14))
    prev_high = df["high"].shift(1)
    prev_low = df["low"].shift(1)
    up_move = df["high"] - prev_high
    down_move = prev_low - df["low"]
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    tr_s = tr.ewm(alpha=1 / adx_period, adjust=False).mean()          # Wilder 平滑 TR
    plus_s = plus_dm.ewm(alpha=1 / adx_period, adjust=False).mean()   # Wilder 平滑 +DM
    minus_s = minus_dm.ewm(alpha=1 / adx_period, adjust=False).mean() # Wilder 平滑 −DM
    plus_di = 100.0 * plus_s / tr_s.replace(0, np.nan)
    minus_di = 100.0 * minus_s / tr_s.replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df["adx"] = dx.ewm(alpha=1 / adx_period, adjust=False).mean().fillna(0.0)
    return df


# ---------------------------------------------------------------- 出场逻辑（框架内置）

def _exit_check(df: pd.DataFrame, i: int, stop: float, take: float,
                hold: int, max_hold: int, direction: int) -> tuple[str, float] | None:
    """检查第 i 根是否触发出场，返回 (原因, 出场价)。任一触发即平。

    成交价口径（正确建模跳空，2026-08-04 修正）：实盘止损/止盈单是市价单，触及触发价即成交；
    但若当根**开盘已跳空越过触发价**（隔夜跳空），止损单在开盘价成交（更差），R 偏离 −1：
      - 跳空开盘越过 stop/take（open 已越过）→ exit = open（真实穿透：止损 R<−1、止盈 R>盈亏比）
      - 盘中触及 stop/take（open 在内、low/high 触及）→ exit = stop/take（R=−1 / +盈亏比）
      - 超时 → exit = 收盘价
    日内禁跨段时 open 不会越过 stop（等价恒定 R=−1）；日 K 跨夜跳空真实存在，此口径才正确
    （此前恒定 stop 美化了日 K 的隔夜跳空风险）。
    """
    o, h, l, c = (df[k].iloc[i] for k in ("open", "high", "low", "close"))
    if hold >= max_hold:
        return "timeout", c
    if direction > 0:  # 做多
        if l <= stop:
            return "stop", o if o <= stop else stop    # 跳空低开越过 stop→open（R<−1）；盘中触及→stop
        if h >= take:
            return "take", o if o >= take else take    # 跳空高开越过 take→open（R>盈亏比）
    else:             # 做空
        if h >= stop:
            return "stop", o if o >= stop else stop
        if l <= take:
            return "take", o if o <= take else take
    return None


# ---------------------------------------------------------------- 回测主循环

@dataclass
class Trade:
    symbol: str
    ktype: str
    category: str
    direction: int
    entry_time: str
    entry_price: float
    stop_loss_ref: float
    exit_time: str
    exit_price: float
    R: float
    risk: float          # 进场固定风险 |entry − 初始 stop|（= stop_atr×ATR）；trailing 移动后的
                         # stop 不改变此值——R 与成本标准化都用它，避免 trailing 紧贴 entry 时 risk→0 爆炸
    hold_bars: int
    entry_vol_ratio: float


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    cost_bps: float = 2.0  # 双向成本（滑点+佣金），每边 bps

    @property
    def n(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.R > 0)

    @property
    def win_rate(self) -> float:
        return self.wins / self.n if self.n else 0.0

    @property
    def avg_R(self) -> float:
        return sum(t.R for t in self.trades) / self.n if self.n else 0.0

    @property
    def gross_pnl(self) -> float:
        """毛盈亏（R 单位，未扣成本）。"""
        return sum(t.R for t in self.trades)

    def _net_R_series(self) -> list[float]:
        """每笔扣自身成本后的净 R 序列。逐笔算法（比总额分摊更精确反映成本拖累）：
        每笔成本 = (entry+exit) × cost_bps × 1e-4，换算成 R = 成本 / risk（进场固定 risk）；
        净 R = R − 成本R。用进场固定 risk（非 trailing 后 stop）避免 risk→0 时成本爆炸。
        """
        series = []
        for t in self.trades:
            if t.risk <= 0:
                continue
            cost_in_R = (t.entry_price + t.exit_price) * self.cost_bps * 1e-4 / t.risk
            series.append(t.R - cost_in_R)
        return series

    @property
    def net_avg_R(self) -> float:
        """净 avg_R（优化目标①，越大越好）：逐笔净 R 的均值。"""
        s = self._net_R_series()
        return sum(s) / len(s) if s else 0.0

    @property
    def var_R(self) -> float:
        """净 R 方差（g 的方差惩罚项来源，诊断用）：σ² = Var(net_R)。
        g 的泰勒近似 g ≈ f·EV − (f²/2)(σ²+EV²) 中，σ² 是负向惩罚项——同 EV 下 σ² 越小 g 越大。"""
        s = self._net_R_series()
        if len(s) < 2:
            return 0.0
        m = sum(s) / len(s)
        return sum((x - m) ** 2 for x in s) / len(s)

    @property
    def std_R(self) -> float:
        """净 R 标准差（var_R 开方，与 avg_R 同量纲，诊断用）。"""
        return self.var_R ** 0.5

    # ---- 主优化指标 g（凯利框架对数增长率）----

    REF_F = 0.02  # 报告 g 用的参考风险比例（每笔风险占权益 2%），策略间可比的固定基准

    def log_growth(self, f: float = REF_F) -> float:
        """每笔对数增长率 g = mean(ln(1 + f·net_R))（主优化指标，越大越好）。

        凯利框架核心：固定风险比例 f 下注时，每笔权益乘 (1+f·net_R)，n 笔后权益 ≈ e^(n·g)。
        g 把净平均赔率 EV(=net_avg_R) 与净赔率方差 σ²(=var_R) 统一成单一实数：
            g ≈ f·EV − (f²/2)(σ² + EV²)   （泰勒近似，见 DayTradingAgent/notes/cumulative-return-derivation.md）
        ——第一项 f·EV 均值贡献（正向），第二项 −(f²/2)σ² 方差惩罚（负向）。
        g > 0 才有指数复利（比 EV > 0 严格得多：EV>0 但 f 过大或 σ² 过大都会让 g<0）。
        用净 R（扣成本）。f = 每笔风险占权益比例（默认 2%）。
        """
        s = self._net_R_series()
        if not s:
            return 0.0
        total = 0.0
        for r in s:
            val = 1.0 + f * r
            if val <= 0:   # f 过大、某笔亏穿 → ln 无定义，记 −∞（f 远超凯利）
                return float("-inf")
            total += math.log(val)
        return total / len(s)

    def f_star(self) -> float:
        """近似凯利最优 f* ≈ EV_net / E[net_R²]（诊断用，指导下注）。
        f* ≤ 0 表示 EV ≤ 0 无 edge；实践用分数凯利（1/4~1/2 f*），超 2f* 必缩水。"""
        s = self._net_R_series()
        if not s:
            return 0.0
        ev = sum(s) / len(s)
        er2 = sum(r * r for r in s) / len(s)
        return ev / er2 if er2 > 0 else 0.0


def run_backtest(strategy, symbol: str, ktype: str, params: dict | None = None,
                 df: pd.DataFrame | None = None) -> BacktestResult:
    """对单标的跑回测（状态机模型：同时最多一仓，更真实——禁止重叠持仓）。

    三个进出场机制（2026-08-03 改进）：
    1. 止损/止盈价基于实际成交价（t+1 开盘价）反推，而非信号根开盘价——
       risk 干净等于 stop_atr × ATR。策略层输出的 stop_loss_ref 仅作参考（实时参考用）。
    2. 移动止损（trail_atr>0）：持仓中止损价随价格有利方向移动、只升不降（做多）。
    3. 反向信号平仓（allow_reverse_exit=True）：持仓中出现反向信号则 t+1 开盘平仓。

    df 可传入预加载的缓存（多标的循环时复用）；默认从 data_cache 读。
    """
    params = {**DEFAULT_PARAMS, **(params or {})}
    if df is None:
        df = load_cache(symbol, ktype)
    df = _load(df, ktype)
    df = add_indicators(df, params)

    # 进场价模式：open=次日开盘（默认）/ close=次日收盘 / low=日内最低（理论上界，未来函数）/
    # dip_recover=探底回升（需 15m 分钟 K 执行数据；富途分钟 K 仅 2021 起，故 2021 前信号自动放弃）
    entry_mode = params.get("entry_price_mode", "open")
    df_min = None
    if entry_mode == "dip_recover":
        try:
            df_min = _load(load_cache(symbol, "15m"), "15m")
        except Exception:
            df_min = None
        dip_pct = float(params.get("dip_pct", 0.005))

    market = market_of(symbol)
    cost_bps = float(params.get("cost_bps", MARKET_COST_BPS.get(market, 5.0)))
    result = BacktestResult(cost_bps=cost_bps)

    stop_atr = float(params["stop_atr"])
    take_atr = float(params["take_atr"])
    max_hold = int(params["max_hold_bars"])
    trail_atr = float(params.get("trail_atr", 0))      # 0=固定止损；>0=trailing
    allow_reverse = bool(params.get("allow_reverse_exit", False))
    turtle_bars = int(params.get("turtle_exit_bars", 0))  # 海龟主动离场（0=关）
    min_risk_pct = float(params["min_risk_pct"])

    position: dict | None = None   # 当前持仓；None=空仓

    def close_pos(pos: dict, exit_idx: int, exit_price: float) -> None:
        """结算持仓成一笔交易，加入 result。"""
        if pos["direction"] > 0:
            R = (exit_price - pos["entry_price"]) / pos["risk"]
        else:
            R = (pos["entry_price"] - exit_price) / pos["risk"]
        result.trades.append(Trade(
            symbol=symbol, ktype=ktype, category=pos["category"], direction=pos["direction"],
            entry_time=pos["entry_time"].isoformat(), entry_price=pos["entry_price"],
            stop_loss_ref=pos["stop"], exit_time=df.index[exit_idx].isoformat(),
            exit_price=exit_price, R=R, risk=pos["risk"], hold_bars=exit_idx - pos["entry_idx"],
            entry_vol_ratio=pos["vol_ratio"],
        ))

    for i in range(len(df)):
        # —— 有持仓 & 上一根标记了反向平仓：本根（i）开盘执行平仓（t+1 口径）——
        if position is not None and position.get("reverse_exit_idx") == i:
            close_pos(position, i, float(df["open"].iloc[i]))
            position = None

        if position is not None:
            pos = position
            # ①② trailing 更新：基于 ≤ i-1 的 high/low（已确定，无未来函数），只朝有利方向
            if trail_atr > 0 and i > pos["entry_idx"]:
                if pos["direction"] > 0:
                    pos["extreme"] = max(pos["extreme"], float(df["high"].iloc[i - 1]))
                    pos["stop"] = max(pos["stop"], pos["extreme"] - trail_atr * pos["atr"])
                else:
                    pos["extreme"] = min(pos["extreme"], float(df["low"].iloc[i - 1]))
                    pos["stop"] = min(pos["stop"], pos["extreme"] + trail_atr * pos["atr"])

            # 出场条件检查（固定止损/止盈 + 超时；stop 已是 trailing 更新后的值，若启用）
            held = i - pos["entry_idx"]
            exit_trigger = _exit_check(df, i, pos["stop"], pos["take"], held, max_hold, pos["direction"])
            if exit_trigger:
                close_pos(pos, i, exit_trigger[1])
                position = None
            elif turtle_bars > 0 and _turtle_exit(df, i, pos, turtle_bars):
                # 海龟式主动离场：做多收盘跌破 N 日结构低点 = 趋势反转确认，主动离场、不等 trailing 回撤
                close_pos(pos, i, float(df["close"].iloc[i]))
                position = None
            elif _is_session_end(df.index[i], ktype, market):
                # 日内禁跨段：午休前（港股 12:00）/ 收盘前（16:00）强制平仓，防隔夜 + 防跨午休跳空
                close_pos(pos, i, float(df["close"].iloc[i]))
                position = None
            elif allow_reverse and i + 1 < len(df):
                # ③ 反向信号检测（i 根收盘）：若反向，标记 i+1 开盘平
                sig = strategy.evaluate(df.iloc[: i + 1], params)
                if sig and sig.get("direction", 0) != 0 \
                        and int(sig["direction"]) != pos["direction"]:
                    pos["reverse_exit_idx"] = i + 1

        if position is None and i < len(df) - 1:
            # —— 空仓：i 根收盘检查进场信号，i+1 开盘成交 ——
            signal = strategy.evaluate(df.iloc[: i + 1], params)
            if not signal or signal.get("direction", 0) == 0:
                continue
            direction = int(signal["direction"])
            entry_idx = i + 1
            if ktype != "DAY" and not _allow_entry(df.index[entry_idx], market):
                continue   # 日 K 次日开盘成交、无盘中时段约束；分钟 K 才受开盘/尾盘约束
            # 进场价模式：
            #   open       = 次日开盘（默认，保守）
            #   close      = 次日收盘（更晚确认）
            #   low        = 日内最低（做多理论上界，未来函数——仅评估上限）
            #   dip_recover= 探底回升：t+1 日 15m 序列，先探底（回踩 dip_pct）后收复开盘价 → 买在收复根收盘
            entry_mode = params.get("entry_price_mode", "open")
            if entry_mode == "dip_recover" and df_min is not None:
                day = df.index[entry_idx].date()
                day_min = df_min[df_min.index.date == day]
                if len(day_min) < 3:
                    continue   # 当日无 15m 数据 → 放弃信号
                open0 = float(day_min["open"].iloc[0])
                low_day = float(day_min["low"].min())
                if low_day >= open0 * (1 - dip_pct):
                    continue   # 无回踩（没探底）→ 放弃，不追高
                recover = day_min[day_min["close"] > open0]
                if len(recover) == 0:
                    continue   # 全天没收复开盘价 → 放弃，不接飞刀
                entry_price = float(recover["close"].iloc[0])
            elif entry_mode == "close":
                entry_price = float(df["close"].iloc[entry_idx])
            elif entry_mode == "low":
                entry_price = float(df["low"].iloc[entry_idx])
            else:
                entry_price = float(df["open"].iloc[entry_idx])
            atr = float(df["atr"].iloc[i])   # 信号根 ATR（基于 ≤ i 数据）
            if atr <= 0:
                continue
            # ① 止损/止盈基于实际成交价反推：risk = stop_atr × atr（干净）
            if direction > 0:
                stop = entry_price - stop_atr * atr
                take = entry_price + take_atr * atr
                extreme = float(df["high"].iloc[entry_idx])   # trailing 起点（trailing 启用时用）
            else:
                stop = entry_price + stop_atr * atr
                take = entry_price - take_atr * atr
                extreme = float(df["low"].iloc[entry_idx])
            risk = abs(entry_price - stop)
            if risk / entry_price < min_risk_pct:   # risk 下限过滤
                continue
            position = {
                "direction": direction, "entry_idx": entry_idx, "entry_price": entry_price,
                "stop": stop, "take": take, "risk": risk, "atr": atr, "extreme": extreme,
                "category": signal.get("category", "unknown"),
                "entry_time": df.index[entry_idx],
                "vol_ratio": float(df["vol_ratio"].iloc[entry_idx] or 0.0),
            }

    if position is not None:   # 数据结束仍有持仓：用最后收盘平（罕见兜底）
        close_pos(position, len(df) - 1, float(df["close"].iloc[-1]))
    return result


def _allow_entry(t: pd.Timestamp, market: str) -> bool:
    """进场时段约束：开盘后、尾盘前（日内策略尾盘不进场，防隔夜风险 + 留持仓空间）。

    港股：09:30–15:30（早段 + 午后，最后 30 分钟不进场）；
    美股：09:30–15:30（收盘前 30 分钟不进场）。
    t 为 tz-aware 时间（市场本地时区），.time() 即市场本地时间。
    """
    hm = t.time()
    if market == "HK":
        return (dt.time(9, 30) <= hm <= dt.time(12, 0)) or (dt.time(13, 0) <= hm <= dt.time(15, 30))
    return dt.time(9, 30) <= hm <= dt.time(15, 30)


_KTYPE_MIN = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "60m": 60, "DAY": 1440}

def _is_session_end(t: pd.Timestamp, ktype: str, market: str) -> bool:
    """该 K 线根是否为交易段最后一根（其收盘时刻 = 段结束）。日内禁跨段时用于强制平仓。

    港股段结束：12:00（午休前）/ 16:00（收盘前）；美股：16:00（收盘前）。
    日 K 不适用（跨日，返回 False）。t 为 tz-aware 市场本地时间。
    """
    kmin = _KTYPE_MIN.get(ktype, 0)
    if kmin <= 0 or kmin >= 1440:
        return False
    close_time = (t + pd.Timedelta(minutes=kmin)).time()
    if market == "HK":
        return close_time in (dt.time(12, 0), dt.time(16, 0))
    return close_time == dt.time(16, 0)


def _turtle_exit(df: pd.DataFrame, i: int, pos: dict, n: int) -> bool:
    """海龟式主动离场：做多收盘跌破过去 N 日最低价、做空收盘升破过去 N 日最高价 → 趋势反转确认。

    这是「主动平仓」：趋势结构反转就离场，不等 trailing 回撤 3×ATR（可能回吐更多）。
    用当根收盘价确认（避免盘中插针误判），出场价 = 当根收盘价。
    """
    if i < n:
        return False
    if pos["direction"] > 0:
        return float(df["close"].iloc[i]) < df["low"].iloc[i - n:i].min()
    return float(df["close"].iloc[i]) > df["high"].iloc[i - n:i].max()


# ---------------------------------------------------------------- 统计 → 置信度表

def confidence_table(result: BacktestResult) -> dict:
    """按类别聚合 → 类别 → {win_rate, avg_R, samples, net_avg_R, low_sample}。"""
    table: dict[str, dict] = {}
    for t in result.trades:
        row = table.setdefault(t.category, {"win_rate": 0.0, "avg_R": 0.0,
                                            "samples": 0, "net_avg_R": 0.0, "low_sample": False})
        row["samples"] += 1
    for cat, row in table.items():
        trades = [t for t in result.trades if t.category == cat]
        row["win_rate"] = sum(1 for t in trades if t.R > 0) / len(trades)
        row["avg_R"] = sum(t.R for t in trades) / len(trades)
        # 净 avg_R：按类别内交易逐笔扣成本（用进场固定 risk，非 trailing 后 stop）
        total_risk = sum(t.risk for t in trades)
        if total_risk > 0:
            cost_total = sum(t.entry_price + t.exit_price for t in trades) * result.cost_bps * 1e-4
            row["net_avg_R"] = row["avg_R"] - cost_total / total_risk
        else:
            row["net_avg_R"] = row["avg_R"]
        row["low_sample"] = row["samples"] < 20
    return table


# ---------------------------------------------------------------- CLI

def _load_strategy(name: str):
    try:
        return importlib.import_module(f"strategies.{name}")
    except ImportError as e:
        raise DataError(f"策略 {name} 不存在：{e}") from e


def cmd_run(args) -> None:
    strategy = _load_strategy(args.strategy)
    df = load_cache(args.symbol, args.ktype)
    params = json.loads(args.params) if args.params else None
    result = run_backtest(strategy, args.symbol, args.ktype, params=params, df=df)

    table = confidence_table(result)
    f_ref = BacktestResult.REF_F
    g = result.log_growth(f_ref)
    fstar = result.f_star()
    g_str = f"{g*100:+.3f}%/笔" if g != float("-inf") else "−∞（f 过大）"
    print(f"\n=== {args.symbol} {args.ktype} @ {args.strategy} ===")
    print(f"总交易 {result.n}，胜率 {result.win_rate:.1%}，"
          f"g={g_str} @ f={f_ref:.0%}（主指标，>0 才有复利）；"
          f"诊断：EV净={result.net_avg_R:+.3f}R σ净={result.std_R:.3f}R f*={fstar*100:+.1f}%（≤0=无edge）")
    print(f"\n按类别：")
    for cat, row in sorted(table.items()):
        flag = " ⚠️低样本" if row["low_sample"] else ""
        print(f"  {cat}: n={row['samples']} win={row['win_rate']:.1%} avg_R={row['avg_R']:.3f} "
              f"net={row['net_avg_R']:.3f}{flag}")

    # 存 confidence 表
    CONFIDENCE_TABLE.parent.mkdir(parents=True, exist_ok=True)
    CONFIDENCE_TABLE.write_text(json.dumps(table, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n置信度表 → {CONFIDENCE_TABLE.relative_to(Q_DIR)}")

    # 存每笔记录（可溯源）
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    trades_df = pd.DataFrame([t.__dict__ for t in result.trades])
    out = OUTPUT_DIR / f"{args.symbol}_{args.ktype}_{args.strategy}.parquet"
    trades_df.to_parquet(out, index=False)
    print(f"每笔记录 → {out.relative_to(Q_DIR)}")


def cmd_run_all(args) -> None:
    strategy = _load_strategy(args.strategy)
    symbols = sorted(p.name for p in DATA_CACHE.iterdir() if p.is_dir())
    if not symbols:
        raise DataError(f"缓存里没有标的：{DATA_CACHE}，先跑 python3 data.py fetch-all")
    for sym in symbols:
        for kt in ("5m", "15m", "1m", "DAY"):
            try:
                df = load_cache(sym, kt)
            except DataError:
                continue
            result = run_backtest(strategy, sym, kt, df=df)
            print(f"{sym} {kt}: n={result.n} win={result.win_rate:.1%} avg_R={result.avg_R:.3f} net={result.net_avg_R:.3f}")


# 泛化测试：train/test 切分 + 参数敏感性 + 随机基线
def cmd_generalization(args) -> None:
    strategy = _load_strategy(args.strategy)
    df = load_cache(args.symbol, args.ktype)  # 原始 df（有 time 列），切分后交 run_backtest 内部 _load

    # 1. train/test 按时间切分（前 70% 调参，后 30% 只测一次）
    split = int(len(df) * float(args.split))
    train, test = df.iloc[:split].copy(), df.iloc[split:].copy()
    tr = run_backtest(strategy, args.symbol, args.ktype, df=train)
    te = run_backtest(strategy, args.symbol, args.ktype, df=test)
    print(f"\n=== 泛化测试 {args.symbol} {args.ktype} @ {args.strategy} ===")
    print(f"train（前 {len(train):,} 根）: n={tr.n} avg_R={tr.avg_R:.3f}")
    print(f"test （后 {len(test):,} 根）: n={te.n} avg_R={te.avg_R:.3f}")
    print(f"OOS 衰减：{(te.avg_R - tr.avg_R) / tr.avg_R:.1%}（负 = 衰减，正 = 增强）" if tr.avg_R else "train avg_R=0，无法算衰减")

    # 2. 参数敏感性：默认参数邻域 ±sens_range，看 avg_R 是否稳定（孤峰 vs 高原）
    base = DEFAULT_PARAMS
    print("\n参数敏感性（±20%）：")
    for key in ("mom_period", "vol_mult", "stop_atr", "take_atr", "max_hold_bars"):
        if key not in base:
            continue
        vals = []
        for factor in (0.8, 1.0, 1.2):
            p = {**base, key: max(1, int(base[key] * factor)) if isinstance(base[key], int) else base[key] * factor}
            r = run_backtest(strategy, args.symbol, args.ktype, params=p, df=df)
            vals.append(r.avg_R)
        print(f"  {key}: {vals[0]:.3f} → {vals[1]:.3f} → {vals[2]:.3f}（0.8x/1.0x/1.2x）")

    # 3. 随机基线：打乱信号，看真实 avg_R 是否显著高于随机
    # 20 次已足够估计随机分布的均值/标准差（P 值粗算够用）；100 次全量回测太久（每次 ~12s）
    np.random.seed(42)
    df_shuf = df.copy()
    base_r = run_backtest(strategy, args.symbol, args.ktype, df=df)
    nulls = []
    for _ in range(20):
        df_shuf["close"] = np.random.permutation(df_shuf["close"].values)
        r = run_backtest(strategy, args.symbol, args.ktype, df=df_shuf)
        nulls.append(r.avg_R)
    nulls = np.array(nulls)
    pct = (nulls >= base_r.avg_R).mean()
    print(f"\n随机基线（{len(nulls)} 次打乱）: 随机 avg_R {nulls.mean():.3f}±{nulls.std():.3f}，真实 {base_r.avg_R:.3f}，P(随机≥真实)={pct:.1%}")
    print(f"结论：{'✅ 显著优于随机' if pct < 0.05 else '❌ 未显著优于随机（可能是噪声/过拟合）'}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="backtest.py", description="回测驱动器：回测 → 类别统计 → 置信度表 + 泛化测试")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="单标的回测，出置信度表")
    p.add_argument("symbol", help="标的，如 HK.02800")
    p.add_argument("--strategy", required=True, help="策略名（strategies/ 下文件名）")
    p.add_argument("--ktype", default="5m", help="周期（1m/5m/15m/DAY）")
    p.add_argument("--params", default=None,
                   help='策略参数 JSON，覆盖默认。如 \'{"ma_period":15,"oversold":30,"vol_threshold":1.0}\'')
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("run-all", help="股票池全标的回测（摘要）")
    p.add_argument("--strategy", required=True)
    p.set_defaults(fn=cmd_run_all)

    p = sub.add_parser("generalization", help="泛化测试：train/test + 参数敏感性 + 随机基线")
    p.add_argument("symbol", help="标的")
    p.add_argument("--strategy", required=True)
    p.add_argument("--ktype", default="5m")
    p.add_argument("--split", type=float, default=0.7, help="train 比例（默认 0.7）")
    p.set_defaults(fn=cmd_generalization)

    args = parser.parse_args()
    try:
        args.fn(args)
    except (DataError, ValueError) as e:
        log(f"错误：{e}")
        sys.exit(2)


if __name__ == "__main__":
    main()
