# 日内分时数据源调研（2026-07-14）

为本 skill（`quant/`）下可回测策略选数据底座。**结论先行：富途 OpenD 有约 5.5 年分钟 K（2021-01-21 起），足够做分钟级日内策略回测——免费、已接入、即用，是关键突破口。** 故 MVP 不必局限于日 K 战略层，战术层（分钟级入场触发）也能回测。

---

## 一、本项目已接入的三个免费源（实测）

### 1. 长桥 CLI（`bash /tmp/lb.sh`）

| 数据 | 深度 | 备注 |
|---|---|---|
| 日 K | ✅ 十几年 | `kline history` 单次上限 **1000 根**（>1000 报错），分页拼接（每页≈4 年）可拉到上市 |
| 分钟 K | ❌ 太浅 | count≤1000：1m 仅最近 3-4 天、5m≈3 周、1h≈3 月 |
| intraday --date | ❌ 历史日期空 | 实测 20260713/0710/0707 全 `timeshares=[]`，只当日 live |
| capital --flow / calc-index | ❌ 只当日/当前 | 资金流、换手率、量比均无历史 |

- 符号格式：`代码.市场` 后缀（`02800.HK`/`SPY.US`）
- **定位**：日 K 回测可用；分钟/资金流/盘口回测**不可用**

### 2. 富途 OpenD（`futu-api`，127.0.0.1:11111）— 关键突破

实测（标的 `HK.02800` 盈富 / `US.SPY`，`max_count=100000`）：

| 数据 | 深度 | 实测 |
|---|---|---|
| 日 K | ✅ ~20 年 | rows=4930，first=**2006-06-30**，last=2026-07-14 |
| **1m 分钟 K** | ✅ **约 5.5 年** | first=**2021-01-21**；单次返 10 万根（≈1.2 年，从最早返），全量 5.5 年需约 5 页 |
| **5m 分钟 K** | ✅ 约 5.5 年 | rows=88242，2021-01-21→2026-07-14，单次够全量 |
| **15m 分钟 K** | ✅ 约 5.5 年 | rows=29414，2021-01-21→2026-07-14 |
| 美股 SPY 1m | ✅ 约 5 年 | first=2021-01-21，单次 10 万根 |

- 接口：`ctx.request_history_kline(code, start, end, ktype=KLType.K_1M/K_5M/K_DAY..., max_count)`（注意是 `request_history_kline` 不是 `get_history_kline`）
- 符号格式：`市场.代码` **前缀**（`HK.02800`/`US.SPY`），与长桥后缀**相反**，混用报错
- 启动：`open ~/FutuOpenD/FutuOpenD.app`，登录后 11111 监听；离线需先启动（未登录 ECONNREFUSED）
- 分钟 K 历史下限约 **2021-01-21**（富途免费版限制）
- **定位**：**分钟级日内策略回测的主力数据源**；历史盘口/经纪队列深度未测（本调研聚焦 K 线）

### 3. 老虎 SDK（`tigeropen`）— 待修

- 实测 `QuoteClient` 初始化报 `private key can not be empty`（配置加载问题，待排查 `~/.tigeropen/` 加载方式）
- 港股实时 Lv2 depth 实测可用（2026-07-10 盘中实证），但**历史 K 线深度未测通**
- 美股无行情权限
- **定位**：港股实时 WebSocket 辅助；历史回测暂不可用

---

## 二、付费 / 第三方渠道（调研）

### 美股

| 源 | 分钟/tick 历史 | 价格 | 接入 |
|---|---|---|---|
| **Polygon / Massive** | 分钟 + tick，20+ 年 | 免费 EOD；$29–79/mo（延迟+~2 年分钟）；**$199/mo**（实时+20+年分钟/tick） | REST |
| **Databento** | tick/秒/分/时/日，15+ 年 | **按量 $/GB**，新用户 **$125 免费额度**；5 年小时数据 <$4 | REST/FTP |
| **Alpaca** | 分钟 | 便宜，含券商集成 | REST |
| **IQFeed** | 实时+历史 tick | 订阅制，零售 algo 常用 | TCP |

### 港股

| 源 | 分钟/tick 历史 | 价格 | 接入 |
|---|---|---|---|
| **HKEX Data Marketplace** | 官方全量 | 询价（贵，权威） | 门户 |
| **Tick Data** | 港股 2008-05 起，预置 1min bar + tick | 询价（机构级，贵） | 下载 |
| **EODData** | 1min bar，30 年 | 订阅制 | 下载 |
| **AllTick** | 港/美/A 股 tick，~17 万标的 | 订阅制 | REST/WS |

### 国内（中文友好）

| 源 | 港股分钟 | 价格 | 备注 |
|---|---|---|---|
| **Tushare Pro** | ✅ 1/5/15/30/60min（120 积分） | 积分制，单次 8000 行分页 | 美股通用接口也有 |
| **AKShare** | 部分 | **免费开源** | A 股分钟全，港/美部分 |
| **聚宽 JoinQuant** | ✅ | 会员制 | **内置日线/分钟回测平台**，一体化最省事 |

---

## 三、MVP 推荐

- **首选富途 OpenD 分钟 K**（免费、已接入、5.5 年）：MVP 做**分钟级日内策略**，战术层（分钟入场触发）可回测。
- 数据层 `data.py`：统一封装——富途拉分钟 K（前缀符号 `HK.02800`）为主，长桥拉日 K（后缀符号 `02800.HK`）互补与交叉验证。
- **未来要 tick 级 / 更深历史**：Databento（按量、$125 免费额度试水最划算）或 Polygon $199/mo（美股全量）；港股官方深历史走 HKEX/Tick Data（贵）。

---

## 四、接入备忘（踩坑）

1. **符号格式两源相反**：富途 `HK.02800`（前缀）vs 长桥 `02800.HK`（后缀），混用报错。
2. **富途分钟 K 单次 `max_count` 上限 10 万根**，1m 全量 5.5 年需分页约 5 次（按时间窗口往前推）。
3. **富途 OpenD 离线**：`open ~/FutuOpenD/FutuOpenD.app` 启动；未登录则 11111 不监听（ECONNREFUSED）。
4. **macOS 无 `timeout` 命令**：富途/老虎调用用 `signal.SIGALRM` 做硬超时防挂起。
5. 富途接口名是 `request_history_kline`（不是 `get_history_kline`）。

---

## 参考来源

- [HKEX Data Marketplace](https://www.hkex.com.hk/Services/Market-Data-Services/Historical-Data-Services/HKEX-Data-Marketplace?sc_lang=en)
- [Tick Data – Hong Kong Equities](https://www.tickdata.com/equity-data/hong-kong-equities)
- [Polygon / Massive Pricing](https://massive.com/pricing)
- [Databento Pricing](https://databento.com/pricing)
- [QuantPedia – Best Historical Market Data Providers](https://quantpedia.com/best-historical-market-data-providers/)
- [Cracking Markets – TradeStation vs Polygon vs Alpaca](https://www.crackingmarkets.com/comparing-affordable-intraday-data-sources-tradestation-vs-polygon-vs-alpaca/)
- [Tushare 港股分钟行情](https://tushare.pro/document/2?doc_id=304)
- [AKShare 文档](https://akshare.akfamily.xyz/data/stock/stock.html)
- [聚宽 JoinQuant](https://www.joinquant.com/help/data/stock?f=home$m=footer)
