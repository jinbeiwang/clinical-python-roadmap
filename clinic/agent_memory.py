#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""记忆与上下文管理（第 21 章）。

三个核心组件：

* :class:`MemoryStrategy` —— 三种对话记忆策略（全量 / 滑窗 / 摘要）。
* :class:`KeywordRetriever` —— 零依赖的关键词检索，用于 SAP / CRF / 规范文档。
* :func:`render_tool_result` —— 工具结果进上下文前的长度控制。

一个贯穿全篇的原则：**数字必须能溯源，检索不到就说没找到**。
这两条在临床场景里都不是"最佳实践"，而是底线。
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from clinic.agent_core import LLMClient, Message, rough_tokens

__all__ = [
    "render_tool_result", "cap_rows", "rough_tokens",
    "Chunk", "KeywordRetriever", "load_corpus",
    "MemoryStrategy", "FullMemory", "WindowMemory", "SummaryMemory",
    "ContextBudget", "fact_table_text",
]


# ===========================================================================
# 1. 工具结果的长度控制
# ===========================================================================
def cap_rows(rows: Sequence[Any], limit: int = 50,
             hint: str | None = None) -> dict:
    """统一的结果截断：截断 + 摘要 + **明确告知被截断**。

    第三点最关键 —— 不写明"已截断"，模型会把前 50 行当成全部数据，
    得出"只有 50 个受试者"这种**数据层面的幻觉**（比语言幻觉更危险，
    因为它看起来有工具结果支撑）。
    """
    total = len(rows)
    if total <= limit:
        return {"行": list(rows), "总数": total}
    return {
        "行": list(rows[:limit]),
        "总数": total,
        "已截断": True,
        "说明": hint or (f"仅返回前 {limit} 行，共 {total} 行；"
                        f"如需完整结果，请缩小条件范围或改用汇总类工具。"),
    }


def render_tool_result(data: Any, max_chars: int = 1200) -> str:
    """把工具结果渲染进上下文；超长则保留骨架 + 前若干行。

    这是省 token 最有效的一招：8 轮的核查任务能稳定控制在 8K token 上下，
    而不是随轮次无限膨胀。
    """
    text = json.dumps(data, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return text

    if isinstance(data, dict) and isinstance(data.get("行"), list):
        rows = data["行"]
        keep = max(3, max_chars // 90)
        trimmed = dict(data)
        trimmed["行"] = rows[:keep]
        trimmed["说明"] = (f"（为控制上下文长度，此处仅展示前 {keep} 行，"
                          f"总数 {data.get('总数', len(rows))}）")
        return json.dumps(trimmed, ensure_ascii=False, default=str)

    return text[:max_chars] + f"…（已截断，原长 {len(text)} 字符）"


# ===========================================================================
# 2. 检索
# ===========================================================================
@dataclass
class Chunk:
    """一个可检索片段。**元数据必须够定位** —— 否则引用无法核查。"""
    doc: str                        # 文件名
    section: str                    # 章节号，如 "6.3.2"
    title: str
    text: str
    page: int | None = None         # PDF 页码（若有）

    def citation(self) -> str:
        parts = [self.doc]
        if self.section and self.section not in ("-", ""):
            parts.append(f"第 {self.section} 节")
        if self.page:
            parts.append(f"第 {self.page} 页")
        return " ".join(parts) if len(parts) > 1 else f"{self.doc} · {self.title}"


def _tokenize(s: str) -> set[str]:
    """中英混合分词：英文按词，中文按 2-gram。

    对"缺失值""受试者""安全性人群"这类两字以上术语足够有效，
    且**完全可解释** —— 你能说清为什么召回了这一段。
    """
    s = s.lower()
    words = set(re.findall(r"[a-z_][a-z0-9_]{2,}", s))
    cjk = re.findall(r"[\u4e00-\u9fff]", s)
    words |= {"".join(cjk[i:i + 2]) for i in range(len(cjk) - 1)}
    return words


class KeywordRetriever:
    """基于关键词打分的检索器。零依赖，对结构化文档效果好。

    什么时候该换成向量检索：**文档超过约 200 个 chunk**。
    在那之前，关键词检索的**可解释性**反而是临床场景更看重的性质。
    """

    def __init__(self, chunks: Sequence[Chunk], min_score: float = 0.25) -> None:
        self.chunks = list(chunks)
        self.min_score = min_score
        self._index = [_tokenize(c.title + " " + c.text) for c in self.chunks]

    def search(self, query: str, top_k: int = 3) -> list[tuple[Chunk, float]]:
        q = _tokenize(query)
        if not q:
            return []
        scored: list[tuple[Chunk, float]] = []
        for chunk, toks in zip(self.chunks, self._index):
            hit = len(q & toks)
            if not hit:
                continue
            score = hit / len(q)
            if q & _tokenize(chunk.title):
                score += 0.3                        # 标题命中更相关
            if q & _tokenize(chunk.section):
                score += 0.2
            scored.append((chunk, round(score, 3)))
        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]

    def answer_context(self, query: str, top_k: int = 3) -> str:
        """检索并渲染成给模型看的上下文。**低于阈值就明说没找到**。"""
        hits = self.search(query, top_k)
        good = [(c, s) for c, s in hits if s >= self.min_score]
        if not good:
            return ("在提供的文档中没有找到与该问题相关的规定。\n"
                    "请确认文档范围，或提供相关章节 —— 不要凭推测作答。")
        parts = [f"【依据 {i}】{c.citation()} · {c.title}\n{c.text}"
                 for i, (c, _s) in enumerate(good, 1)]
        return "\n\n".join(parts)


# ------------------------------------------------------------------ 语料构建
_SECTION_RE = re.compile(r"^(#{1,3})\s+(.*)$")


def load_corpus(paths: Iterable[str | Path], max_chars: int = 1500) -> list[Chunk]:
    """把一组 Markdown 文件切成 chunk（按标题切）。

    做 RAG 的第一步往往不是"上向量库"，而是**把文档切对**。
    结构化文档按标题切，召回质量立刻就不一样。

    ⚠️ 两个真实陷阱（本项目实测踩过）：

    1. **代码块里的 ``#`` 不是标题**。教程文档里常有大段示例代码，
       里面的 ``# 注释`` 或 ``---- 3. 步骤 ----`` 会被朴素的行解析误判成标题，
       切出一堆标题形如 ``---------- 2. 受试者一致性 -----`` 的碎片。
       本函数用围栏标记（``` ``` ``）跟踪，围栏内不识别标题。
    2. **没编号的标题拿不到章节号**，``section`` 会是 ``-``。
       引用时不要拼成"第 - 节"（见 :meth:`Chunk.citation`）。
    """
    out: list[Chunk] = []
    for p in paths:
        p = Path(p)
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        cur_sec, cur_title, buf = "", p.stem, []
        in_fence = False

        def flush() -> None:
            body = "\n".join(buf).strip()
            if body:
                out.append(Chunk(doc=p.name, section=cur_sec or "-",
                                 title=cur_title, text=body[:max_chars]))

        for line in text.splitlines():
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                buf.append(line)
                continue
            m = None if in_fence else _SECTION_RE.match(line)
            if m:
                flush()
                buf = []
                heading = m.group(2).strip()
                num = re.match(r"^([\d.]+)", heading)
                cur_sec = num.group(1).rstrip(".") if num else "-"
                cur_title = re.sub(r"\*+", "", heading).strip()
            else:
                buf.append(line)
        flush()
    return out


# ===========================================================================
# 3. 三种记忆策略
# ===========================================================================
class MemoryStrategy:
    """构造送给模型的上下文。子类实现 ``build()``。"""

    def build(self, messages: Sequence[Message]) -> list[Message]:
        raise NotImplementedError

    def stats(self, messages: Sequence[Message]) -> dict:
        ctx = self.build(messages)
        text = "\n".join(m.content for m in ctx)
        return {"轮数": len(ctx), "字符数": len(text), "估算token": rough_tokens(text)}


class FullMemory(MemoryStrategy):
    """全量保留。短任务与调试用。"""

    def build(self, messages: Sequence[Message]) -> list[Message]:
        return list(messages)


class WindowMemory(MemoryStrategy):
    """滑窗：保留最前 N 条 + 最近 K 条，中间以一行提示代替。

    ⚠️ 生产中要注意一个坑：``tool`` 消息必须紧跟在对应的 ``assistant``
    tool_call 消息之后。窗口边界正好切在这两者之间时，API 会报 400。
    这里用 ``align`` 参数做向后对齐。
    """

    def __init__(self, keep_recent: int = 5, keep_head: int = 2,
                 align: bool = True) -> None:
        self.keep_recent = keep_recent
        self.keep_head = keep_head
        self.align = align

    def build(self, messages: Sequence[Message]) -> list[Message]:
        msgs = list(messages)
        if len(msgs) <= self.keep_head + self.keep_recent:
            return msgs
        cut = len(msgs) - self.keep_recent
        if self.align:                      # 别把 tool 消息和它的请求切开
            while cut < len(msgs) and msgs[cut].role == "tool":
                cut += 1
        head, tail = msgs[: self.keep_head], msgs[cut:]
        omitted = cut - self.keep_head
        if omitted > 0:
            head = head + [Message.system(f"（此处省略了 {omitted} 条中间消息）")]
        return head + tail


class SummaryMemory(MemoryStrategy):
    """摘要压缩：把旧对话压成结构化摘要，**保留数字与结论**。

    这是临床场景推荐的策略 —— 纯滑窗会丢掉早期的关键发现，
    纯摘要有信息损失风险，两者结合最稳。

    ``summarize_fn`` 可注入：默认走规则化摘要（离线可用），
    也可以换成 LLM 摘要（更自然但有失真风险）。
    """

    PROMPT = """把下面的对话压缩成结构化摘要。
必须保留：① 已确认的事实与数字（含出处）② 已排除的可能性 ③ 未解决的问题
不要保留：寒暄、重复的推理过程、失败尝试的细节

输出格式：
## 已确认
- ...
## 已排除
- ...
## 待解决
- ..."""

    def __init__(self, keep_recent: int = 4,
                 summarize_fn: Callable[[str], str] | None = None,
                 max_summary_chars: int = 2000) -> None:
        self.keep_recent = keep_recent
        self.summarize_fn = summarize_fn or rule_based_summary
        self.max_summary_chars = max_summary_chars
        self._summary: str = ""
        self._compressed_upto: int = 0

    @property
    def summary(self) -> str:
        return self._summary

    def build(self, messages: Sequence[Message]) -> list[Message]:
        msgs = list(messages)
        if len(msgs) <= self.keep_recent + 2:
            return msgs
        cut = max(1, len(msgs) - self.keep_recent)
        while cut < len(msgs) and msgs[cut].role == "tool":
            cut += 1
        old = msgs[self._compressed_upto:cut]
        if old:
            self._summary = self._compress(self._summary, old)
            self._compressed_upto = cut
        out = list(msgs[:1])                     # system
        if self._summary:
            out.append(Message.system("## 早前对话摘要\n" + self._summary))
        out.extend(msgs[cut:])
        return out

    def _compress(self, prev: str, new: Sequence[Message]) -> str:
        raw = "\n".join(f"[{m.role}] {m.content[:600]}" for m in new if m.content)
        merged = (self.summarize_fn(raw) if not prev
                  else prev + "\n" + self.summarize_fn(raw))
        return merged[: self.max_summary_chars]


_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def rule_based_summary(text: str, max_lines: int = 40) -> str:
    """不依赖 LLM 的规则化摘要。

    两条规则：
    1. **所有数字都保留**（数字在临床场景里就是结论本身）
    2. 保留包含关键动词的行（发现、排除、确认、失败、错误）
    """
    if not text.strip():
        return ""
    keep: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        important = bool(_NUM_RE.search(s)) or re.search(
            r"发现|排除|确认|失败|错误|无法|缺失|不一致", s)
        key = re.sub(r"\W+", "", s)[:40]
        if important and key not in seen:
            seen.add(key)
            keep.append(s[:200])
        if len(keep) >= max_lines:
            break
    if not keep:
        return text[:400]
    return "## 已确认（规则化摘要，保留数字）\n" + "\n".join(f"- {k}" for k in keep)


# ===========================================================================
# 4. 上下文预算
# ===========================================================================
@dataclass
class ContextBudget:
    """上下文预算分配（第 21.2 节）。

    留一份"给输出的空间"是必要的 —— 把窗口填满，
    模型就没地方写答案了。
    """
    total: int = 32_000
    system: int = 2_000
    tools: int = 4_000
    plan: int = 1_000
    facts: int = 3_000
    recent: int = 6_000
    tool_results: int = 8_000
    retrieved: int = 4_000
    reserve_output: int = 4_000

    def check(self) -> list[str]:
        alloc = (self.system + self.tools + self.plan + self.facts
                 + self.recent + self.tool_results + self.retrieved
                 + self.reserve_output)
        if alloc > self.total:
            over = alloc - self.total
            return [f"预算超出 {over} token（分配 {alloc} / 总额 {self.total}）",
                    "建议：减少工具数量、缩短事实表、或改用摘要记忆"]
        return []

    def report(self) -> str:
        rows = [("System Prompt", self.system), ("工具清单", self.tools),
                ("任务计划", self.plan), ("事实表", self.facts),
                ("最近对话", self.recent), ("工具结果", self.tool_results),
                ("检索依据", self.retrieved), ("留给输出", self.reserve_output)]
        lines = [f"上下文预算（总额 {self.total}）", ""]
        for name, v in rows:
            bar = "█" * max(1, int(v / self.total * 40))
            lines.append(f"  {name:12s} {v:>6d}  {bar}")
        problems = self.check()
        lines.append("")
        lines.append("  ⚠️ " + problems[0] if problems else "  ✓ 预算分配合理")
        return "\n".join(lines)


def fact_table_text(facts: dict[str, Any] | Sequence[Any]) -> str:
    """把事实渲染成固定格式的表（模型"读"它，而不是"回忆"）。"""
    if isinstance(facts, dict):
        items = list(facts.items())
        if not items:
            return "（暂无已确认的事实）"
        lines = ["| 事实 | 值 |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in items]
        return "\n".join(lines)
    return "（暂无可渲染的事实）"
