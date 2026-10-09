"""RSI 均值回归策略（纯函数，无置信度——置信度由回测赋予）。

逻辑：RSI 超卖（< oversold）→ 做多（赌反弹）；RSI 超买（> overbought）→ 做空（赌回落）。
与 trend_breakout（动量追涨）相反——赌价格过度偏离后会回归，符合 ETF/个股日内均值回归特性。

出场由回测框架统一内置（止损/止盈/超时/trailing/reverse），策略只负责进场。
多空对称（超卖做多、超买做空），便于测反向信号平仓机制。

参数：
  rsi_period   RSI 计算周期（默认 14）
  oversold     超卖阈值（默认 30，越低越严格）
  overbought   超买阈值（默认 70，越高越严格）

纯函数保证：无随机、无全局状态、无副作用、无未来函数。
"""

from __future__ import annotations

import pandas as pd


def evaluate(bars: pd.DataFrame, params: dict) -> dict | None:
    if len(bars) < 30:
        return None

    last = bars.iloc[-1]
    rsi = float(bars["rsi"].iloc[-1])
    atr = float(bars["atr"].iloc[-1])
    if atr <= 0:
        return None

    oversold = float(params.get("oversold", 30))
    overbought = float(params.get("overbought", 70))
    open_ = float(last["open"])

    if rsi < oversold:
        direction = 1
        cat = "rsi_oversold_deep" if rsi < oversold * 0.6 else "rsi_oversold"
    elif rsi > overbought:
        direction = -1
        cat = "rsi_overbought_deep" if rsi > 100 - (100 - overbought) * 0.6 else "rsi_overbought"
    else:
        return None

    stop_atr = float(params.get("stop_atr", 2.0))
    take_atr = float(params.get("take_atr", 3.0))
    # 均值回归是逆势：止损用 ATR 倍数（与动量同框架，跨策略可比）
    return {
        "direction": direction,
        "action": 1,
        "entry_price": open_,
        "trigger": "rsi_reversion",
        "trigger_params": {"rsi": round(rsi, 1), "rsi_period": int(params.get("rsi_period", 14)),
                           "oversold": oversold, "overbought": overbought},
        "levels": {
            "support": float(last["low"]),
            "resistance": float(last["high"]),
            "breakout": float(last["close"]),
            # 参考位（实际止损由框架基于成交价反推，见 backtest.run_backtest）
            "stop_loss_ref": round(open_ - direction * stop_atr * atr, 4),
            "take_profit_ref": round(open_ + direction * take_atr * atr, 4),
        },
        "features": {"rsi": round(rsi, 1), "atr": round(atr, 4),
                     "vol_ratio": round(float(bars["vol_ratio"].iloc[-1] or 0.0), 2)},
        "category": cat,
    }
