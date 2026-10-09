"""日 K 动量突破策略（多空对称，纯函数，无置信度——置信度由回测赋予）。

逻辑：收盘创 N 根新高 + 放量 → 做多（direction=1）；收盘创 N 根新低 + 放量 → 做空（direction=-1）。
- 出场（止损/止盈/超时）由回测框架统一内置（backtest.py 的 _exit_check），策略只负责进场。
- 输出战术层 schema dict，或 None（无信号）。

参数（见 DEFAULT_PARAMS / backtest 传入）：
  mom_period  动量周期（根）：收盘创 mom_period 根新高/新低
  vol_mult    放量倍数：成交量 > vol_mult × 均量
  mode        交易方向："both" 多空都做（默认）/ "long" 只做多 / "short" 只做空
  vol_period  均量周期（根）
  atr_period  在 backtest 内置，用于止损/止盈距离

纯函数保证：无随机、无全局状态、无副作用、无未来函数 → 同 bars + 同 params 永远同输出。
"""

from __future__ import annotations

import pandas as pd


def evaluate(bars: pd.DataFrame, params: dict) -> dict | None:
    """输入截至当前根的历史 K 线 → 输出战术层信号 dict（无 confidence），无信号返回 None。"""
    if len(bars) < 30:  # 需要足够历史算指标
        return None

    last = bars.iloc[-1]
    close = float(last["close"])
    open_ = float(last["open"])
    high = float(last["high"])
    low = float(last["low"])
    vol = float(last["volume"])
    atr = float(bars["atr"].iloc[-1])
    vol_ratio = float(bars["vol_ratio"].iloc[-1])

    if atr <= 0:
        return None

    mode = params.get("mode", "both")   # "both" 多空都做 / "long" 只做多 / "short" 只做空
    mom_period = int(params.get("mom_period", 20))
    vol_mult = float(params.get("vol_mult", 1.5))

    # 趋势过滤（可选）：ADX < 阈值 = 震荡市（无趋势可跟），不交易——把震荡期亏损变成空仓零收益
    adx_threshold = float(params.get("adx_threshold", 0))   # 0 = 不过滤
    if adx_threshold > 0 and float(bars["adx"].iloc[-1]) < adx_threshold:
        return None

    # 动量：收盘创 mom_period 根新高（做多）/ 新低（做空），不含当前根
    recent = bars["close"].iloc[-mom_period - 1:-1]
    recent_high = recent.max()
    recent_low = recent.min()
    vol_surge = vol_ratio >= vol_mult
    long_sig = close > recent_high and vol_surge and mode != "short"
    short_sig = close < recent_low and vol_surge and mode != "long"

    if long_sig:
        direction = 1
        base_cat = "mom_breakout"
    elif short_sig:
        direction = -1
        base_cat = "mom_breakdown"
    else:
        return None

    # 类别签名（可复现，用于查回测表；做多/做空区分）
    category = base_cat
    if vol_ratio >= 2.0:
        category += "_vol2x"
    elif vol_ratio >= 1.5:
        category += "_vol1.5x"
    else:
        category += "_vol1x"

    # 止损/止盈：ATR 倍数（框架内置口径，策略提供参考位）
    stop_atr = float(params.get("stop_atr", 2.0))
    take_atr = float(params.get("take_atr", 3.0))

    return {
        "direction": 1,
        "action": 1,
        "entry_price": open_,
        "trigger": "mom_breakout",
        "trigger_params": {"mom_period": mom_period, "vol_mult": vol_mult,
                           "breakout_level": recent_high},
        "levels": {
            "support": low,
            "resistance": high,
            "breakout": recent_high,
            "stop_loss_ref": round(open_ - stop_atr * atr, 4),
            "take_profit_ref": round(open_ + take_atr * atr, 4),
        },
        "features": {
            "momentum": round(close / recent_high - 1.0, 6),
            "vol_ratio": round(vol_ratio, 2),
            "atr": round(atr, 4),
        },
        "category": category,
    }
