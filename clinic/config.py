# -*- coding: utf-8 -*-
"""
配置与密钥管理（第 24.3 节）
================================

**本模块只做一件事：把所有"环境相关"的东西挡在业务代码之外。**

三个必须记住的原则：

1. **环境变量优先**（12-Factor 的第 3 条）。
   代码里不留任何"测试库 / 生产库"的分支 —— 差异全部走配置。
   一旦代码里出现 ``if env == "prod": url = "..."``，你就同时失去了
   "本地能复现生产"和"改配置不用改代码"两个好处。

2. **密钥不进代码、不进日志、不进对话上下文。**
   ``Settings`` 提供了 :meth:`Settings.redacted`，就是为了让
   "把配置打出来看看" 这个动作**不可能**泄漏密钥。
   第 23 章讲外部 API 时提过的"三不进"，这里是它的落地实现。

3. **启动时自检（fail fast）。**
   :meth:`Settings.validate_for_prod` 在进程启动阶段就把错配置拦下来。
   一个"能跑但配置是错的"服务，比一个"起不来"的服务危险得多：
   前者会安静地产生不可信的结果，后者至少你立刻知道。

依赖说明
--------
本实现**只用标准库**，接口刻意与 ``pydantic-settings`` 的 ``BaseSettings``
对齐 —— 真实项目里换成::

    from pydantic_settings import BaseSettings
    class Settings(BaseSettings): ...

即可，业务代码一行不用改。用标准库版本的理由有两个：
一是本仓库坚持"离线可跑、零依赖兜底"；二是**让你看清校验到底做了什么** ——
框架帮你做的事情，你应该至少亲手写一遍。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "Settings", "ConfigError", "SENSITIVE_FIELDS", "is_sensitive",
    "load_settings", "data_version_of",
]

# 字段名里出现这些**词**（按 ``_`` 分词后精确比对）就打码。
#
# ★ 为什么不是"子串匹配"：``max_tokens`` 含子串 ``token``，
#   但它是配额不是密钥 —— 子串匹配会把它一起打码，
#   于是日志里再也看不到真实的 token 预算。
#   脱敏规则**误伤**的代价是被日志骗，比漏记还阴险，所以这里必须精确。
SENSITIVE_FIELDS = ("api_key", "apikey", "token", "secret", "password",
                    "passwd", "credential", "authorization")


def is_sensitive(name: str) -> bool:
    """字段名是否属于"密钥类"。按分词精确比对，避免误伤。

    ``llm_api_key`` → True（末两段拼成 ``api_key``）
    ``max_tokens``  → False（词是 ``tokens``，不是 ``token``）
    ``client_secret`` → True
    """
    parts = name.lower().split("_")
    cands = set(parts)
    if len(parts) >= 2:
        cands.add("_".join(parts[-2:]))
    return bool(cands & set(SENSITIVE_FIELDS))

# 允许的取值（写出来是为了让错配置在自检阶段就暴露，而不是运行一天后才发现）
VALID_ENVS = ("dev", "staging", "prod")
VALID_PROVIDERS = ("mock", "openai", "azure", "internal")


class ConfigError(RuntimeError):
    """配置有问题 —— 启动阶段就该抛出来，不要带病运行。"""


def _truthy(raw: str) -> bool:
    return raw.strip().lower() in ("1", "true", "yes", "on", "y")


@dataclass
class Settings:
    """全部运行配置。本地读 ``.env``，生产读环境变量。

    字段命名与文档第 24.3 节一致；环境变量前缀 ``CPR_``
    （例如 ``CPR_LLM_PROVIDER``、``CPR_MAX_STEPS``）。
    """

    # ---- 环境标识 ----
    env: str = "dev"

    # ---- LLM ----
    llm_provider: str = "mock"            # mock / openai / azure / internal
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str = "gpt-4o-mini"

    # ---- 数据 ----
    data_dir: Path = Path("data/samples")
    # 默认放开本仓库 data/samples 里实际存在的域。
    # 生产环境应该显式给出**这个项目允许访问的全部数据集**，
    # 而不是"默认全都放开、出问题了再关" —— 白名单的意义在于默认拒绝。
    allowed_datasets: tuple[str, ...] = (
        "dm", "adsl", "adae", "ae", "adlbc_shift", "adtte", "ex", "ds", "vs_bp")

    # ---- 限额（三道预算闸的第一道：单次请求）----
    max_steps: int = 15
    max_tokens: int = 120_000
    request_timeout_sec: float = 300.0

    # ---- 审计 ----
    audit_dir: Path = Path("outputs/audit")

    # ---- 上限（超出即视为配置错误，而不是"用户想多跑几步"）----
    HARD_MAX_STEPS: int = 60
    HARD_MAX_TIMEOUT_SEC: float = 3600.0

    # ------------------------------------------------------------------
    # 构造
    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls, prefix: str = "CPR_",
                 env_file: str | Path | None = ".env",
                 environ: Mapping[str, str] | None = None) -> "Settings":
        """从环境变量构造。**环境变量优先于 .env 文件。**

        优先级（高 → 低）：显式传入的 environ > 进程环境变量 > .env > 默认值。
        这条顺序很重要：容器里注入的环境变量必须能覆盖镜像里带的 .env，
        否则"同一镜像多环境"就跑不起来。
        """
        src: dict[str, str] = {}
        if env_file:
            src.update(_read_dotenv(Path(env_file)))
        src.update(os.environ)
        if environ:
            src.update(environ)

        kwargs: dict[str, Any] = {}
        for f in fields(cls):
            if f.name.startswith("HARD_"):
                continue
            raw = src.get(prefix + f.name.upper())
            if raw is None:
                continue
            kwargs[f.name] = _coerce(f.type, raw)
        return cls(**kwargs)

    # ------------------------------------------------------------------
    # 自检：宁可起不来，也不要带着错配置跑
    # ------------------------------------------------------------------
    def validate(self) -> None:
        """**所有环境**都要通过的检查（含 dev）。"""
        problems: list[str] = []

        if self.env not in VALID_ENVS:
            problems.append(f"env={self.env!r} 不在 {list(VALID_ENVS)} 中")
        if self.llm_provider not in VALID_PROVIDERS:
            problems.append(
                f"llm_provider={self.llm_provider!r} 不在 {list(VALID_PROVIDERS)} 中")
        if self.max_steps < 1:
            problems.append(f"max_steps={self.max_steps} 必须 ≥ 1")
        if not self.allowed_datasets:
            problems.append("allowed_datasets 为空 —— 一个数据集都不放开，服务没有意义")
        if self.request_timeout_sec <= 0:
            problems.append(f"request_timeout_sec={self.request_timeout_sec} 必须 > 0")

        self._raise(problems, "基础配置校验失败")

    def validate_for_prod(self) -> None:
        """**生产环境**启动前的自检 —— 这是本模块最重要的一段代码。

        每一条都会真实地拦过事故，逐条解释：

        * ``mock`` LLM：结果看起来正常，但数字是编的。在临床场景里
          这比崩溃危险得多 —— 它不会被发现。
        * 缺 API Key：会在第一个用户请求时才报错，而不是启动时。
        * ``max_steps`` 过大：单次请求可能烧掉不可思议的 token，
          而且往往是"工具契约有问题、模型在反复试错"的征兆，
          放大预算只是把症状盖住。
        * ``data_dir`` 不存在：服务能起来，但每个请求都失败 ——
          又是一个"启动时能发现、却拖到运行时"的问题。
        """
        if self.env != "prod":
            return

        problems: list[str] = []

        if self.llm_provider == "mock":
            problems.append(
                "生产环境不允许使用 mock LLM（结果看似正常但数字是编的）")
        if self.llm_provider != "mock" and not self.llm_api_key:
            problems.append("缺少 CPR_LLM_API_KEY")
        if self.max_steps > self.HARD_MAX_STEPS:
            problems.append(
                f"max_steps={self.max_steps} 超过硬上限 {self.HARD_MAX_STEPS}")
        if self.request_timeout_sec > self.HARD_MAX_TIMEOUT_SEC:
            problems.append(
                f"request_timeout_sec={self.request_timeout_sec} "
                f"超过硬上限 {self.HARD_MAX_TIMEOUT_SEC}")
        if not Path(self.data_dir).is_dir():
            problems.append(f"data_dir={self.data_dir} 不存在")
        if not str(self.audit_dir).strip():
            problems.append("audit_dir 为空 —— 受监管环境必须有审计落盘路径")

        # 顺序也很重要：基础校验先跑，否则错误信息会互相掩盖
        self._raise(problems, "生产配置校验失败（fail fast：宁可起不来）")

    def validate_all(self) -> None:
        """``validate()`` + ``validate_for_prod()``，启动时调用这一个就够。"""
        self.validate()
        self.validate_for_prod()

    @staticmethod
    def _raise(problems: Iterable[str], title: str) -> None:
        items = list(problems)
        if items:
            raise ConfigError(title + "：\n  - " + "\n  - ".join(items))

    # ------------------------------------------------------------------
    # 安全输出
    # ------------------------------------------------------------------
    def to_dict(self, redact: bool = True) -> dict[str, Any]:
        """导出为字典，供日志/健康检查使用。

        ``redact=True``（默认）会把密钥类字段替换成 ``"***已设置***"`` /
        ``"未设置"`` —— **只暴露"有没有"，不暴露"是什么"**。
        这样"把配置打出来看看"就不会成为一次泄漏事故。
        """
        out: dict[str, Any] = {}
        for f in fields(self):
            if f.name.startswith("HARD_"):
                continue
            v = getattr(self, f.name)
            if redact and is_sensitive(f.name):
                out[f.name] = "***已设置***" if v else "未设置"
            elif isinstance(v, (Path, tuple)):
                out[f.name] = str(v) if isinstance(v, Path) else list(v)
            else:
                out[f.name] = v
        return out

    def describe(self) -> str:
        """一行行的可读摘要（已脱敏），适合启动日志。"""
        return "\n".join(f"  {k} = {v}" for k, v in self.to_dict().items())

    def is_prod(self) -> bool:
        return self.env == "prod"


# ===========================================================================
# 辅助
# ===========================================================================
def _read_dotenv(path: Path) -> dict[str, str]:
    """极简 ``.env`` 解析：``KEY=VALUE``，支持 ``#`` 注释与引号。

    不追求覆盖所有语法 —— 完整的实现交给 ``python-dotenv``。
    这里之所以自己写，是为了让"配置从哪来"这件事完全透明：
    当你排查"为什么这个值不对"时，能一条路径走到底。
    """
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        # 去掉成对引号（含"值里有空格必须加引号"这种常见写法）
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        out[k.strip()] = v
    return out


def _coerce(tp: Any, raw: str) -> Any:
    """把环境变量字符串转成字段类型。

    ``from __future__ import annotations`` 让 ``field.type`` 变成字符串，
    所以这里按**字符串名**判断，而不是按类型对象。
    """
    t = tp if isinstance(tp, type) else str(tp)
    name = t if isinstance(t, str) else getattr(t, "__name__", str(t))
    if "tuple" in name:
        return tuple(x.strip() for x in raw.split(",") if x.strip())
    if name == "int":
        return int(raw)
    if name == "float":
        return float(raw)
    if name == "bool":
        return _truthy(raw)
    if "Path" in name:
        return Path(raw)
    return raw


def data_version_of(paths: Sequence[str | Path]) -> str:
    """算出一批数据文件的"版本"指纹（第 24.6 节缓存用）。

    ★ **为什么必须算这个**：缓存键如果只有"工具名 + 参数"，
    数据更新后会返回**旧结果** —— 而且不会有任何报错。
    这是"静默返回错数据"这类事故里最常见的一种。

    用 ``mtime + size`` 而不是文件内容哈希：后者在几百 MB 的
    XPT 上每次请求都要全量读一遍，代价远大于收益。
    跨环境共享缓存时才需要内容哈希 —— 那时换成 sha256 并单独存储。
    """
    parts: list[str] = []
    for p in sorted(Path(x) for x in paths):
        if p.is_file():
            st = p.stat()
            parts.append(f"{p.name}:{int(st.st_mtime)}:{st.st_size}")
        else:
            parts.append(f"{p.name}:missing")
    return "|".join(parts)


def load_settings(environ: Mapping[str, str] | None = None,
                  env_file: str | Path | None = ".env",
                  prefix: str = "CPR_") -> Settings:
    """一步到位：读配置 → 校验 → 返回。**启动代码只应该调这个函数。**"""
    s = Settings.from_env(prefix=prefix, env_file=env_file, environ=environ)
    s.validate_all()
    return s
