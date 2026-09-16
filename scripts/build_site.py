#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把仓库内容构建成可发布到 GitHub Pages 的静态站点。

为什么不用 Jekyll？
    文档正文的代码块里有 ``${{ matrix.python }}``（GitHub Actions 语法）
    和 Python 的 ``{{ }}`` 转义写法 —— Jekyll 会把它们当成 Liquid 标签解析，
    直接构建失败。而且仓库里全是中文文件名，Jekyll 的 permalink 会生成
    一串百分号编码的可分享 URL。自己生成 HTML 反而更简单、更可控。

产物（全部生成到 docs/，GitHub Pages 从 master 分支的 /docs 目录发布）:
    docs/index.html            首页（hero + 卡片 + README 正文）
    docs/guide/*.html          25 章教程 + 速查表 + 资料索引
    docs/code/*.html           13 个案例、clinic 工具包、构建脚本的源码阅读页
    docs/assets/style.css      样式（无任何 CDN 依赖）
    docs/assets/app.js         交互逻辑
    docs/assets/nav-data.js    导航与搜索索引（本脚本生成）
    docs/.nojekyll             关闭 Jekyll 处理

用法:
    python scripts/build_site.py
    python scripts/build_site.py --clean     # 先删除上次产物再构建
"""

from __future__ import annotations

import html
import posixpath
import re
import shutil
import sys
import unicodedata
from pathlib import Path
from urllib.parse import quote, unquote

import markdown
from pygments import highlight as pyg_highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.util import ClassNotFound

# --------------------------------------------------------------------------- 配置
REPO = "jinbeiwang/clinical-python-roadmap"
BRANCH = "master"
SITE_NAME = "临床统计程序员的 Python 进阶路线图"
SITE_SHORT = "临床 Python 进阶路线图"
SITE_DESC = ("面向「熟 SAS、Python 只会一点点」的临床统计程序员：25 章教程 + 13 个可运行案例，"
             "全部基于 CDISC 公开真实数据（SDTM/ADaM），从语法速通到 AI Agent 开发与部署。")

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs"
ASSETS = Path(__file__).resolve().parent / "site_assets"
GH = f"https://github.com/{REPO}"
GH_BLOB = f"{GH}/blob/{BRANCH}"

# --------------------------------------------------------------------- 页面清单
# (分组, 页 id, 侧栏编号, 侧栏短标题, 章节标题, 源文件, 输出路径)
CHAPTERS = [
    ("开始", "00", "00", "学习路线图与如何使用本项目", "docs/00-学习路线图与如何使用本项目.md"),
    ("第一阶段 · Python 基础", "01", "01", "环境搭建与思维转换", "docs/01-环境搭建与思维转换.md"),
    ("第一阶段 · Python 基础", "02", "02", "基础语法速通（SAS 对照）", "docs/02-基础语法速通-SAS对照.md"),
    ("第一阶段 · Python 基础", "03", "03", "核心数据结构 list / dict / str", "docs/03-核心数据结构-list-dict-str.md"),
    ("第一阶段 · Python 基础", "04", "04", "控制流、函数、模块与异常", "docs/04-控制流函数模块与异常.md"),
    ("第一阶段 · Python 基础", "05", "05", "文件与批处理自动化", "docs/05-文件与批处理自动化.md"),
    ("第二阶段 · 数据操作", "06", "06", "NumPy 与向量化思维", "docs/06-NumPy与向量化思维.md"),
    ("第二阶段 · 数据操作", "07", "07", "pandas 入门：DataFrame 就是数据集", "docs/07-pandas入门-DataFrame就是数据集.md"),
    ("第二阶段 · 数据操作", "08", "08", "数据操作对照：DATA 步 / PROC SQL", "docs/08-数据操作对照-DATA步与PROC SQL.md"),
    ("第二阶段 · 数据操作", "09", "09", "合并、重塑与分组汇总", "docs/09-合并重塑与分组汇总.md"),
    ("第二阶段 · 数据操作", "10", "10", "日期、缺失值、格式与数据质量", "docs/10-日期缺失值格式与数据质量.md"),
    ("第二阶段 · 数据操作", "11", "11", "读取 XPT / SAS7BDAT 与临床数据结构", "docs/11-读取XPT与临床数据结构.md"),
    ("第三阶段 · 临床实战", "12", "12", "SDTM 数据处理实战", "docs/12-SDTM数据处理实战.md"),
    ("第三阶段 · 临床实战", "13", "13", "ADaM 衍生与 TFL 报表生成", "docs/13-ADaM衍生与TFL报表生成.md"),
    ("第三阶段 · 临床实战", "14", "14", "统计分析与可视化", "docs/14-统计分析与可视化.md"),
    ("第四阶段 · AI 与工程化", "15", "15", "AI 辅助编程与代码迁移", "docs/15-AI辅助编程与代码迁移.md"),
    ("第四阶段 · AI 与工程化", "16", "16", "Agent 开发入门：临床 QC Agent", "docs/16-Agent开发入门.md"),
    ("第四阶段 · AI 与工程化", "17", "17", "工程化与后续进阶", "docs/17-工程化与后续进阶.md"),
    ("第五阶段 · Agent 核心能力", "18", "18", "Agent 架构设计", "docs/18-Agent架构设计.md"),
    ("第五阶段 · Agent 核心能力", "19", "19", "任务规划与调度", "docs/19-任务规划与调度.md"),
    ("第五阶段 · Agent 核心能力", "20", "20", "工具调用进阶：契约、校验与边界", "docs/20-工具调用进阶.md"),
    ("第五阶段 · Agent 核心能力", "21", "21", "记忆与上下文管理", "docs/21-记忆与上下文管理.md"),
    ("第六阶段 · Agent 工程化", "22", "22", "多 Agent 协作", "docs/22-多Agent协作.md"),
    ("第六阶段 · Agent 工程化", "23", "23", "与外部 API 及服务集成", "docs/23-外部API与服务集成.md"),
    ("第六阶段 · Agent 工程化", "24", "24", "部署与监控", "docs/24-部署与监控.md"),
]

EXTRAS = [
    ("速查与资料", "cheatsheet", "★", "SAS → Python 速查表",
     "cheatsheets/SAS-to-Python速查表.md",
     "14 类语法对照 + 常见坑 Top 20，从 SAS 迁 Python 的高频查阅页"),
    ("速查与资料", "cheat-agent", "★", "Agent 开发速查表",
     "cheatsheets/Agent开发速查表.md",
     "分层架构 / 工具契约 / 重试决策表 / 独立性与反模式 Top 15 / 上线检查清单"),
    ("速查与资料", "resources", "◆", "资料索引 · 总览",
     "resources/README.md",
     "本仓库精选的外部资料总入口，所有链接均已核对可访问"),
    ("速查与资料", "res-01", "1", "GitHub 权威仓库",
     "resources/01-GitHub权威仓库.md",
     "CDISC / PHUSE / atorus 等官方与社区开源仓库"),
    ("速查与资料", "res-02", "2", "标准与规范 · CDISC / PHUSE",
     "resources/02-标准与规范-CDisc-PHUSE.md",
     "SDTM、ADaM、define-XML、受控术语与 PHUSE 工作组的官方入口"),
    ("速查与资料", "res-03", "3", "论文与技术资料",
     "resources/03-论文与技术资料.md",
     "PharmaSUG / PHUSE / SAS Global Forum 中与 Python、开源、AI 相关的论文"),
    ("速查与资料", "res-04", "4", "Python 学习资源与工具链",
     "resources/04-Python学习资源与工具链.md",
     "官方文档、必装库、IDE 与工程化工具"),
    ("速查与资料", "res-05", "5", "Agent 开发与 LLM 资料",
     "resources/05-Agent开发与LLM资料.md",
     "Agent 工程化、评估与测试，以及临床 / 受监管场景的 AI 官方材料"),
]

G_DATA = "实战案例 · 数据与报表"
G_AGENT = "实战案例 · Agent 开发"

CASES = [
    ("case01", "01", "数据体检", "cases/case01_数据体检.py", G_DATA),
    ("case02", "02", "人口学表", "cases/case02_人口学表.py", G_DATA),
    ("case03", "03", "不良事件汇总表", "cases/case03_不良事件汇总表.py", G_DATA),
    ("case04", "04", "实验室移位表", "cases/case04_实验室移位表.py", G_DATA),
    ("case05", "05", "ADSL 衍生与双编程验证", "cases/case05_ADSL衍生.py", G_DATA),
    ("case06", "06", "批处理自动化", "cases/case06_批处理自动化.py", G_DATA),
    ("case07", "07", "临床数据 QC Agent", "cases/case07_临床数据QC_Agent.py", G_AGENT),
    ("case08", "08", "分层架构 QC Agent（架构 / 工具 / 记忆 / 预算）",
     "cases/case08_分层架构QC_Agent.py", G_AGENT),
    ("case09", "09", "SDTM 一致性核查 Agent（记忆检索 / 阈值标定）",
     "cases/case09_SDTM一致性核查Agent.py", G_AGENT),
    ("case10", "10", "TLF 生成流水线（DAG 规划 / 并行 / 断点续跑）",
     "cases/case10_TLF生成流水线.py", G_AGENT),
    ("case11", "11", "双编程 Agent 对（多 Agent / 独立性审计）",
     "cases/case11_双编程Agent对.py", G_AGENT),
    ("case12", "12", "QC Agent 服务化（接口 / 指标 / 缓存 / 评估集）",
     "cases/case12_QC_Agent服务化.py", G_AGENT),
    ("caseA", "A", "外部数据源客户端（第 23 章配套）",
     "cases/case_http_demo.py", G_AGENT),
]

G_CORE = "工具包 clinic/ · 数据与报表"
G_AG = "工具包 clinic/ · Agent 与工程化"

MODULES = [
    ("io", "读 XPT / sas7bdat，保留变量标签，清洗 CDISC 伪缺失值", G_CORE),
    ("derive", "SAS 语义舍入、年龄分组边界、日期与部分日期处理", G_CORE),
    ("report", "TFL 报表构造件：n (%)、Mean (SD)、移位表、p 值格式化", G_CORE),
    ("qc", "数据质量检查、跨域一致性、PROC COMPARE 等价物", G_CORE),
    ("agent_tools", "暴露给 LLM 的 8 个临床数据工具（含数据集白名单硬边界）", G_AG),
    ("agent_core", "Agent 主循环、工具契约与注册表、预算闸门、观察者、指标、LLM 客户端", G_AG),
    ("agent_planner", "任务 DAG 与拓扑排序、并行调度、重试、部分失败、人工确认与续跑", G_AG),
    ("agent_memory", "分层记忆、结构化事实表、CJK 2-gram 关键词检索与引用", G_AG),
    ("agent_role", "多 Agent 拓扑、角色契约、结构化消息、差异分级、独立性守卫", G_AG),
    ("agent_http", "带重试 / 退避 / 限流 / 脱敏审计的 HTTP 客户端与可注入传输层", G_AG),
    ("config", "12-Factor 配置、启动自检（fail fast）、密钥脱敏、数据版本指纹", G_AG),
    ("agent_eval", "确定性评估集：正向断言 + 否定式安全断言 + 报告渲染", G_AG),
]

SCRIPTS = [
    ("download_data", "多通道下载 CDISC 公开 XPT（GitHub API / raw / 镜像回退）"),
    ("make_samples", "把原始 XPT 裁剪成入库的小体积样本 CSV"),
]

# ------------------------------------------------------------------ 高亮配置
LANG_ALIAS = {
    "python": "python", "py": "python", "python3": "python",
    "bash": "bash", "sh": "bash", "shell": "bash", "console": "bash", "zsh": "bash",
    "sas": "sas", "sql": "sql", "proc sql": "sql",
    "json": "json", "yaml": "yaml", "yml": "yaml", "toml": "toml", "ini": "ini",
    "r": "r", "text": "", "txt": "", "plain": "", "log": "", "output": "", "out": "",
    "diff": "diff", "powershell": "powershell", "ps1": "powershell",
    "xml": "xml", "html": "html", "css": "css", "javascript": "javascript", "js": "javascript",
}

# 语言在代码块头部的显示名
LANG_LABEL = {
    "python": "Python", "bash": "Shell", "sas": "SAS", "sql": "SQL",
    "json": "JSON", "yaml": "YAML", "toml": "TOML", "ini": "INI", "r": "R",
    "diff": "Diff", "powershell": "PowerShell", "xml": "XML", "html": "HTML",
    "css": "CSS", "javascript": "JavaScript", "": "文本",
}

CODE_RE = re.compile(
    r'<pre><code(?: class="language-([\w+#\- .]+)")?>(.*?)</code></pre>', re.S)
H_TAG_RE = re.compile(r"<(h[1-6])[^>]*\bid=\"([^\"]+)\"[^>]*>(.*?)</\1>", re.S)
TAG_RE = re.compile(r"<[^>]+>")


# --------------------------------------------------------------------- 工具函数
def rel_href(target_out: str, from_out: str) -> str:
    """计算从 from_out 页面指向 target_out 页面的相对链接。"""
    base = posixpath.dirname(from_out)
    rel = posixpath.relpath(target_out, base) if base else target_out
    return rel


def versioned_asset(name: str) -> str:
    """算出静态资源的内容指纹（**只返回指纹本身**，不带文件名）。

    调用方在模板里已经写成 ``style.css?v=__CSSV__``，
    所以这里只能返回 ``c1eb1978`` 这样的裸摘要。

    ⚠️ 这里曾经返回 ``"style.css?v=c1eb1978"``，与模板拼起来变成
    ``style.css?v=style.css?v=c1eb1978`` —— 页面照样能加载（查询串被静态
    服务器忽略），所以这个错**不会报错、也不会被肉眼发现**，
    只会让版本号看起来像乱码。构建产物里的每个链接都值得用
    ``scripts/check_site.py`` 过一遍，就是防这一类问题。
    """
    import hashlib
    p = ASSETS / name
    return hashlib.md5(p.read_bytes()).hexdigest()[:8] if p.exists() else "0"


def sniff_lang(code: str) -> str:
    """没有标注语言的代码块，猜一下是 SAS 还是 Python。"""
    if re.search(r"^\s*(?:proc|data|libname|%macro|options|ods)\b", code, re.M | re.I):
        return "sas"
    if re.search(r"^\s*(?:import|from|def|class)\s+\w", code, re.M):
        return "python"
    if re.search(r"^\s*\$?\s*(?:pip|python|git|cd|ls|conda)\s", code, re.M):
        return "bash"
    return ""


def highlight_code(code: str, lang: str) -> tuple[str, str, bool]:
    """返回 (高亮后 HTML, 语言显示名, 是否做了语法着色)。"""
    key = LANG_ALIAS.get(lang.strip().lower(), None)
    if key is None:
        key = lang.strip().lower()
    if key == "" and not lang.strip():
        key = sniff_lang(code)

    lexer = None
    if key:
        try:
            lexer = get_lexer_by_name(key, stripnl=False, ensurenl=False)
        except ClassNotFound:
            lexer = None
    if lexer is None:
        lexer = TextLexer(stripnl=False, ensurenl=False)
        colored = False
    else:
        colored = not isinstance(lexer, TextLexer)

    formatter = HtmlFormatter(nowrap=True, cssclass="hl")
    body = pyg_highlight(code, lexer, formatter)
    label = LANG_LABEL.get(key, key.upper() if key else "文本")
    return body, label, colored


def render_code_block(code: str, lang: str) -> str:
    body, label, colored = highlight_code(code, lang)
    cls = "codeblock" if colored else "codeblock plain"
    return (
        f'<div class="{cls}">'
        f'<div class="cb-head"><span class="cb-lang">{html.escape(label)}</span></div>'
        f'<div class="hl"><pre><code>{body}</code></pre></div>'
        f"</div>"
    )


def replace_code_blocks(md_html: str) -> str:
    """把 fenced_code 产出的 <pre><code> 换成带语言标签和复制按钮的代码块。"""

    def _sub(m: re.Match) -> str:
        lang = (m.group(1) or "").strip()
        raw = html.unescape(m.group(2))
        return render_code_block(raw.rstrip("\n"), lang)

    return CODE_RE.sub(_sub, md_html)


# ------------------------------------------------------------------ 链接重写
class LinkRewriter:
    """把 Markdown 里的仓库内相对链接，改写成站点内链接或 GitHub 链接。"""

    def __init__(self, outmap: dict[str, str]):
        # src_rel（仓库内 POSIX 相对路径）-> 站点输出路径
        self.outmap = outmap

    def __call__(self, text: str, src_rel: str, out_rel: str) -> str:
        def _sub(m: re.Match) -> str:
            url = m.group(1).strip()
            if not url or url.startswith(("http://", "https://", "mailto:", "data:", "#")):
                return m.group(0)
            if url.startswith("<"):
                return m.group(0)
            path, sep, frag = url.partition("#")
            if not path:
                return m.group(0)
            clean = unquote(path)
            target = posixpath.normpath(posixpath.join(posixpath.dirname(src_rel), clean))
            if target in self.outmap:
                link = rel_href(self.outmap[target], out_rel)
            elif (ROOT / target).is_dir():
                link = f"{GH}/tree/{BRANCH}/{quote(target)}"
            elif (ROOT / target).exists():
                link = f"{GH_BLOB}/{quote(target)}"
            else:
                # 目标不存在（例如文档里提到未来会加的文件）→ 指向仓库根
                link = GH
            if sep:
                link += "#" + frag
            return "](" + link + ")"

        # 注意：`[^)\n]*` 而不是 `[^)\s]*` —— 仓库里有个文件名含空格
        # （docs/08-数据操作对照-DATA步与PROC SQL.md），用 \s 排除会让含空格的
        # 链接整条漏掉、不被重写。
        return re.sub(r"\]\(([^)\n]*)\)", _sub, text)


# -------------------------------------------------------------------- Markdown
def gh_slugify(value: str, separator: str) -> str:
    """GitHub 风格的标题锚点 id。

    为什么不能用 markdown 自带的 slugify：
        markdown 3.x 的实现会把中日韩字符**全部丢弃** ——
        ``## 3. 变量操作（DATA 步对照）`` 得到 ``3-data``，
        而仓库文档里手写的目录锚点是按 GitHub 规则写的
        （``#3-变量操作data-步对照``），于是全站目录点击后毫无反应。

    这里复刻 GitHub 的规则：
        1. 去掉 HTML 标签，NFKC 归一化，转小写
        2. 删除所有标点（保留 Unicode 字母数字、下划线、连字符、空白）
        3. **每个空白字符单独**替换为连字符（不压缩连续空白 ——
           ``（BY 组处理 / PROC MEANS）`` 里被删掉的 ``/`` 会留下两个连字符）
    """
    value = TAG_RE.sub("", value)
    value = unicodedata.normalize("NFKC", value).strip().lower()
    value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)
    value = re.sub(r"\s", separator, value)
    return value.strip(separator)


def make_md() -> markdown.Markdown:
    return markdown.Markdown(
        extensions=["tables", "fenced_code", "attr_list", "def_list", "footnotes",
                    "sane_lists", "toc", "md_in_html"],
        extension_configs={"toc": {"permalink": False, "toc_depth": "1-4",
                                   "slugify": gh_slugify}},
    )


def clean_head_name(name: str) -> str:
    """把 toc_tokens 里的标题原文清理成纯文本。

    ``toc_tokens[i]["name"]`` 是**未渲染的 Markdown 原文**，直接塞进右侧目录
    会露出 ``**加粗**``、``` `代码` ``` 甚至 ``&amp;`` 之类的标记。
    """
    s = re.sub(r"`([^`]*)`", r"\1", name)              # 行内代码
    s = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s)   # 链接 / 图片
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)           # 粗体
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", s)  # 斜体
    s = s.replace("~~", "")
    s = TAG_RE.sub("", s)
    return html.unescape(s).strip()


def collect_heads(md: markdown.Markdown) -> list[dict]:
    """从 toc_tokens 提取 h2/h3，用于右侧目录与搜索索引。"""
    heads: list[dict] = []

    def walk(tokens):
        for t in tokens:
            if t["level"] in (2, 3):
                heads.append({"id": t["id"], "t": clean_head_name(t["name"]),
                              "lv": t["level"]})
            walk(t.get("children", []))

    walk(md.toc_tokens)
    return heads


def render_markdown(text: str, src_rel: str, out_rel: str,
                    rewriter: LinkRewriter) -> tuple[str, list[dict], str]:
    """返回 (正文 HTML, 小节标题列表, 文档大标题)。"""
    text = rewriter(text, src_rel, out_rel)
    md = make_md()
    body = md.convert(text)
    body = replace_code_blocks(body)
    heads = collect_heads(md)
    title = ""
    for line in text.splitlines():
        if line.startswith("# "):
            title = re.sub(r"^#\s+", "", line).strip()
            break
    return body, heads, title


def render_source_page(text: str, lang: str) -> str:
    """源码阅读页：整份文件高亮 + 行号。"""
    return render_code_block(text.rstrip("\n"), lang)


# ------------------------------------------------------------------- 页面模板
HEAD_TMPL = """<!DOCTYPE html>
<html lang="zh-CN" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<meta name="description" content="__DESC__">
<meta name="author" content="jinbeiwang">
<meta name="color-scheme" content="light dark">
<meta property="og:type" content="article">
<meta property="og:title" content="__TITLE__">
<meta property="og:description" content="__DESC__">
<meta property="og:site_name" content="__SITE__">
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🐍</text></svg>">
<script>(function(){try{var t=localStorage.getItem("cpr-theme");if(!t){t=window.matchMedia&&window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light";}document.documentElement.setAttribute("data-theme",t);}catch(e){}})();</script>
<link rel="stylesheet" href="__BASE__assets/style.css?v=__CSSV__">
</head>
<body data-base="__BASE__" data-page="__PAGE__">
<header class="topbar">
  <button class="icon-btn menu-btn" id="menuBtn" type="button" aria-label="打开目录">☰</button>
  <a class="brand" href="__BASE__index.html">
    <span class="logo">Py</span>
    <span>__SITE__</span>
  </a>
  <div class="spacer"></div>
  <div class="search-wrap">
    <input id="searchInput" type="search" placeholder="搜索章节与小节…" autocomplete="off" spellcheck="false" aria-label="站内搜索">
    <span class="s-icon">⌕</span>
    <kbd>/</kbd>
    <div class="search-results" id="searchResults"></div>
  </div>
  <button class="icon-btn" id="themeBtn" type="button" title="切换深浅色">☾</button>
  <a class="icon-btn" href="__GH__" target="_blank" rel="noopener" title="GitHub 仓库" aria-label="GitHub 仓库">
    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/></svg>
  </a>
</header>
"""

FOOT_TMPL = """<footer class="site-footer">
  <p><strong>__SITE__</strong> · MIT License · 内容基于 CDISC 公开的
     <a href="https://github.com/cdisc-org/sdtm-adam-pilot-project" target="_blank" rel="noopener">SDTM/ADaM Pilot Project</a>
     （CDISCPILOT01）真实数据。</p>
  <p>所有代码与数字均在本机实测复现，可直接 <code>git clone</code> 后离线运行。
     <a href="__GH__" target="_blank" rel="noopener">在 GitHub 上查看 / 提交 Issue</a></p>
</footer>
<button class="to-top" id="toTop" type="button" aria-label="回到顶部">↑</button>
<div class="overlay" id="overlay"></div>
<script src="__BASE__assets/nav-data.js"></script>
<script src="__BASE__assets/app.js?v=__JSV__"></script>
</body>
</html>
"""


def build_page(out_rel: str, title: str, desc: str, body: str, crumb: str,
               actions: str = "", pager: str = "") -> str:
    base = "../" * (len(posixpath.dirname(out_rel).split("/")) if posixpath.dirname(out_rel) else 0)
    head = (HEAD_TMPL
            .replace("__BASE__", base)
            .replace("__PAGE__", out_rel)
            .replace("__TITLE__", html.escape(title))
            .replace("__SITE__", html.escape(SITE_SHORT))
            .replace("__DESC__", html.escape(desc))
            .replace("__GH__", GH)
            .replace("__CSSV__", CSS_V)
            .replace("__JSV__", JS_V))
    foot = (FOOT_TMPL
            .replace("__BASE__", base)
            .replace("__SITE__", html.escape(SITE_NAME))
            .replace("__GH__", GH)
            .replace("__JSV__", JS_V))
    return (
        head
        + '<div class="layout">\n'
        + '<aside class="sidebar" id="sidebar"></aside>\n'
        + '<main class="content">\n'
        + f'  <div class="page-head"><div class="crumb">{crumb}</div>'
        + f"<h1>{html.escape(title)}</h1>"
        + (f'<div class="page-actions">{actions}</div>' if actions else "")
        + "</div>\n"
        + f'  <article class="markdown" id="content">{body}</article>\n'
        + (pager if pager else "")
        + "\n</main>\n"
        + '<aside class="toc" id="toc"></aside>\n'
        + "</div>\n"
        + foot
    )


# ----------------------------------------------------------------------- 主流程
CSS_V = ""
JS_V = ""


def main() -> int:
    global CSS_V, JS_V
    clean = "--clean" in sys.argv

    gen_dirs = [OUT / "guide", OUT / "code", OUT / "assets"]
    if clean:
        for d in gen_dirs:
            if d.exists():
                shutil.rmtree(d)
        idx = OUT / "index.html"
        if idx.exists():
            idx.unlink()

    for d in gen_dirs:
        d.mkdir(parents=True, exist_ok=True)
    (OUT / ".nojekyll").write_text("", encoding="utf-8")

    # 资源
    for name in ("style.css", "app.js"):
        shutil.copyfile(ASSETS / name, OUT / "assets" / name)
    CSS_V = versioned_asset("style.css")
    JS_V = versioned_asset("app.js")

    # ---- 建立「源文件 -> 输出文件」映射，供链接重写使用
    outmap: dict[str, str] = {"README.md": "index.html"}
    pages: list[dict] = []          # 导航用
    docs_meta: list[dict] = []      # 搜索索引用

    for group, pid, num, label, src in CHAPTERS:
        out_rel = f"guide/{pid}.html"
        outmap[src] = out_rel
        pages.append(dict(group=group, pid=pid, num=num, label=label, src=src, out=out_rel))
    for group, pid, num, label, src, _desc in EXTRAS:
        out_rel = f"guide/{pid}.html"
        outmap[src] = out_rel
        pages.append(dict(group=group, pid=pid, num=num, label=label, src=src, out=out_rel))

    case_pages = []
    for cid, num, label, src, grp in CASES:
        out_rel = f"code/{cid}.html"
        outmap[src] = out_rel
        case_pages.append(dict(group=grp, pid=cid, num=num, label=label,
                               src=src, out=out_rel, lang="python",
                               doc_title=f"案例 {num} · {label}"))
    mod_pages = []
    for name, desc, grp in MODULES:
        src = f"clinic/{name}.py"
        if not (ROOT / src).exists():
            continue
        out_rel = f"code/clinic-{name}.html"
        outmap[src] = out_rel
        mod_pages.append(dict(group=grp, pid=f"clinic-{name}", num="",
                              label=f"{name}.py", src=src, out=out_rel, lang="python",
                              doc_title=f"clinic/{name}.py"))
    script_pages = []
    for name, desc in SCRIPTS:
        src = f"scripts/{name}.py"
        if not (ROOT / src).exists():
            continue
        out_rel = f"code/script-{name}.html"
        outmap[src] = out_rel
        script_pages.append(dict(group="构建脚本", pid=f"script-{name}", num="",
                                 label=f"{name}.py", src=src, out=out_rel, lang="python",
                                 doc_title=f"scripts/{name}.py"))

    all_code_pages = case_pages + mod_pages + script_pages
    for p in all_code_pages:
        pages.append(p)

    rewriter = LinkRewriter(outmap)

    # ---- 渲染 Markdown 页（源码页走下面的专用分支，不要在这里处理）
    md_pages = [p for p in pages if p["src"].endswith(".md")]
    for p in md_pages:
        text = (ROOT / p["src"]).read_text(encoding="utf-8")
        body, heads, doc_title = render_markdown(text, p["src"], p["out"], rewriter)
        # 页头已经展示过文档标题，正文里的首个 h1 去掉，免得一屏出现两遍
        body = re.sub(r"^\s*<h1[^>]*>.*?</h1>\s*", "", body, count=1, flags=re.S)
        p["heads"] = heads
        p["doc_title"] = doc_title or p["label"]

        crumb = (f'<a href="{rel_href("index.html", p["out"])}">路线图</a>'
                 f' <span>›</span> {html.escape(p["group"])}')
        actions = (f'<a href="{GH_BLOB}/{quote(p["src"])}" target="_blank" rel="noopener">'
                   f"在 GitHub 上查看源文件</a>")
        pager = build_pager(p, pages)
        out_html = build_page(p["out"], doc_title or p["label"],
                              f'{p["label"]} — {SITE_SHORT}', body, crumb, actions, pager)
        (OUT / p["out"]).write_text(out_html, encoding="utf-8")

    # ---- 渲染源码阅读页
    for p in all_code_pages:
        text = (ROOT / p["src"]).read_text(encoding="utf-8")
        n_lines = text.count("\n") + 1
        # 取文件顶部注释块作为说明
        intro = extract_docstring(text)
        body = (f'<div class="code-meta" style="margin:0 0 14px;padding:12px 16px;'
                f'border:1px solid var(--bd);border-radius:10px;background:var(--bg-soft);'
                f'font-size:13.5px;color:var(--tx-2)">'
                f'<strong style="color:var(--tx)">{html.escape(posixpath.basename(p["src"]))}</strong>'
                f' · {n_lines} 行 · {len(text) // 1024 + 1} KB'
                f'{"<br>" + html.escape(intro) if intro else ""}</div>'
                + render_source_page(text, p["lang"]))
        crumb = (f'<a href="{rel_href("index.html", p["out"])}">路线图</a>'
                 f' <span>›</span> {html.escape(p["group"])}')
        actions = (f'<a href="{GH_BLOB}/{quote(p["src"])}" target="_blank" rel="noopener">'
                   f"在 GitHub 上查看</a>"
                   f'<a href="{GH}/raw/{BRANCH}/{quote(p["src"])}">下载</a>')
        p["heads"] = []
        out_html = build_page(p["out"], p["doc_title"], f'{p["label"]} — {SITE_SHORT}',
                              body, crumb, actions, build_pager(p, pages))
        (OUT / p["out"]).write_text(out_html, encoding="utf-8")

    # ---- 首页
    write_index(rewriter, pages, case_pages)

    # ---- 导航数据
    write_nav_data(pages)

    total_bytes = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    n_pages = len(md_pages) + len(all_code_pages) + 1
    print(f"✓ 生成 {n_pages} 个页面 → {OUT.relative_to(ROOT)}/")
    print(f"  合计 {total_bytes / 1024:.0f} KB（未压缩，GitHub Pages 走 gzip 后约 1/4）")
    print(f"  guide/ {len(md_pages)} 页 · code/ {len(all_code_pages)} 页 · index.html + assets/")
    print(f"  导航条目 {len(pages) + 1} 个 · 搜索小节索引 "
          f"{sum(len(p.get('heads') or []) for p in pages)} 条")
    return 0


def build_pager(page: dict, pages: list[dict]) -> str:
    order = [p for p in pages]
    try:
        i = order.index(page)
    except ValueError:
        return ""
    prev_p = order[i - 1] if i > 0 else None
    next_p = order[i + 1] if i + 1 < len(order) else None
    if not prev_p and not next_p:
        return ""
    parts = ['<nav class="pager">']
    if prev_p:
        parts.append(
            f'<a class="pg-prev" href="{rel_href(prev_p["out"], page["out"])}">'
            f'<span class="pg-label">← 上一篇</span>'
            f'<span class="pg-title">{html.escape(prev_p["label"])}</span></a>')
    else:
        parts.append("<span></span>")
    if next_p:
        parts.append(
            f'<a class="pg-next" href="{rel_href(next_p["out"], page["out"])}">'
            f'<span class="pg-label">下一篇 →</span>'
            f'<span class="pg-title">{html.escape(next_p["label"])}</span></a>')
    parts.append("</nav>")
    return "".join(parts)


def extract_docstring(text: str) -> str:
    """取源码开头的模块文档字符串首段，作为源码页的说明。"""
    m = re.match(r'\s*(?:#![^\n]*\n)?\s*(?:#[^\n]*\n\s*)*("""|\'\'\')(.*?)\1', text, re.S)
    if m:
        doc = m.group(2).strip()
    else:
        m2 = re.match(r'\s*(?:#[^\n]*\n)+', text)
        if not m2:
            return ""
        doc = "\n".join(l.lstrip("# ").rstrip() for l in m2.group(0).splitlines()).strip()
    lines = doc.splitlines()
    out, blank = [], 0
    for l in lines:
        if not l.strip():
            break
        out.append(l.strip())
        if len(" ".join(out)) > 150:
            break
    s = " ".join(out).strip()
    # 这段摘要是塞进 HTML 的纯文本，所以要先把 Markdown 标记去掉 ——
    # 否则页面上会原样显示 ``**目标**`` 这种记号（看起来像构建出错）。
    s = re.sub(r"`([^`]*)`", r"\1", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", s)
    s = re.sub(r"^[-*+]\s+", "", s)
    s = re.sub(r"\s+", " ", s)
    return (s[:170] + "…") if len(s) > 170 else s


def write_index(rewriter: LinkRewriter, pages: list[dict], case_pages: list[dict]) -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    body, _heads, _t = render_markdown(text, "README.md", "index.html", rewriter)
    # README 开头（一级标题 / 简介引用块 / 徽章）已经在 hero 里呈现过了，
    # 正文从第一个二级标题开始，避免同一屏重复两遍。
    cut = re.search(r"<h2[^>]*>", body)
    if cut:
        body = body[cut.start():]

    # 章节卡片（只取 00–17 正文章节）
    cards = ['<div class="card-grid">']
    for p in pages:
        if re.fullmatch(r"guide/\d{2}\.html", p["out"]):
            cards.append(
                f'<a class="card" href="{p["out"]}">'
                f'<div class="c-num">CHAPTER {p["num"]}</div>'
                f'<div class="c-title">{html.escape(p["label"])}</div></a>')
    cards.append("</div>")

    hero = f"""<div class="hero">
  <h1>{html.escape(SITE_NAME)}</h1>
  <p class="lede">给「熟 SAS、Python 只会一点点」的临床统计程序员。<br>
     从语法速通到能开发 AI Agent —— 全程用<strong>真实的公开临床数据</strong>，
     所有代码<strong>实测可跑通</strong>，所有数字<strong>来自真实数据而非编造</strong>。</p>
  <div class="hero-badges">
    <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python 3.10+">
    <img src="https://img.shields.io/badge/pandas-2.x%20%7C%203.x-150458?logo=pandas&logoColor=white" alt="pandas">
    <img src="https://img.shields.io/badge/CDISC-SDTM%20%7C%20ADaM-005A9C" alt="CDISC">
    <img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT">
  </div>
  <div class="hero-stats">
    <div class="hs"><b>25</b><span>章系统教程</span></div>
    <div class="hs"><b>13</b><span>个可运行案例</span></div>
    <div class="hs"><b>12</b><span>个可复用模块</span></div>
    <div class="hs"><b>254</b><span>真实受试者</span></div>
  </div>
  <h2 style="font-size:19px;margin:30px 0 12px">章节总览</h2>
  {''.join(cards)}
</div>"""

    base = ""
    head = (HEAD_TMPL
            .replace("__BASE__", base)
            .replace("__PAGE__", "index.html")
            .replace("__TITLE__", html.escape(f"{SITE_NAME} · 从 SAS 到 Python 与 AI Agent"))
            .replace("__SITE__", html.escape(SITE_SHORT))
            .replace("__DESC__", html.escape(SITE_DESC))
            .replace("__GH__", GH)
            .replace("__CSSV__", CSS_V)
            .replace("__JSV__", JS_V))
    foot = (FOOT_TMPL
            .replace("__BASE__", base)
            .replace("__SITE__", html.escape(SITE_NAME))
            .replace("__GH__", GH)
            .replace("__JSV__", JS_V))
    out = (
        head
        + '<div class="layout">\n'
        + '<aside class="sidebar" id="sidebar"></aside>\n'
        + '<main class="content">\n'
        + hero
        + f'<article class="markdown" id="content">{body}</article>\n'
        + "</main>\n"
        + '<aside class="toc" id="toc"></aside>\n'
        + "</div>\n"
        + foot
    )
    (OUT / "index.html").write_text(out, encoding="utf-8")


def write_nav_data(pages: list[dict]) -> None:
    import json

    ordered: list[dict] = [{"id": "home", "num": "", "label": "项目主页",
                            "title": "项目主页", "url": "index.html", "group": "开始"}]
    for p in pages:
        ordered.append({"id": p["pid"], "num": p.get("num", ""), "label": p["label"],
                        "title": p.get("doc_title") or p["label"], "url": p["out"],
                        "group": p["group"]})

    nav, search, cur_group = [], [], None
    for e in ordered:
        if e["group"] != cur_group:
            cur_group = e["group"]
            nav.append({"group": cur_group, "items": []})
        nav[-1]["items"].append({k: e[k] for k in ("id", "num", "label", "title", "url")})
        search.append({"title": e["title"], "group": e["group"], "url": e["url"],
                       "heads": next((p.get("heads", []) for p in pages
                                      if p["out"] == e["url"]), [])})

    data = {"repo": REPO, "branch": BRANCH, "nav": nav, "search": search}
    js = "/* 由 scripts/build_site.py 生成，请勿手工编辑 */\nwindow.SITE = " \
        + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n"
    (OUT / "assets" / "nav-data.js").write_text(js, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
