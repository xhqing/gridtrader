# 量化策略 输入输出契约 + 回测设计（2026-07-14 定）

## 三条铁律（用户立，最高优先级）

1. **时间必带市场时区**：港股用北京/港时（`+08:00`），美股用美东（`EDT -04:00` / `EST -05:00`）。ISO 8601 带偏移。禁止无时区时间戳。
2. **输出全量化**：每个字段要么是**数值**（价格/量/比率/百分比），要么是**有限枚举**（离散值，数值编码）。**禁止定性文字**（如"5 日均线上行""动能减弱"）。这是量化区别于主观交易的本质。
3. **schema 与回测协同设计**：只有输入（K 线 OHLCV）和输出（本 schema）都可量化，回测才能**精准复现**（同数据 + 同参数 → 同结果）。

> 判定依据不许写文字，必须转成一组数值 `features`（如均线值、斜率、量比、ATR）。定性结论（方向/状态）只作为这些数值的**离散枚举结果**输出。

---

## 一、战略层 schema（日 K，今日地图）

| 字段 | 类型 | 量化说明 |
|---|---|---|
| `symbol` | string | 标的标识，富途前缀 `HK.02800` / `US.SPY` |
| `bar.date` | date | 该日 K 的日期，如 `2026-07-14` |
| `bar.tz` | string | 市场时区：港股 `Asia/Hong_Kong (+08:00 北京/港时)`；美股 `America/New_York (EDT -04:00 / EST -05:00 美东)` |
| `direction` | int 枚举 | `+1` 多 / `-1` 空 / `0` 中性 |
| `regime` | int 枚举 | `+1` 趋势上升 / `-1` 趋势下降 / `0` 区间震荡 |
| `levels.support` | float | 支撑位（价格） |
| `levels.resistance` | float | 阻力位（价格） |
| `levels.breakout` | float | 突破触发位（价格） |
| `levels.stop_loss_ref` | float | 止损参考位（价格，技术位；最终止损由实盘综合定） |
| `levels.take_profit_ref` | float | 止盈参考位（价格） |
| `entry_condition` | string 枚举 | 进场条件类型：`pullback_hold`(回踩企稳) / `breakout`(突破) / `range_low`(区间支撑买) / `range_high`(区间阻力卖) / `none` |
| `entry_params` | object(全数值) | 该条件的量化参数（如 `pullback_zone_low/high`、`confirm_bars`），由策略定义 |
| `features` | object(全 float) | **判定的量化依据**（替代文字 reason）：如 `ma5/ma20/ma5_slope/volume_ratio_5d/atr20/adx`，具体由策略定，**必须全是数字** |
| `category` | string | 类别签名，由量化特征拼接（如 `trend_up_5d+vol_breakout+pullback_holds`），可复现、用于查回测表 |
| `confidence` | object | `{win_rate: float, samples: int, avg_R: float}`，**回测赋予**；策略原始输出 `null` |

**JSON 例子（港股盈富，战略层）**：
```json
{
  "symbol": "HK.02800",
  "bar": {"date": "2026-07-14", "tz": "Asia/Hong_Kong (+08:00 北京/港时)"},
  "direction": 1,
  "regime": 1,
  "levels": {"support": 24.20, "resistance": 25.10, "breakout": 25.10, "stop_loss_ref": 23.90, "take_profit_ref": 25.80},
  "entry_condition": "pullback_hold",
  "entry_params": {"pullback_zone_low": 24.20, "pullback_zone_high": 24.40, "confirm_bars": 2},
  "features": {"ma5": 24.50, "ma20": 24.10, "ma5_slope": 0.012, "volume_ratio_5d": 1.8, "atr20": 0.35},
  "category": "trend_up_5d+vol_breakout+pullback_holds",
  "confidence": {"win_rate": 0.62, "samples": 40, "avg_R": 1.3}
}
```

---

## 二、战术层 schema（分钟 K，盘中扳机）

| 字段 | 类型 | 量化说明 |
|---|---|---|
| `symbol` | string | 同上 |
| `bar.time` | ISO 8601 | 带偏移，如 `2026-07-14T10:35:00+08:00` |
| `bar.tz` | string | 同战略层（港股 `+08:00` / 美股 EDT·EST） |
| `bar.ktype` | string | `1m` / `5m` / `15m` |
| `direction` | int 枚举 | `+1` / `-1` / `0` |
| `action` | int 枚举 | `+1` 进场 / `-1` 离场 / `0` 不动 |
| `entry_price` | float | 触发价 |
| `trigger` | string 枚举 | 触发类型：`1m_vol_breakout` / `pullback_ma_cross` / `range_touch` 等（有限枚举） |
| `trigger_params` | object(全数值) | 触发的量化参数（如 `breakout_level`、`vol_threshold`、`confirm_bars`） |
| `levels` | object(全 float) | 分钟级 `support/resistance/stop_loss_ref/take_profit_ref` |
| `features` | object(全 float) | 量化依据（如 `ma5m/vol_ratio_1m/spread`），必须全数字 |
| `category` | string | 类别签名，可复现 |
| `confidence` | object | `{win_rate, samples, avg_R}`，回测赋予，策略输出 `null` |

**JSON 例子（港股盈富，战术层）**：
```json
{
  "symbol": "HK.02800",
  "bar": {"time": "2026-07-14T10:35:00+08:00", "tz": "Asia/Hong_Kong (+08:00 北京/港时)", "ktype": "1m"},
  "direction": 1,
  "action": 1,
  "entry_price": 24.56,
  "trigger": "1m_vol_breakout",
  "trigger_params": {"breakout_level": 24.55, "vol_threshold": 1.5, "confirm_bars": 1},
  "levels": {"support": 24.42, "resistance": 24.70, "stop_loss_ref": 24.38, "take_profit_ref": 24.70},
  "features": {"ma5m": 24.48, "vol_ratio_1m": 2.1, "spread": 0.01},
  "category": "1m_vol_breakout+pullback_5m_ma",
  "confidence": {"win_rate": 0.55, "samples": 120, "avg_R": 1.1}
}
```

---

## 三、时间字段时区规则

| 市场 | 时区 | 标注 | 交易时段（本地） |
|---|---|---|---|
| 港股 | `Asia/Hong_Kong` = UTC+8 | `+08:00 北京/港时` | 09:30-12:00 / 13:00-16:00 |
| 美股 | `America/New_York` | `EDT -04:00`（夏令时，3 月第二周日–11 月第一周日）/ `EST -05:00`（冬令时） | 09:30-16:00 美东 |

- 所有 `bar.time` 用 **ISO 8601 带偏移**（`2026-07-14T10:35:00+08:00`），偏移量本身就编码了时区。
- 富途 K 线返回的 `time_key` 无时区后缀，按**市场本地时间**解读（港股=港时、美股=美东）；落盘时**补上偏移**。
- `bar.tz` 字段冗余写明人类可读时区名，双重保险防误读。

---

## 四、回测设计（与 schema 协同，保证精准复现）

### 4.0 优化目标与可用性门槛（先于一切；2026-08-03 改为单一指标 g）

**主优化目标：`g`（每笔对数增长率，最大化）**——凯利框架核心。

固定风险比例 `f`（每笔风险占权益比）下注时，每笔权益乘 `(1+f·net_R)`，`n` 笔后累计收益率 ≈ `e^(n·g) − 1`。`g` 把**净平均赔率 `EV`（=net avg_R）与净赔率方差 `σ²`（=net var_R）统一成单一实数**：

$$g = \mathbb{E}[\ln(1+fR)] \approx f\cdot EV - \tfrac{f^2}{2}(\sigma^2 + EV^2)$$

- 第一项 `f·EV`：均值贡献（正向）——`EV` 越大 `g` 越大。
- 第二项 `−(f²/2)·σ²`：**方差惩罚**（负向）——`σ²` 越大、`f` 越大，`g` 被吃得越多。
- 第三项 `−(f²/2)·EV²`：均值非线性修正（通常较小）。

> 完整推导见 [DayTradingAgent/notes/cumulative-return-derivation.md](https://github.com/xhqing/DayTradingAgent/blob/main/notes/cumulative-return-derivation.md)。

**为什么用 `g` 而非「`EV` + `σ²` 双目标」**：双目标无全序（帕累托权衡难以决策——`EV` 高 `σ²` 也高的策略 vs `EV` 低 `σ²` 低的策略，谁好？），`g` 是单一实数可直接比较排序。且 **`g>0` 才有指数复利**，比 `EV>0` 严格得多——`EV>0` 但 `f` 过大或 `σ²` 过大都会让 `g<0`（长期缩水）。错误公式 `(1+f·EV)ⁿ` 丢掉方差惩罚、系统性诱导过度下注，**禁止用于决策**。

**诊断成分**（`g` 的组成部分，保留以解释策略、指导仓位决策）：

| 成分 | 含义 | 在 `g` 中的角色 |
|---|---|---|
| `net avg_R`（EV） | 净平均赔率 | 均值贡献 `f·EV`（正向） |
| `net var_R` / `std_R`（σ²/σ） | 净赔率方差/标准差 | 方差惩罚 `−(f²/2)σ²`（负向）；同 EV 下 σ² 越小 g 越大 |
| `f*` ≈ `EV/E[R²]` | 近似凯利最优下注 | 诊断：`f*≤0`=无 edge；实践用分数凯利（1/4~1/2 f*），超 2f* 必缩水 |

> 用**净 R**（扣成本）算 `g`：每笔 `net_R_i = R_i − 成本/risk`，`g = mean(ln(1 + f·net_R_i))`。`cost_bps` 按市场（港股 18 含印花税、美股 3，见 4.4）。报告 `g` 用参考 `f=2%`（`BacktestResult.REF_F`）——固定基准使策略间可比；实战 `f` 按 `f*` 的分数凯利定。

**可用性门槛（策略"能用"的最低要求，参考值可按风险偏好调）**：

| 指标 | 门槛 | 说明 |
|---|---|---|
| `g` @ f=2% | > 0 | **每笔对数增长率为正才有复利**（核心门槛）；>0.5%/笔 优秀 |
| `f*` | > 0 | 近似凯利为正 = 有 edge；≤0 直接弃 |
| Profit Factor（总盈利/总亏损） | ≥ 1.3 | >1.5 良好，>2 优秀 |
| 样本数 | ≥ 30 | 统计意义下限（分钟策略通常远超） |
| Max Drawdown | ≤ 20% | 风险上限 |
| 胜率 | ≥ 35% | g 正但胜率过低→连亏风险大、难执行 |

**综合参考（不优化、只看）**：Sharpe（风险调整收益）、Calmar（收益/回撤）。

**一句话**：在"样本足、回撤可控、胜率可执行"的门槛约束下，**最大化 `g`（每笔对数增长率，在参考 f=2% 下）**——`g` 已内含净平均赔率（EV）与净赔率方差（σ²）的权衡，`g>0` 才交付使用。回测产出每笔净 R 记录 → 算 `g`/`EV`/`σ²`/`f*`（优化用），又按 category 分组填 `confidence.win_rate/avg_R`（实时用）。

### 4.1 确定性输入（数据缓存）
- 首次从富途（分钟 K）/长桥（日 K）拉取 → 存 **parquet 快照** 到 `data_cache/`（含时区），回测只读缓存。
- 好处：数据源变动 / 限流不影响回测；**同快照 → 同结果**。
- `data_cache/` 大文件不入库（`.gitignore`），可从源重生。

### 4.2 策略是纯函数
```python
def evaluate(bars: pd.DataFrame, params: dict) -> dict | None:
    # bars = 截至当前根的历史 K 线（含时区）；params = 策略参数
    # 返回战略层/战术层 schema dict，或 None（无信号）
```
- **无随机、无全局状态、无副作用** → 同 `bars` + 同 `params` 永远同输出。

### 4.3 逐根遍历，严防未来函数
- 按时序 `for t in range(start, end)`：`signal = evaluate(bars[:t], params)`，**t 时刻决策只能用 `≤ t` 的数据**。
- `features` / `levels` 必须只用历史数据算（如 `ma5` 只用 t 及之前 5 根）。回测框架自动检测：若输出引用了 `bars[t+1:]` 报错。

### 4.4 成交价假设（统一、保守）
- 信号在 t 根收盘后产生 → **t+1 根开盘价成交**（战略层 = 次日开盘；战术层 = 下一分钟开盘）。
- 这是**保守近似**：实盘是盘中实时执行（可能更优），但回测统一用下一根开盘，避免美化、保证可比。滑点/手续费/税费作为可配置扣减项。
- **成本按市场分开设（2026-08-03 改进）**：`cost_bps`（单边 bps）按标的所属市场自动取——**港股 18**（含印花税买卖各 0.13% + 佣金 + 滑点，印花税是大头）、**美股 3**（零佣金券商 + 滑点）。可用 `params={"cost_bps": X}` 显式覆盖。此前默认 2bps 未算港股印花税，严重高估港股策略（同一盈富策略净 avg_R 从 +0.027 跌到 −0.423）。

### 4.5 出场条件全量化（日内固定止盈止损模式，2026-08-04 定稿）

持仓中逐根检查（任一触发即平，状态机模型：同时最多一仓，禁止重叠持仓）：
- **固定止损**（`stop` 被触及，做多：当根最低价 ≤ stop）→ `exit = stop`（R = −1）；
- **固定止盈**（`take` 被触及，做多：当根最高价 ≥ take）→ `exit = take`（R = +盈亏比）；
- **段末强制平仓**（日内禁跨段）：持仓到午休前（港股 12:00）/ 收盘前（16:00）的最后一根 → `exit = 收盘价`（防隔夜 + 防跨午休）；
- **超时**（持仓根数 ≥ `max_hold_bars`）→ `exit = 收盘价`（兜底）。

**出场价口径**：实盘止损/止盈单是市价单，触及触发价即成交；**日内禁跨段**（段末强平）消灭了跳空，故成交价**恒定 = stop / take**（不取 open、不取 close）。段末/超时强平才用收盘价。

> trailing（`trail_atr>0`）与反向信号（`allow_reverse_exit=True`）作为**可选**能力保留在框架中，但日内固定止盈止损模式下默认关闭——实测在盈富 15m 上纯 trailing 出场 g 全负（趋势延续性不足，trailing 让利润在有限趋势里回吐），固定止盈更优。

**止损价口径（基于成交价反推）**：`stop = entry ∓ stop_atr×ATR`、`take = entry ± take_atr×ATR`，基于实际成交价（t+1 开盘价）反推，`risk = stop_atr×ATR` 干净。

**止损价口径（基于成交价反推，2026-08-03 改进）**：止损/止盈价基于**实际成交价**（t+1 开盘价）反推 `stop = entry ∓ stop_atr×ATR`，而非信号根开盘价——这样 `risk = stop_atr×ATR` 干净（策略层输出的 `stop_loss_ref` 仅作信号时刻参考；回测/实盘成交后由框架按成交价重算）。

### 4.6 每笔记录（全数值）
`{symbol, category, direction, entry_price, exit_price, R, hold_bars, entry_time(tz), exit_time(tz)}`
- `R` = 盈亏 / 单股风险（`entry_price - stop_loss_ref` 做多），标准化盈亏倍数。

### 4.7 置信度查表（confidence 来源）
- 按 `category` 分组聚合所有历史交易 → 统计 `win_rate` / `avg_R` / `samples` → 存 `confidence_table.json`。
- 盘中实时：策略出信号 + `category` → 查表填 `confidence`。
- **样本量门槛**：`samples < 20` → 标 `low_sample: true`，调用方自动降权；查无的 category → `confidence=null`（弃权）。

### 4.8 复现保证
**同 `data_cache` 快照 + 同 `params` + 同策略代码 = 完全相同的回测结果。** 版本化快照与参数（写入结果目录），任何时点重跑可对齐。

---

## 五、可量化性自查清单（写每个策略时逐条对照）

- [ ] 每个输出字段是 **数值** 或 **有限枚举**？（无定性文字）
- [ ] 判定依据在 `features` 里全是数字？（不允许"趋势强"这种词）
- [ ] `levels` 全是价格数值？
- [ ] 时间字段带市场时区（ISO 8601 偏移 + `tz`）？
- [ ] `evaluate` 是纯函数、无随机、无未来函数？
- [ ] 进出场条件都有量化阈值（`entry_params`/`trigger_params`/止损止盈价/`max_hold_bars`）？
