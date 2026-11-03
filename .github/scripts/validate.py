#!/usr/bin/env python3
"""仓库基础卫生检查 —— 团队仓库通用的 CI 门禁。

检查四类问题，都是「改 A 坏 B」式回归最容易踩到的：

1. 必备文件缺失：README.md、CHANGELOG.md（团队统一标配）
2. 敏感文件被误提交：.env、私钥、密码库、凭据文件
3. 被跟踪的超大文件：误把大二进制 / 数据集塞进仓库
4. 相对路径链接失效：README 里指向的本地文件被删或改名后忘了同步

设计原则：**只做确定性、低误报的检查**。这个脚本是 required status check，
一旦误报就会卡住所有正常 PR，所以宁可少查，不可瞎查。
"""

import os
import re
import subprocess
import sys

MAX_MB = 10
MAX_BYTES = MAX_MB * 1024 * 1024

# 敏感文件名模式：命中即失败（示例 / 模板文件豁免）
SENSITIVE_PATTERNS = [
    (re.compile(r"^\.env$"), "环境变量文件"),
    (re.compile(r"^\.env\.[^/]+$"), "环境变量文件"),
    (re.compile(r".+\.(pem|p12|pfx|jks|kdbx)$", re.I), "证书 / 密钥 / 密码库"),
    (re.compile(r".+\.(key|keystore)$", re.I), "密钥文件"),
    (re.compile(r"^id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$"), "SSH 私钥"),
    (re.compile(r"^\.npmrc$"), "可能含 token 的 npm 配置"),
    (re.compile(r"^\.netrc$"), "可能含凭据的 netrc"),
    (re.compile(r"^credentials\.json$"), "凭据文件"),
]

# 示例 / 模板 / 占位符性质的豁免后缀
SENSITIVE_EXEMPT_SUFFIXES = (
    ".example", ".sample", ".template", ".dist", ".defaults",
)

# 链接检查只覆盖这两个文件（其余文档不查，避免过度约束）
LINK_DOCS = ("README.md", "README_cn.md")

# markdown 链接：捕获 [text](target) 与 ![alt](target)
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")

SKIP_DIRS = {".git", "node_modules", "tmp", "venv", ".venv", "__pycache__",
             "data_cache", "build", ".build", "dist", "artifacts"}


def fail(msg):
    print(f"  ❌ {msg}")
    return False


def ok(msg):
    print(f"  ✅ {msg}")
    return True


def tracked_files():
    out = subprocess.run(
        ["git", "ls-files", "-z"], capture_output=True, text=True, check=True
    ).stdout
    return [f for f in out.split("\0") if f]


def check_required_files(files):
    print("[1/4] 必备文件")
    good = True
    for name in ("README.md", "CHANGELOG.md"):
        if name in files:
            ok(f"{name} 存在")
        else:
            good = fail(f"缺少 {name}（团队统一标配）") and good
    return good


def check_sensitive(files):
    print("[2/4] 敏感文件未被跟踪")
    bad = []
    for f in files:
        base = os.path.basename(f)
        if base.endswith(SENSITIVE_EXEMPT_SUFFIXES):
            continue
        for pattern, kind in SENSITIVE_PATTERNS:
            if pattern.match(base):
                bad.append(f"{f}（{kind}）")
                break
    if bad:
        for b in bad:
            print(f"  ❌ 发现疑似敏感文件被跟踪：{b}")
        return False
    ok(f"{len(files)} 个被跟踪文件，无敏感文件")
    return True


def check_large_files(files):
    print(f"[3/4] 被跟踪文件不超过 {MAX_MB} MB")
    big = []
    for f in files:
        try:
            size = os.path.getsize(f)
        except OSError:
            continue
        if size > MAX_BYTES:
            big.append(f"{f}（{size / 1024 / 1024:.1f} MB）")
    if big:
        for b in big:
            print(f"  ❌ 超大文件：{b}")
        return False
    ok("无超大文件")
    return True


def check_links(files):
    print("[4/4] README 相对链接有效性")
    bad = []
    checked = 0
    for doc in LINK_DOCS:
        if doc not in files:
            continue
        try:
            text = open(doc, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
        base_dir = os.path.dirname(doc)
        for target in LINK_RE.findall(text):
            t = target.strip()
            # 跳过外部链接 / 纯锚点 / 绝对路径
            if (t.startswith(("http://", "https://", "mailto:", "#", "/"))
                    or "://" in t):
                continue
            # 去掉锚点与查询串
            path = t.split("#", 1)[0].split("?", 1)[0]
            if not path:
                continue
            # 跳过明显的模板占位符（<...>、{...}）
            if "<" in path or "{" in path:
                continue
            full = os.path.normpath(os.path.join(base_dir, path))
            checked += 1
            if not os.path.exists(full):
                bad.append(f"{doc} → {t}")
    if bad:
        for b in bad:
            print(f"  ❌ 链接指向的文件不存在：{b}")
        return False
    ok(f"检查 {checked} 个相对链接，全部有效")
    return True


def check_language_syntax():
    print("[附加] 语言语法检查")
    good = True
    ran = False

    # Python：编译全部 .py（跳过依赖 / 缓存目录）
    py_files = subprocess.run(
        ["git", "ls-files", "*.py"], capture_output=True, text=True
    ).stdout.split()
    py_files = [f for f in py_files if not any(p in f.split("/") for p in SKIP_DIRS)]
    if py_files:
        ran = True
        r = subprocess.run(
            [sys.executable, "-m", "compileall", "-q"] + py_files,
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            ok(f"{len(py_files)} 个 Python 文件编译通过")
        else:
            print(r.stdout + r.stderr)
            good = fail("Python 语法检查未通过") and good

    # Shell：bash -n 语法检查
    sh_files = subprocess.run(
        ["git", "ls-files", "*.sh"], capture_output=True, text=True
    ).stdout.split()
    # 无 .sh 后缀但带 shebang 的脚本
    for f in subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True
    ).stdout.split():
        try:
            with open(f, "rb") as fh:
                first = fh.readline(64)
        except OSError:
            continue
        if first.startswith(b"#!") and b"sh" in first and f not in sh_files:
            sh_files.append(f)
    if sh_files:
        ran = True
        bad = []
        for f in sh_files:
            r = subprocess.run(["bash", "-n", f], capture_output=True, text=True)
            if r.returncode != 0:
                bad.append((f, r.stderr.strip()))
        if bad:
            for f, err in bad:
                print(f"  ❌ {f}: {err}")
            good = fail("Shell 语法检查未通过") and good
        else:
            ok(f"{len(sh_files)} 个 Shell 脚本语法通过")

    if not ran:
        ok("无 Python / Shell 源文件，跳过")
    return good


def main():
    # worktree 里的 .git 是个文件（指向主仓库 gitdir），目录判断会误伤
    if not os.path.exists(".git"):
        print("错误：请在仓库根目录运行")
        return 2

    files = tracked_files()
    results = [
        check_required_files(files),
        check_sensitive(files),
        check_large_files(files),
        check_links(files),
        check_language_syntax(),
    ]

    print()
    if all(results):
        print(f"全部通过（共检查 {len(files)} 个被跟踪文件）")
        return 0
    print("检查未通过，请修正上面的 ❌ 项")
    return 1


if __name__ == "__main__":
    sys.exit(main())
