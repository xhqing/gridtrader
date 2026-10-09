"""多因子策略：顺势回踩（趋势过滤 + RSI 回踩 + 放量确认）。

单动量（追高买顶）、单均值回归（逆势被碾压）在回测中毛 EV≈0。本策略综合两者优点——
顺势（大方向对）+ 等回踩企稳（不追高）+ 放量确认（反弹有效），对应 schema 的
`entry_condition: pullback_hold`。三个弱因子 AND 组合，理论上比任一单因子 EV 高。

逻辑（做多）：MA20 上行 + 收盘在 MA20 上（趋势向上）AND RSI 从超卖区上穿（回踩企稳）
            AND 放量（确认）→ 进场做多。做空对称。

出场由回测框架统一内置（止损/止盈/超时/trailing/reverse），策略只负责进场。

代价：三条件 AND → 信号少，需关注样本量（<30 无统计意义）。

参数：
  ma_period     趋势均线周期（默认 20）
  rsi_period    RSI 周期（默认 14）
  oversold      回踩超卖阈值（默认 30；RSI 上穿此值 = 企稳）
  overbought    做空对称阈值（默认 70）
  vol_threshold 放量确认阈值（默认 1.0 = 均量）

纯函数保证：无随机、无全局状态、无副作用、无未来函数（RSI 上穿用 t-1、t 根比较）。
"""

from __future__ import annotations

import pandas as pd


def evaluate(bars: pd.DataFrame, params: dict) -> dict | None:
    if len(bars) < 35:
        return None

    last = bars.iloc[-1]
    prev = bars.iloc[-2]
    close = float(last["close"])
    open_ = float(last["open"])
    ma = float(bars["ma"].iloc[-1])
    ma_slope = float(bars["ma_slope"].iloc[-1])
    rsi = float(bars["rsi"].iloc[-1])
    rsi_prev = float(bars["rsi"].iloc[-2])
    atr = float(bars["atr"].iloc[-1])
    vol_ratio = float(bars["vol_ratio"].iloc[-1] or 0.0)
    if atr <= 0:
        return None

    oversold = float(params.get("oversold", 30))
    overbought = float(params.get("overbought", 70))
    vol_thr = float(params.get("vol_threshold", 1.0))

    # 因子1 趋势过滤 + 因子2 RSI 上穿回踩 + 因子3 放量确认
    trend_up = (ma_slope > 0) and (close > ma)
    rsi_cross_up = (rsi_prev < oversold) and (rsi >= oversold)   # 从超卖区回升 = 企稳
    vol_ok = vol_ratio >= vol_thr

    if trend_up and rsi_cross_up and vol_ok:
        direction = 1
        cat = "pullback_long"
    else:
        trend_dn = (ma_slope < 0) and (close < ma)
        rsi_cross_dn = (rsi_prev > overbought) and (rsi <= overbought)
        if trend_dn and rsi_cross_dn and vol_ok:
            direction = -1
            cat = "pullback_short"
        else:
            return None

    stop_atr = float(params.get("stop_atr", 2.0))
    take_atr = float(params.get("take_atr", 3.0))
    return {
        "direction": direction,
        "action": 1,
        "entry_price": open_,
        "trigger": "multi_factor_pullback",
        "trigger_params": {"ma_period": int(params.get("ma_period", 20)),
                           "rsi_period": int(params.get("rsi_period", 14)),
                           "oversold": oversold, "vol_threshold": vol_thr},
        "levels": {
            "support": float(last["low"]),
            "resistance": float(last["high"]),
            "breakout": ma,
            "stop_loss_ref": round(open_ - direction * stop_atr * atr, 4),
            "take_profit_ref": round(open_ + direction * take_atr * atr, 4),
        },
        "features": {
            "ma": round(ma, 4), "ma_slope": round(ma_slope, 6),
            "rsi": round(rsi, 1), "rsi_prev": round(rsi_prev, 1),
            "vol_ratio": round(vol_ratio, 2), "atr": round(atr, 4),
        },
        "category": cat,
    }
