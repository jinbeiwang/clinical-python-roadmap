#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
download_data.py —— 下载 CDISC SDTM/ADaM 试点项目公开数据集（CDISCPILOT01）

背景
----
本教程所有实战案例都基于 **CDISC 试点项目（CDISC Pilot Project）** 的数据。
这是 2007 年由 CDISC 公开发布的、经 FDA 审阅的阿尔茨海默症（Alzheimer's）双盲试验
CDISCPILOT01 的完整 SDTM + ADaM 数据包，是行业内学习 CDISC 标准的"事实标准"数据集。

- 数据来源（权威、公开、可自由使用）：
  https://github.com/cdisc-org/sdtm-adam-pilot-project
- 文件格式：SAS v5 传输格式（XPORT/XPT），Python 可直接读取，**无需安装 SAS**

为什么需要这个脚本（而不是直接 git clone）？
-------------------------------------------
1. GitHub 的 raw.githubusercontent.com 在国内网络下通常无法直连。
2. 本脚本实现了「多通道自动降级」下载策略：
   通道 A：raw.githubusercontent.com 直连
   通道 B：GitHub REST API（api.github.com，国内通常可直连）——最可靠
   通道 C：公共 GitHub 镜像代理（gh-proxy / ghproxy.net 等）
3. 支持断点续传（按文件级别跳过已下载项）与完整性校验（XPT 文件头魔数检查）。

用法
----
    # 下载全部数据集（SDTM + ADaM，约 90 MB）
    python scripts/download_data.py

    # 只下载案例用到的核心数据集（推荐，约 15 MB）
    python scripts/download_data.py --core

    # 指定输出目录
    python scripts/download_data.py --out data/raw

    # 查看可下载清单
    python scripts/download_data.py --list

认证（可选但强烈建议）
----------------------
通道 B 使用 GitHub API。若遇到限流（60 次/小时），请设置个人访问令牌：

    # Windows PowerShell
    $env:GITHUB_TOKEN = "ghp_xxxxxxxx"
    python scripts/download_data.py --core

    # 或者，如果你机器上装了 gh CLI 并已登录，脚本会自动调用 `gh auth token` 取令牌
    python scripts/download_data.py --core
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = "cdisc-org/sdtm-adam-pilot-project"
REF = "master"
PKG = "updated-pilot-submission-package/900172/m5/datasets/cdiscpilot01"
SDTM_DIR = f"{PKG}/tabulations/sdtm"
ADAM_DIR = f"{PKG}/analysis/adam/datasets"

# 案例用到的核心数据集（体积小、覆盖主要教学场景）
CORE = {
    "sdtm": ["dm", "ae", "cm", "ex", "vs", "lb", "mh", "sc", "sv", "ds", "suppae"],
    "adam": ["adsl", "adae", "adtte", "adlbc"],
}

# 全量数据集
ALL = {
    "sdtm": [
        "ae", "cm", "dm", "ds", "ex", "lb", "mh", "qs", "relrec", "sc",
        "se", "suppae", "suppdm", "suppds", "supplb", "sv", "ta", "te",
        "ti", "ts", "tv", "vs",
    ],
    "adam": [
        "adae", "adlbc", "adlbh", "adlbhy", "adqsadas", "adqscibc",
        "adqsnpix", "adsl", "adtte", "advs",
    ],
}

# 公共镜像代理（按顺序尝试；镜像失效频繁，脚本会自动跳过失败的）
MIRRORS = [
    "https://gh-proxy.com/https://raw.githubusercontent.com",
    "https://ghproxy.net/https://raw.githubusercontent.com",
    "https://gh-proxy.at9.net/https://raw.githubusercontent.com",
]

UA = {"User-Agent": "clinical-python-roadmap/1.0 (educational data fetcher)"}


# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------
def gh_token() -> str | None:
    """按优先级获取 GitHub Token：环境变量 → gh CLI。"""
    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(key):
            return os.environ[key]
    if shutil.which("gh"):
        try:
            out = subprocess.run(
                ["gh", "auth", "token"], capture_output=True, text=True, timeout=15
            )
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except Exception:
            pass
    return None


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def fetch(url: str, headers: dict | None = None, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def is_valid_xpt(path: Path) -> bool:
    """校验 SAS XPORT 文件头：前 80 字节应包含 'LIBRARY HEADER RECORD'。"""
    if not path.exists() or path.stat().st_size < 80:
        return False
    with path.open("rb") as fh:
        head = fh.read(80)
    return b"HEADER RECORD" in head.upper()


# --------------------------------------------------------------------------
# 多通道下载
# --------------------------------------------------------------------------
def download_one(kind: str, name: str, out_dir: Path, token: str | None) -> bool:
    """下载单个 .xpt 文件。返回 True 表示成功。"""
    rel = f"{SDTM_DIR if kind == 'sdtm' else ADAM_DIR}/{name}.xpt"
    raw_url = f"https://raw.githubusercontent.com/{REPO}/{REF}/{rel}"
    dest = out_dir / f"{name}.xpt"

    if is_valid_xpt(dest):
        print(f"  [跳过] {name}.xpt 已存在且校验通过 ({human(dest.stat().st_size)})")
        return True

    def try_url(label: str, url: str, timeout: int) -> bool:
        try:
            data = fetch(url, headers=api_headers, timeout=timeout) if label.startswith("GitHub API") \
                else fetch(url, timeout=timeout)
            if len(data) < 80 or b"HEADER RECORD" not in data[:80].upper():
                raise ValueError("内容不是合法的 XPT 文件")
            dest.write_bytes(data)
            print(f"  [成功] {name}.xpt  via {label}  ({human(len(data))})")
            return True
        except urllib.error.HTTPError as exc:
            hint = "（403 多为 API 限流，请设置 GITHUB_TOKEN）" if exc.code == 403 else ""
            print(f"  [失败] {name}.xpt  via {label}: HTTP {exc.code} {hint}")
        except Exception as exc:  # noqa: BLE001
            print(f"  [失败] {name}.xpt  via {label}: {type(exc).__name__}")
        return False

    api_url = f"https://api.github.com/repos/{REPO}/contents/{rel}?ref={REF}"
    api_headers = {"Accept": "application/vnd.github.raw", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        api_headers["Authorization"] = f"Bearer {token}"

    # 通道 B：GitHub API —— 有令牌时配额充足（5000 次/小时），且最稳定，优先使用
    if token and try_url("GitHub API", api_url, timeout=120):
        return True

    # 通道 A：raw 直连（国内常被墙，超时设短，快速失败）
    if try_url("raw 直连", raw_url, timeout=12):
        return True

    # 通道 C：镜像代理（可用性波动大，逐个快速试探）
    for m in MIRRORS:
        if try_url("镜像代理", f"{m}/{REPO}/{REF}/{rel}", timeout=20):
            return True

    # 通道 B 兜底：无令牌时最后再试一次 API（受 60 次/小时 限流）
    if not token and try_url("GitHub API", api_url, timeout=120):
        return True

    print(f"  [放弃] {name}.xpt —— 所有通道均失败")
    print(f"         可手动下载：{raw_url}")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(
        description="下载 CDISC 试点项目（CDISCPILOT01）SDTM/ADaM 数据集",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--out", default="data/raw", help="输出目录（默认 data/raw）")
    ap.add_argument("--core", action="store_true", help="只下载案例核心数据集")
    ap.add_argument("--list", action="store_true", help="仅列出将下载的文件")
    ap.add_argument("--sdtm-only", action="store_true", help="只下载 SDTM")
    ap.add_argument("--adam-only", action="store_true", help="只下载 ADaM")
    args = ap.parse_args()

    plan = CORE if args.core else ALL
    if args.sdtm_only:
        plan = {"sdtm": plan["sdtm"]}
    if args.adam_only:
        plan = {"adam": plan["adam"]}

    total = sum(len(v) for v in plan.values())
    if args.list:
        print(f"将下载 {total} 个文件：")
        for kind, names in plan.items():
            print(f"  {kind.upper()}: {', '.join(names)}")
        return 0

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    token = gh_token()
    print("=" * 68)
    print("CDISC 试点项目数据集下载器（CDISCPILOT01）")
    print(f"仓库  : https://github.com/{REPO}")
    print(f"目标  : {out_dir.resolve()}")
    print(f"数量  : {total} 个文件")
    print(f"令牌  : {'已获取（API 配额充足）' if token else '未设置（可能受 API 限流影响）'}")
    print("=" * 68)

    ok = fail = 0
    t0 = time.time()
    for kind, names in plan.items():
        print(f"\n[{kind.upper()}]")
        for name in names:
            if download_one(kind, name, out_dir, token):
                ok += 1
            else:
                fail += 1

    print("\n" + "=" * 68)
    print(f"完成：成功 {ok} 个，失败 {fail} 个，耗时 {time.time() - t0:.1f} 秒")
    if fail:
        print("提示：若全部失败，可配置 HTTP_PROXY/HTTPS_PROXY 后重试，")
        print("      或手动从 https://github.com/cdisc-org/sdtm-adam-pilot-project 下载。")
    print("=" * 68)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
