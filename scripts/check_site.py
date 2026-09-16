#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
站点自检：构建完之后跑一遍，确认没有死链和失效锚点。

**为什么需要这个脚本**
----------------------
`build_site.py` 会把"指向仓库内不存在文件"的链接**改写成指向仓库根** ——
这样站点上不会出现明显的 404，但也意味着**一条死链会被静默掩盖**。
所以构建之后必须有一个独立的检查步骤，把这类问题揪出来。

三类问题它都会报：

1. **站内页面链接**指向不存在的 HTML（`guide/xx.html` 拼错了）
2. **页内锚点**指向目标页面里不存在的 `id`（最常见：
   文档目录里手写的 `#锚点` 与标题实际生成的 slug 不一致）
3. **图片 / 静态资源**缺失

用法::

    python scripts/build_site.py --clean
    python scripts/check_site.py            # 有失败会以退出码 1 结束，可直接进 CI

零依赖：只用标准库的正则 + 文件检查（不引入 BeautifulSoup）。
"""

from __future__ import annotations

import html
import posixpath
import re
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

# 抽取 href / src（够用即可，不追求完整的 HTML 解析）
ATTR_RE = re.compile(r'\b(?:href|src)\s*=\s*"([^"]*)"', re.I)
# 目标文件里存在的锚点：<h1 id="..."> / <a id="..." /> / name="..."
ID_RE = re.compile(r'\b(?:id|name)\s*=\s*"([^"]*)"')

SKIP_PREFIX = ("http://", "https://", "mailto:", "data:", "javascript:", "//")


def collect_ids(path: Path) -> set[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return {html.unescape(m.group(1)) for m in ID_RE.finditer(text)}


def main() -> int:
    if not DOCS.is_dir():
        print("✗ 找不到 docs/ —— 先跑 python scripts/build_site.py")
        return 1

    pages = sorted(DOCS.rglob("*.html"))
    if not pages:
        print("✗ docs/ 下没有任何 HTML 页面")
        return 1

    id_cache: dict[Path, set[str]] = {}
    problems: list[str] = []
    n_links = 0
    n_anchors = 0

    for page in pages:
        rel = page.relative_to(DOCS).as_posix()
        text = page.read_text(encoding="utf-8", errors="replace")
        for m in ATTR_RE.finditer(text):
            raw = html.unescape(m.group(1)).strip()
            if not raw or raw.startswith(SKIP_PREFIX):
                continue
            n_links += 1

            path_part, _, frag = raw.partition("#")
            # 丢掉查询串（`style.css?v=c1eb1978`）—— 浏览器不把它当路径的一部分，
            # 检查器也不该。漏掉这一步会把**每一条带版本号的资源链接**
            # 都误报成死链，把真正的问题淹掉。
            path_part = path_part.split("?", 1)[0]
            if not path_part:                      # 纯锚点：#section
                target = page
            else:
                decoded = urllib.parse.unquote(path_part)
                target = (DOCS / posixpath.normpath(
                    posixpath.join(posixpath.dirname(rel), decoded))).resolve()
                if not target.exists():
                    problems.append(f"{rel} → 目标不存在：{raw}")
                    continue
                if target.is_dir():
                    problems.append(f"{rel} → 指向目录而非文件：{raw}")
                    continue

            if frag and target.suffix == ".html":
                n_anchors += 1
                ids = id_cache.setdefault(target, collect_ids(target))
                if frag not in ids:
                    problems.append(
                        f"{rel} → 锚点不存在：{raw}"
                        f"（{target.relative_to(DOCS).as_posix()} 里没有 id=\"{frag}\"）")

    print(f"检查 {len(pages)} 个页面 · {n_links} 条站内链接 · {n_anchors} 个锚点")
    if problems:
        print(f"\n✗ 发现 {len(problems)} 处问题：")
        for p in problems[:60]:
            print("  · " + p)
        if len(problems) > 60:
            print(f"  …… 另有 {len(problems) - 60} 处未列出")
        return 1
    print("✓ 无死链、无失效锚点")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
