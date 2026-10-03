"""myia-credhunter 适配器:进程内凭证猎手入口(引擎 + CLI 双面)。

本文件由 MYIA 仓库创作与维护,是上游(AGPL-3.0)行为的**功能重实现**:
实现只认行为规格文档(``.trellis/tasks/10-03-aipocket-fusion/research/
behavior-specs/*.md``),零上游代码/标识符/文案复制。

挂载方式(myia-integration-facts.md §1/§2):宿主(CLI 的
``_import_plugin_adapter`` / 未来 ``engine: credhunter`` 引擎)对本文件
做 compile+exec 动态加载 —— 核心仓库与插件零静态耦合。插件目录**不是**
Python 包:同目录 ``credhunter/*.py`` 子模块由本文件用同一 compile+exec
手法自举加载(``__file__`` 先行注入,数据文件靠它定位;刻意不走
importlib,防插件目录产生 ``__pycache__`` 垃圾)。

对外两副入口(v0.1 骨架 = R3 指纹库 + 本地文本扫描;R1 GitHub 猎取、
R2 验证/余额、R4 曝面发现按任务排期落地,本骨架先把 lane 状态面立起来):

- **引擎面(异步)**::func:`fetch` —— 消费 documents(命中位置文本),
  产出管线 items 列表(经 ``engine: credhunter`` 进 fetch→classify→dedup
  →store→push 全链,下游零改动);
- **CLI 面(同步)**::func:`run` —— 装配结构化 JSON payload(掩码-only,
  供 ``myia credhunter --json`` 类子命令与 AI 消费)。

纪律红线:
- **密钥经参数注入**(github_token/fofa_key/shodan_key 形参),绝不硬编码、
  不读环境变量;``keychain:`` 引用由宿主解析后传值(见插件 README);
- **无 key = 显式空态**(lane 状态 ``credential_missing``,status=empty,
  不报错不静默 —— AC6/R4 空态合规);
- **掩码-only 输出**(Q9):payload/items 里密钥只有前 8 后 4 掩码形态,
  全文永不进 stdout;
- 任何失败抛 :class:`CredhunterError`(code + message + details,``to_dict()``
  直接进 CLI JSON),失败码→退出码映射归 CLI 所有。
"""

from __future__ import annotations

import asyncio
import sys
import time
import types
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

__all__ = [
    "PROVIDES",
    "CredhunterError",
    "fetch",
    "findings",
    "fingerprints",
    "packs",
    "run",
    "scan_documents",
    "specs",
]

#: 插件 id(与 plugin.yaml 一致;宿主按 ``<plugins_dir>/myia-credhunter/`` 定位)。
PLUGIN_ID = "myia-credhunter"

#: 能力面(plugin.yaml provides 的代码侧镜像)。
PROVIDES = ("credhunt", "credcheck", "exposure")

#: 本适配器文件所在目录(宿主 compile+exec 前已注入 __file__)。
_ADAPTER_DIR = Path(__file__).resolve().parent

#: 子模块目录(插件内非包结构,逐文件 compile+exec 自举)。
_MODULES_DIR = _ADAPTER_DIR / "credhunter"


def _load_module(name: str) -> types.ModuleType:
    """compile+exec 加载一个 ``credhunter/<name>.py`` 子模块(零 __pycache__)。

    与宿主 ``myia.cli._import_plugin_adapter`` 同一手法与同一理由:不走
    importlib 的 SourceFileLoader(会在插件目录写字节码垃圾,污染插件包
    形状)。差异点:子模块**登记进 ``sys.modules``** —— 模块内 dataclass
    的字符串注解解析会按 ``cls.__module__`` 反查 sys.modules,不登记会在
    类定义期直接 AttributeError(登记只是还原 import 语义,仍零磁盘副作用)。
    加载失败抛 OSError/SyntaxError,由调用命令结构化降级。
    """
    module_file = _MODULES_DIR / f"{name}.py"
    if not module_file.is_file():
        raise FileNotFoundError(f"credhunter 子模块不存在:{module_file}")
    module_name = f"myia_credhunter_{name}"
    module = types.ModuleType(module_name)
    module.__file__ = str(module_file)
    sys.modules[module_name] = module
    executable = compile(module_file.read_text(encoding="utf-8"), str(module_file), "exec")
    exec(executable, module.__dict__)  # noqa: S102 - 仓库内受控插件代码,非任意输入
    return module


#: 子模块挂载(测试/引擎经适配器模块属性直达;数据文件随子模块定位)。
packs = _load_module("packs")
specs = _load_module("specs")
fingerprints = _load_module("fingerprints")
findings = _load_module("findings")


class CredhunterError(Exception):
    """结构化适配器错误:code + message + details,``to_dict()`` 进 JSON 输出."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        """结构化形态:{"code", "message", **details}(供 CLI --json 与 agent 自修)."""
        return {"code": self.code, "message": self.message, **self.details}


# ---------------------------------------------------------------------------
# 本地扫描核心(R3 指纹库;引擎面与 CLI 面共用)
# ---------------------------------------------------------------------------


def _lane_states(*, github_token: str | None, fofa_key: str | None, shodan_key: str | None) -> dict[str, str]:
    """三条出网 lane 的状态面:有 key=ready(排期落地),无 key=显式空态。

    v0.1 骨架:出网 lane 尚未实现(排期件),统一报 ``scheduled``;凭据
    缺失时先报 ``credential_missing``(AC6 语义:显式、不报错、不静默)。
    """
    states: dict[str, str] = {}
    for lane, key in (("credhunt", github_token), ("exposure_fofa", fofa_key), ("exposure_shodan", shodan_key)):
        if not key:
            states[lane] = "credential_missing"
        else:
            states[lane] = "scheduled"
    states["credcheck"] = "scheduled"  # 读库后处理,凭据来自猎取产物而非参数
    return states


def scan_documents(documents: Sequence[Mapping[str, Any]] | None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """对一批命中位置文本跑指纹扫描,装配掩码-only items。

    documents 每项:``text``(必填)、``url``(必填,命中位置证据 URL)、
    ``source_type``(缺省 manual)、``file_path``(可选)。同一 key 出现于
    >5 个不同 url 按教程/蜜罐键剔除(跨位置预过滤)。

    Returns:
        (items, counts):items 为管线形状 dict 列表(url/title/source +
        metadata);counts 为扫描统计(文档数/命中数/噪声后/跨位置剔除)。

    Raises:
        CredhunterError: 文档形状坏(``invalid_document``)。
    """
    counts = {"documents": 0, "raw_hits": 0, "kept": 0, "overexposed_dropped": 0}
    validated: list[tuple[str, str, str, str | None]] = []  # text, url, source_type, file_path
    for index, document in enumerate(documents or []):
        if not isinstance(document, Mapping):
            raise CredhunterError(
                "invalid_document", f"documents[{index}] 必须是映射(text/url 字段),当前为 {type(document).__name__}"
            )
        text = document.get("text")
        url = document.get("url")
        if not isinstance(text, str):
            raise CredhunterError("invalid_document", f"documents[{index}].text 必须是字符串")
        if not isinstance(url, str) or not url.strip():
            raise CredhunterError(
                "invalid_document", f"documents[{index}].url 必填(命中位置证据 URL,成为 item.url)"
            )
        source_type = document.get("source_type", "manual")
        if not isinstance(source_type, str) or not source_type.strip():
            raise CredhunterError("invalid_document", f"documents[{index}].source_type 必须是非空字符串")
        file_path = document.get("file_path")
        if file_path is not None and not isinstance(file_path, str):
            raise CredhunterError("invalid_document", f"documents[{index}].file_path 必须是字符串或省略")
        validated.append((text, url.strip(), source_type.strip(), file_path))
    counts["documents"] = len(validated)

    hits_by_location: dict[str, list[str]] = {}
    per_document: list[tuple[str, list[Any]]] = []
    for text, url, _source_type, _file_path in validated:
        hits = fingerprints.extract_secrets(text)
        counts["raw_hits"] += len(hits)
        hits_by_location[url] = [hit.apikey for hit in hits]
        per_document.append((url, hits))
    overexposed = fingerprints.drop_overexposed(hits_by_location)

    items: list[dict[str, Any]] = []
    for (url, hits), (text, _u, source_type, file_path) in zip(per_document, validated, strict=True):
        for hit in hits:
            if hit.apikey in overexposed:
                continue
            items.append(
                findings.build_finding_item(
                    apikey=hit.apikey,
                    provider=hit.provider,
                    source_url=url,
                    apiurl=hit.apiurl,
                    source_type=source_type,
                    matched_by=hit.matched_by,
                    variable=hit.variable,
                    file_path=file_path,
                    context_excerpt=text,
                )
            )
    counts["overexposed_dropped"] = len(overexposed)
    counts["kept"] = len(items)
    return items, counts


# ---------------------------------------------------------------------------
# 引擎面(异步):engine: credhunter 的 fetch 入口
# ---------------------------------------------------------------------------


async def fetch(*, documents: Sequence[Mapping[str, Any]] | None = None) -> list[dict[str, Any]]:
    """引擎面入口:消费 documents,返回管线 items(下游 classify/dedup/store/push)。

    v0.1 为纯 CPU 本地扫描(指纹库);GitHub/FOFA/Shodan 出网 lane 按排期
    落地后扩展参数(github_token 等,密钥由引擎宿主解析 keychain: 后注入)。
    """
    await asyncio.sleep(0)  # 引擎上下文让出事件循环(与 async 契约对齐)
    items, _counts = scan_documents(documents)
    return items


# ---------------------------------------------------------------------------
# CLI 面(同步):结构化 JSON payload
# ---------------------------------------------------------------------------


def run(
    *,
    documents: Sequence[Mapping[str, Any]] | None = None,
    github_token: str | None = None,
    fofa_key: str | None = None,
    shodan_key: str | None = None,
    clock: Any = time.monotonic,
) -> dict[str, Any]:
    """跑一次本地指纹扫描,装配掩码-only 结构化 payload(CLI stdout 面)。

    Args:
        documents: 命中位置文本批(text/url/source_type/file_path)。
        github_token: GitHub 泳道凭据(**参数注入**,宿主解析 keychain: 引用;
            本骨架不消费,只进 lane 状态面)。
        fofa_key / shodan_key: 曝面 lane 凭据(同上)。
        clock: 单调时钟注入口(测试 mock 用)。

    Returns:
        结构化 payload:plugin/mode/status/lane 状态面/发现层与验证层数据
        计数/findings(掩码-only items)/counts/duration_seconds。
        无文档或零命中 → ``status="empty"``(显式空态,不是错误)。

    Raises:
        CredhunterError: 文档形状坏(invalid_document);数据文件坏
            (packs/specs loader 抛的 ValueError 向上透传,code=credhunter_failed)。
    """
    started = clock()
    try:
        loaded_packs = packs.load_packs()
        loaded_specs = specs.load_specs()
        items, counts = scan_documents(documents)
    except ValueError as exc:  # PackDataError/SpecDataError/文档校验统一结构化
        code = getattr(exc, "code", "credhunter_failed")
        raise CredhunterError(str(code), str(exc)) from exc
    return {
        "plugin": PLUGIN_ID,
        "mode": "in_process",
        "provides": list(PROVIDES),
        "status": "success" if items else "empty",
        "lanes": _lane_states(github_token=github_token, fofa_key=fofa_key, shodan_key=shodan_key),
        "library": {
            "packs": len(loaded_packs),
            "specs": len(loaded_specs),
            "github_query_pool": len(packs.github_query_pool(loaded_packs)),
        },
        "findings": items,
        "counts": counts,
        "duration_seconds": round(clock() - started, 3),
    }
