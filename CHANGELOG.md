# Changelog

本文件记录 gridtrader 每个版本的主要变更，格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased]

### 变更（AutoMemory 遗留文件不入库：memory/ 与 settings 模板归档移出）

- **为什么改**（2026-10-09）：全局 AutoMemory 已弃用（现行约定「新建项目一律不配 `.claude/memory/`」），兄弟仓库均已不含此类文件——Swing、主仓库 QuantStrategistAgent（Markowitz）的 `.claude/` 仅保留 `skills/quant/`；Intraday 拆分时也已删除同类文件（其 CHANGELOG 记录理由：memory 内容与门面仓库绑定、settings 属本机配置）。本仓库 2026-08-29 首次同步时带入的 `memory/`（2 文件）、`settings.json`、`settings.local.example.json` 属历史遗留，不应入库（否则与家族现状和现行约定分叉）。
- **改了什么**（2026-10-09）：`memory/README.md`、`memory/MEMORY.md`、`settings.json`、`settings.local.example.json` 四个文件从工作区移出、归档至本机 `tmp/removed-automemory-scaffold-20261009/`（`tmp/` 已 gitignore，不入库、可回溯）；本机配置文件 `settings.local.json` 保留原位（已被 `.gitignore` 隔离、不入库）。入库的 `.claude/` 与家族现状对齐：仅 `skills/quant/` 超集副本。

### 变更（体系叙事同步：删去 Victor / DayTradingAgent 关联表述，量化策略独立开发）

- **为什么改**（2026-09-19）：主仓库 QuantStrategistAgent 叙事修正（用户裁定：Markowitz 量化策略独立开发、与 DayTradingAgent 无关系），随附嵌文同步清理，避免旧叙事继续误导。
- **改了什么**（2026-09-19）：`CLAUDE.md` 随附嵌文同步主仓库 5 处修订（定位句独立开发、第 4 步沉淀策略库、独立边界块、中性化表述）。本仓库其余文件零改动（`g` 公式推导出处链接为参考文献引用，保留）。

### 变更（CLAUDE.md 删去「由 Claude Code 自动加载」说明句）

- **为什么改**：用户 2026-09-12 要求 CLAUDE.md 不再强调本文由 Claude Code 加载，团队全部项目的 CLAUDE.md 统一清理此类语句。
- **改了什么**（2026-09-12）：`CLAUDE.md` 开头角色定位行删去句尾「本文件由 Claude Code 在每次会话开头自动加载。」，角色描述本身保留。

### 新增（纳入 QuantStrategistAgent（Markowitz）子项目体系：`.claude/` 超集关系建立）

- **为什么改**：用户指定 gridtrader 由 QuantStrategistAgent（Markowitz）负责维护。按全局规则「Agent 项目与子项目的 `.claude/` 超集关系」（2026-08-10 立），Agent 项目 `.claude/` 为权威源、子项目 `.claude/` 为其超集——保证用户只操作子项目时，子项目也体现该项目归 Markowitz 负责。
- **改了什么**：
  - 新增 `.claude/` 目录：从 QuantStrategistAgent 逐字节同步全部文件——`memory/`（MEMORY.md / README.md）、`settings.json`、`settings.local.example.json`、`settings.local.json`（本机配置，已被上方 `.gitignore` 隔离）、`skills/quant/`（量化策略开发 skill：schema、backtest、data、三个策略模块、可信度查表等）。运行时数据不同步：`skills/quant/data_cache/`（45 MB parquet 行情缓存，可从数据源重生）、`output/`（回测产物）、`__pycache__/`（字节码缓存）。
  - 新增本文件 `CLAUDE.md`：子项目自身说明 + QuantStrategistAgent `.claude/CLAUDE.md` 全文随附（带指代说明，「本项目」均指 QuantStrategistAgent）。
  - 新增项目标配文件：`.gitignore`、`VERSION`、`CHANGELOG.md`（本文件）。
  - 权威源侧同步：QuantStrategistAgent `CLAUDE.md` 新增「子项目清单」节登记本子项目。
- **边界**：`data/` 下样例 CSV（沪深指数 / 个股 / 币安分段）为仓库原有跟踪内容，保持不动。
