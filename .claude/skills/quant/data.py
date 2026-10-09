#!/usr/bin/env python3
"""数据层：K 线拉取 + parquet 缓存 + 增量更新 + 快照备份。

数据源：
- 富途 OpenD（futu-api，127.0.0.1:11111）为主力：分钟 K（1m/5m/15m，约 5.5 年，2021-01-21 起）
  + 日 K（约 20 年）；符号前缀格式 `HK.02800` / `US.SPY`。
- 长桥 CLI 日 K（后缀格式 `02800.HK`）作交叉验证：接口脚本 /tmp/lb.sh 为本机临时脚本，
  非项目资产，MVP 不依赖；后续有需要再接入（数据源调研见 data-sources.md）。

用法：
  python3 data.py fetch HK.02800               # 拉单标的全量（含增量）落 parquet 缓存
  python3 data.py fetch-all                    # 拉股票池全量
  python3 data.py snapshot [--upload]          # 打包快照 + manifest；--upload 上传 GitHub Release
  python3 data.py restore snapshot-2026-08-03  # 从 Release 下载校验解压回 data_cache/
  python3 data.py verify [--manifest <dir>]    # 校验缓存完整性 / 校验快照包 sha256

时区铁律：落盘 time 列一律带市场时区——港股 Asia/Hong_Kong（固定 +08:00），
美股 America/New_York（自动跟随 EDT/EST 切换）。富途 time_key 无时区后缀，
按市场本地时间解读后 localize 补时区（详见 schema.md 第三节）。

确定性：回测只读 data_cache/ 下的 parquet 快照，同快照 → 同结果。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import signal
import subprocess
import sys
import tarfile
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------- 常量

# 本文件所在目录 = quant skill 目录（data.py / backtest.py / data_cache 都在这）
Q_DIR = Path(__file__).resolve().parent
DATA_CACHE = Q_DIR / "data_cache"          # parquet 缓存（已 gitignore）
PROJECT_ROOT = Q_DIR.parent.parent.parent  # 项目根 Intraday
TMP = PROJECT_ROOT / "tmp"                 # 临时产物目录（已 gitignore）
SNAPSHOT_DIR = TMP / "snapshot"            # 快照打包输出目录
RESTORE_DIR = TMP / "restore"              # 恢复下载目录

BACKUP_REPO = "xhqing/market-data-backup"  # 私有备份仓库（Release 挂快照包）
FUTU_HOST = "127.0.0.1"
FUTU_PORT = 11111
MAX_COUNT = 100_000        # 富途单次 request_history_kline 上限（实测 10 万根）
REQUEST_TIMEOUT = 60       # 单次请求硬超时（秒），防 OpenD 挂起
HIST_LOWER_BOUND = "2020-01-01 00:00:00"   # 全量拉取起点：早于所有历史下限，富途从最早可给处返回

# 周期 → (富途 KLType, 分钟步长) ；步长用于增量更新计算拉取起点
KTYPES = {
    "1m": ("K_1M", 1),
    "5m": ("K_5M", 5),
    "15m": ("K_15M", 15),
    "DAY": ("K_DAY", 1440),
}

# 市场 → 时区（铁律：时间必带市场时区）
MARKET_TZ = {"HK": "Asia/Hong_Kong", "US": "America/New_York",
             "SH": "Asia/Shanghai", "SZ": "Asia/Shanghai"}

# 股票池（预先定死，防幸存者偏差：按「流动性规则」选定后不再按回测表现增删。
# MVP 先放几个验证链路的标的，正式池子按流动性排名规则扩充后写入本列表）
STOCK_POOL = [
    # 港股（大盘股 + ETF，富途前缀 HK.）—— 恒指/国企成分 + 科技，高流动性、上市久
    "HK.02800",  # 盈富基金（恒指 ETF）
    "HK.00005",  # 汇丰控股
    "HK.00001",  # 长和
    "HK.00388",  # 港交所
    "HK.00688",  # 中国海油
    "HK.00700",  # 腾讯控股
    "HK.00939",  # 建设银行
    "HK.00941",  # 中国移动
    "HK.01109",  # 华润置地
    "HK.01299",  # 友邦保险
    "HK.02318",  # 中国平安
    "HK.03690",  # 美团
    "HK.09988",  # 阿里巴巴
    # 美股（大盘股 + ETF，富途前缀 US.）—— 覆盖七大行业，高流动性、上市久
    # ETF
    "US.SPY",    # 标普 500 ETF
    "US.QQQ",    # 纳斯达克 100 ETF
    "US.DIA",    # 道琼斯 ETF
    "US.IWM",    # 罗素 2000 ETF
    # 科技
    "US.AAPL",   # 苹果
    "US.MSFT",   # 微软
    "US.GOOG",   # 谷歌
    "US.AMZN",   # 亚马逊
    "US.TSLA",   # 特斯拉
    "US.NVDA",   # 英伟达
    "US.META",   # Meta
    "US.NFLX",   # 奈飞
    "US.AMD",    # AMD
    "US.INTC",   # 英特尔
    "US.ORCL",   # 甲骨文
    "US.IBM",    # IBM
    "US.CSCO",   # 思科
    # 金融
    "US.JPM",    # 摩根大通
    "US.GS",     # 高盛
    "US.MS",     # 摩根士丹利
    "US.V",      # Visa
    "US.MA",     # 万事达
    "US.BAC",    # 美国银行
    # 消费
    "US.WMT",    # 沃尔玛
    "US.PG",     # 宝洁
    "US.KO",     # 可口可乐
    "US.MCD",    # 麦当劳
    "US.DIS",    # 迪士尼
    "US.NKE",    # 耐克
    # 医疗
    "US.UNH",    # 联合健康
    "US.JNJ",    # 强生
    "US.PFE",    # 辉瑞
    "US.LLY",    # 礼来
    # 能源
    "US.XOM",    # 埃克森美孚
    "US.CVX",    # 雪佛龙
    # 工业
    "US.CAT",    # 卡特彼勒
    "US.BA",     # 波音
    # 低相关资产 ETF（与股票低相关/负相关，用于降低组合波动——黄金/债券/商品/地产/新兴市场）
    "US.GLD",    # 黄金 ETF（危机时涨，对冲股票下跌）
    "US.TLT",    # 20+年国债 ETF（避险资金流入，与股票负相关）
    "US.IEF",    # 7-10年国债 ETF（中期国债）
    "US.DBC",    # 大宗商品 ETF（商品，低相关）
    "US.VNQ",    # 地产 REIT ETF
    "US.EEM",    # 新兴市场股票 ETF
]

PARQUET_COLS = ["time", "open", "high", "low", "close", "volume", "turnover"]


# ---------------------------------------------------------------- 工具

class DataError(Exception):
    """数据层错误（用户可读消息）。"""


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


@contextmanager
def deadline(seconds: int):
    """SIGALRM 硬超时：macOS 无 timeout 命令，富途请求可能挂起，用闹钟兜底。"""
    def _handler(signum, frame):
        raise TimeoutError(f"请求超时（>{seconds}s），OpenD 可能无响应")
    old = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def symbol_normalize(sym: str) -> str:
    """统一富途前缀格式：`02800.HK`（长桥后缀）→ `HK.02800`（富途前缀）。"""
    s = sym.strip().upper()
    if "." not in s:
        raise DataError(f"符号格式错误：{sym}，应为 HK.02800 或 02800.HK")
    code, market = s.split(".", 1)
    if market in ("HK", "US"):      # 后缀格式（市场在后，长桥）：02800.HK → HK.02800
        return f"{market}.{code}"
    if code in ("HK", "US", "SH", "SZ"):    # 前缀格式（市场在前，富途）：HK.02800 / SH.513050
        return f"{code}.{market}"
    raise DataError(f"无法识别市场：{sym}（仅支持 HK / US / SH / SZ）")


def market_of(symbol: str) -> str:
    return symbol_normalize(symbol).split(".")[0]


def tz_of(symbol: str) -> str:
    return MARKET_TZ[market_of(symbol)]


def cache_path(symbol: str, ktype: str) -> Path:
    return DATA_CACHE / symbol_normalize(symbol) / f"{ktype}.parquet"


def iso_now() -> str:
    """本地当前时间，ISO 8601 带偏移（铁律：时间必带时区）。"""
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- 拉取（富途 OpenD）

def _futu_open() -> "OpenQuoteContext":
    from futu import OpenQuoteContext
    try:
        return OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
    except Exception as e:  # OpenD 未启动 / 未登录 → ECONNREFUSED 等
        raise DataError(
            f"连接 OpenD 失败（{FUTU_HOST}:{FUTU_PORT}）：{e}\n"
            f"请先启动：open ~/FutuOpenD/FutuOpenD.app（登录后监听 11111）"
        ) from e


def _fetch_page(ctx, symbol: str, ktype: str, start: str, end: str,
                page_req_key: str | None = None) -> tuple[pd.DataFrame, str | None]:
    """单次分页请求，返回 (数据, 下一页 page_req_key)。page_req_key 为 None 表示没有更多页。

    start/end 为市场本地时间字符串 'yyyy-MM-dd HH:mm:ss'，必须都传：
    富途对 start=str 且 end=None 只返回 365 天，全量必须显式指定 end。
    """
    from futu import KLType, RET_OK
    kl = getattr(KLType, KTYPES[ktype][0])
    with deadline(REQUEST_TIMEOUT):
        ret, data, prk = ctx.request_history_kline(
            symbol_normalize(symbol), start, end, ktype=kl, max_count=MAX_COUNT,
            page_req_key=page_req_key,
        )
    if ret != RET_OK:
        raise DataError(f"拉取失败 {symbol} {ktype}：{data}")
    if data is None or data.empty:
        return pd.DataFrame(columns=PARQUET_COLS), None
    return data, prk


def _fetch_range(ctx, symbol: str, ktype: str, start: str | None, end: str | None) -> pd.DataFrame:
    """分页拉取 [start, end] 全量（官方 page_req_key 分页），去重、补时区、规整列。

    全量起点按 ktype 区分：日 K 从 "2000-01-01"（富途日 K 可到 2006，~20 年）；
    分钟 K 从 HIST_LOWER_BOUND（2020，富途分钟 K 仅近 5.5 年）。增量时用传入的 start。
    """
    tz = tz_of(symbol)
    if start is None:
        start = "2000-01-01" if ktype == "DAY" else HIST_LOWER_BOUND
    frames = []
    prk = None
    while True:
        page, prk = _fetch_page(ctx, symbol, ktype, start, end, prk)
        if page.empty:
            break
        frames.append(page)
        if prk is None:
            break
    if not frames:
        return pd.DataFrame(columns=PARQUET_COLS)

    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset="time_key")
    df["time"] = pd.to_datetime(df["time_key"]).dt.tz_localize(tz, ambiguous="infer")
    df = df.sort_values("time").reset_index(drop=True)
    df["volume"] = df["volume"].astype("int64")
    return df[PARQUET_COLS]


def fetch_symbol(ctx, symbol: str, ktypes: list[str] | None = None, force: bool = False) -> dict[str, int]:
    """拉一个标的指定周期全量（增量合并）落 parquet。返回 {ktype: 写入根数}。"""
    symbol = symbol_normalize(symbol)
    ktypes = ktypes or list(KTYPES)
    wrote = {}
    for kt in ktypes:
        if kt not in KTYPES:
            raise DataError(f"未知周期 {kt}（可选：{', '.join(KTYPES)}）")
        path = cache_path(symbol, kt)
        path.parent.mkdir(parents=True, exist_ok=True)

        old: pd.DataFrame = pd.DataFrame()
        start = None
        if path.exists() and not force:
            old = pd.read_parquet(path)
            if not old.empty:
                # 增量：从本地最后时间起步（富途按市场本地时间解读，重叠去重）
                last_local = old["time"].iloc[-1].tz_convert(tz_of(symbol))
                start = last_local.strftime("%Y-%m-%d %H:%M:%S")

        end = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        new = _fetch_range(ctx, symbol, kt, start, end)
        if new.empty and old.empty:
            log(f"  {kt}: 无数据（{symbol} 该周期可能无行情）")
            continue

        merged = pd.concat([old, new], ignore_index=True)
        merged = merged.drop_duplicates(subset="time").sort_values("time").reset_index(drop=True)
        merged.to_parquet(path, index=False)
        added = len(merged) - len(old)
        wrote[kt] = added
        log(f"  {kt}: {len(merged):,} 根（新增 {added:,}）→ {path.relative_to(PROJECT_ROOT)}")
    return wrote


# ---------------------------------------------------------------- 读缓存（供回测 / 策略共用）

def load_cache(symbol: str, ktype: str) -> pd.DataFrame:
    """读 parquet 缓存，time 列为市场时区 aware 的 datetime（铁律：带时区）。无缓存抛 DataError。"""
    path = cache_path(symbol, ktype)
    if not path.exists():
        raise DataError(f"缓存不存在：{path.relative_to(PROJECT_ROOT)}，先跑 `python3 data.py fetch {symbol}`")
    df = pd.read_parquet(path)
    if df.empty:
        raise DataError(f"缓存为空：{path.relative_to(PROJECT_ROOT)}")
    return df


# ---------------------------------------------------------------- 快照备份（GitHub Release）

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _market_pack(root: Path, market: str, out_dir: Path) -> Path:
    """把一个市场下所有符号的缓存打包成 tar.gz。包内保留 `HK.02800/5m.parquet` 结构。"""
    members = sorted(p for p in root.iterdir() if p.is_dir() and p.name.startswith(market + "."))
    if not members:
        raise DataError(f"缓存里没有 {market} 市场的数据，先跑 fetch")
    out = out_dir / f"{market}.parquet.tar.gz"
    with tarfile.open(out, "w:gz") as tar:
        for member in members:
            for f in sorted(member.glob("*.parquet")):
                tar.add(f, arcname=f"{member.name}/{f.name}")
    return out


def _build_manifest(tag: str, assets: list[Path]) -> dict:
    return {
        "tag": tag,
        "created_at": iso_now(),
        "source": "futu-opend",
        "note": "行情数据快照备份（私有自用，禁止公开再分发）",
        "symbols": [p.name for p in sorted(DATA_CACHE.iterdir()) if p.is_dir()],
        "files": [
            {"name": p.name, "size": p.stat().st_size, "sha256": _sha256(p)}
            for p in sorted(assets)
        ],
    }


def cmd_snapshot(args) -> None:
    tag = args.tag or f"snapshot-{dt.date.today().isoformat()}"
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    if not args.no_refresh:
        log("先确保缓存最新（增量拉取股票池）…")
        _fetch_pool()

    # 按缓存里实际存在的市场打包（HK / US），没数据的市场不强制
    markets = sorted({p.name.split(".")[0] for p in DATA_CACHE.iterdir() if p.is_dir()})
    if not markets:
        raise DataError(f"缓存里没有数据：{DATA_CACHE}，先跑 fetch")
    assets = [_market_pack(DATA_CACHE, m, SNAPSHOT_DIR) for m in markets]
    manifest = _build_manifest(tag, assets)
    manifest_path = SNAPSHOT_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for a in assets:
        log(f"打包完成：{a.relative_to(PROJECT_ROOT)}（{a.stat().st_size / 1e6:.1f} MB）")
    log(f"manifest：{manifest_path.relative_to(PROJECT_ROOT)}")

    if args.upload:
        _upload_release(tag, assets + [manifest_path], manifest)


def _upload_release(tag: str, files: list[Path], manifest: dict) -> None:
    notes = (
        f"行情数据快照备份（私有自用）。\n\n"
        f"- 数据源：富途 OpenD（分钟 K 约 5.5 年 + 日 K 约 20 年）\n"
        f"- 标的：{len(manifest['symbols'])} 个：{', '.join(manifest['symbols'])}\n"
        f"- 文件校验：sha256 见 manifest.json\n"
        f"- 恢复：`python3 data.py restore {tag}`"
    )
    cmd = [
        "gh", "release", "create", tag, "-R", BACKUP_REPO,
        "--title", f"行情快照 {tag}",
        "--notes", notes,
        *[str(f) for f in files],
    ]
    log(f"上传 Release {tag} → {BACKUP_REPO} …")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        # tag 已存在是常见情况（当天重复备份），给出明确提示
        raise DataError(f"上传失败：{proc.stderr.strip()}\n若 tag 已存在，用 --tag 指定新标签")
    log(proc.stdout.strip())


def cmd_restore(args) -> None:
    tag = args.tag
    RESTORE_DIR.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["gh", "release", "download", tag, "-R", BACKUP_REPO, "--dir", str(RESTORE_DIR),
         "--pattern", "*.tar.gz", "--pattern", "manifest.json"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise DataError(f"下载失败：{proc.stderr.strip()}\n先确认 Release 存在：gh release view {tag} -R {BACKUP_REPO}")
    log(proc.stdout.strip())

    manifest_path = RESTORE_DIR / "manifest.json"
    if not manifest_path.exists():
        raise DataError("下载缺少 manifest.json，中止恢复（无法校验完整性）")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # 校验 sha256 后再解压
    for entry in manifest["files"]:
        f = RESTORE_DIR / entry["name"]
        if not f.exists():
            raise DataError(f"缺少文件 {entry['name']}，中止恢复")
        actual = _sha256(f)
        if actual != entry["sha256"]:
            raise DataError(f"校验失败：{entry['name']} sha256 不匹配（{actual[:12]} ≠ {entry['sha256'][:12]}）")
        log(f"校验通过：{entry['name']}")

    DATA_CACHE.mkdir(parents=True, exist_ok=True)
    for entry in manifest["files"]:
        if not entry["name"].endswith(".tar.gz"):
            continue
        with tarfile.open(RESTORE_DIR / entry["name"], "r:gz") as tar:
            tar.extractall(DATA_CACHE, filter="data")  # filter="data" 防路径穿越
        log(f"解压完成：{entry['name']} → {DATA_CACHE.relative_to(PROJECT_ROOT)}")
    log("恢复完成。提示：恢复会覆盖同名缓存文件，后续回测即基于恢复的快照。")


# ---------------------------------------------------------------- 校验

def cmd_verify(args) -> None:
    if args.manifest:
        _verify_manifest(Path(args.manifest))
    else:
        _verify_cache()


def _verify_manifest(dir_path: Path) -> None:
    """校验快照包：对照 manifest 里的 sha256。"""
    manifest_path = dir_path / "manifest.json"
    if not manifest_path.exists():
        raise DataError(f"目录里没有 manifest.json：{dir_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ok = True
    for entry in manifest["files"]:
        f = dir_path / entry["name"]
        actual = _sha256(f) if f.exists() else None
        match = actual == entry["sha256"]
        ok &= match
        log(f"  {'✅' if match else '❌'} {entry['name']}（{entry['size'] / 1e6:.1f} MB）")
    if not ok:
        raise DataError("有文件校验失败")
    log(f"manifest 校验全部通过（{manifest['tag']}，{len(manifest['files'])} 个文件）")


def _verify_cache() -> None:
    """校验本地缓存：每个 parquet 可读、time 带时区且单调、无 NaN、步长合法。"""
    if not DATA_CACHE.exists():
        raise DataError(f"缓存目录不存在：{DATA_CACHE}，先跑 fetch")
    issues = []
    checked = 0
    for symbol_dir in sorted(DATA_CACHE.iterdir()):
        if not symbol_dir.is_dir():
            continue
        for f in sorted(symbol_dir.glob("*.parquet")):
            checked += 1
            try:
                df = pd.read_parquet(f)
                kt = f.stem
                if df.empty:
                    issues.append(f"空文件 {f}")
                    continue
                t = df["time"]
                if t.dt.tz is None:
                    issues.append(f"time 无时区 {f}")
                elif not t.is_monotonic_increasing:
                    issues.append(f"time 非单调 {f}")
                elif df.isna().any().any():
                    issues.append(f"含 NaN {f}")
                else:
                    # 步长合法性：相邻时间差应等于周期分钟数（美股午盘连续、港股 12-13 点休市，
                    # 用众数校验主步长即可，允许节假日/停牌跳空）
                    step = t.dt.tz_convert("UTC").diff().dropna().dt.total_seconds() / 60
                    mode = step.mode().iloc[0] if not step.empty else None
                    expected = KTYPES[kt][1]
                    if mode is not None and abs(mode - expected) > 0.01:
                        issues.append(f"步长异常 {f}：众数 {mode} 分钟 ≠ {expected}")
            except Exception as e:
                issues.append(f"读取失败 {f}：{e}")
    for issue in issues:
        log(f"  ❌ {issue}")
    if issues:
        raise DataError(f"缓存校验发现 {len(issues)} 个问题")
    log(f"  ✅ 缓存校验全部通过（{checked} 个 parquet 文件）")


# ---------------------------------------------------------------- CLI

def _fetch_pool(ktypes: list[str] | None = None, force: bool = False) -> None:
    ctx = _futu_open()
    try:
        for sym in STOCK_POOL:
            log(f"拉取 {sym} …")
            fetch_symbol(ctx, sym, ktypes=ktypes, force=force)
    finally:
        ctx.close()


def cmd_fetch(args) -> None:
    ctx = _futu_open()
    try:
        fetch_symbol(ctx, args.symbol, args.ktypes)
    finally:
        ctx.close()


def cmd_fetch_all(args) -> None:
    ktypes = [args.ktype] if args.ktype else None
    _fetch_pool(ktypes, force=args.force)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="data.py",
        description="K 线拉取 + parquet 缓存 + 增量更新 + 快照备份（量化回测数据层）",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("fetch", help="拉单标的全量（含增量）")
    p.add_argument("symbol", help="符号，富途前缀格式 HK.02800（兼容 02800.HK）")
    p.add_argument("--ktypes", nargs="+", default=None, help="周期列表，默认全部（1m/5m/15m/DAY）")
    p.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("fetch-all", help="拉股票池全量（含增量更新）")
    p.add_argument("--ktype", default=None, help="只拉指定周期（如 DAY），默认全部（1m/5m/15m/DAY）")
    p.add_argument("--force", action="store_true", help="忽略本地缓存全量重拉（向前扩历史，覆盖现有）")
    p.set_defaults(fn=cmd_fetch_all)

    p = sub.add_parser("snapshot", help="打包快照 + manifest（可选上传 GitHub Release）")
    p.add_argument("--tag", default=None, help="Release 标签，默认 snapshot-<今天>")
    p.add_argument("--upload", action="store_true", help="上传到备份仓库 Release")
    p.add_argument("--no-refresh", action="store_true", help="不先增量拉取，直接打包现有缓存")
    p.set_defaults(fn=cmd_snapshot)

    p = sub.add_parser("restore", help="从备份仓库 Release 下载校验解压")
    p.add_argument("tag", help="Release 标签，如 snapshot-2026-08-03")
    p.set_defaults(fn=cmd_restore)

    p = sub.add_parser("verify", help="校验缓存完整性；--manifest 校验快照包 sha256")
    p.add_argument("--manifest", default=None, help="快照目录（含 manifest.json），校验其 sha256")
    p.set_defaults(fn=cmd_verify)

    args = parser.parse_args()
    try:
        args.fn(args)
    except (DataError, TimeoutError) as e:
        log(f"错误：{e}")
        sys.exit(2)


if __name__ == "__main__":
    main()
