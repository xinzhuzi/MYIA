"""myia-credhunter 适配器:进程内凭证猎手入口(引擎 + CLI 双面)。

本文件由 MYIA 仓库创作与维护,是上游(AGPL-3.0)行为的**功能重实现**:
实现只认行为规格文档(``.trellis/tasks/10-03-aipocket-fusion/research/
behavior-specs/*.md``),零上游代码/标识符/文案复制。

挂载方式(myia-integration-facts.md §1/§2):宿主(CLI 的
``_import_plugin_adapter`` 与 ``engine: credhunter`` 引擎)对本文件
做 compile+exec 动态加载 —— 核心仓库与插件零静态耦合。插件目录**不是**
Python 包:同目录 ``credhunter/*.py`` 子模块由本文件用同一 compile+exec
手法自举加载(``__file__`` 先行注入,数据文件靠它定位;刻意不走
importlib,防插件目录产生 ``__pycache__`` 垃圾)。

对外入口(三 lane 各有引擎面/CLI 面,再加本地扫描面):

- **本地扫描(R3 指纹库)**::func:`fetch`(引擎面,异步)/ :func:`run`
  (CLI 面,同步)—— 消费 documents(命中位置文本),产出掩码-only items;
- **credhunt(R1 GitHub 工件猎取)**::func:`fetch_hunt`(引擎面,异步)/
  :func:`run_credhunt`(CLI 面,同步)—— 两泳道 + 工件二次加工,产出
  items + 跨 run checkpoint;命中同步入本地密钥库(全文只进库,Q9 对外
  仍掩码,见下);
- **exposure(R4 FOFA/Shodan 曝面)**::func:`fetch_exposure`(引擎面,
  to_thread 包同步实现)/ :func:`run_exposure`(CLI 面,同步)—— 搜页 +
  L0 被动探测(服务暴露面命中无凭据,不入密钥库);
- **credcheck(R2 验证/余额)**::func:`run_credcheck`(``--apikey`` 显式
  传键)/ :func:`run_credcheck_keystore`(``--from-keystore`` 读密钥库)
  双 CLI 入口,均不走引擎(见 integration-facts §2);「读库→回填」
  通路已落地插件侧 keystore:猎手命中入库(指纹→全文)→
  ``credcheck.run_from_keystore`` 逐条探测 → ``check_state``/
  ``last_check`` 回填(核心仓库 store/* 深集成仍列后续件)。

纪律红线:
- **密钥经参数注入**(github_tokens/fofa_key/shodan_key/records 形参),
  绝不硬编码、不读环境变量;``keychain:`` 引用由宿主解析后传值(见插件
  README);
- **无 key = 显式空态**(lane 状态 ``credential_missing``,status=empty,
  不报错不静默 —— AC6/R4 空态合规;credhunt 例外:GitHub 无 token 该源
  不启用 —— 本适配器(CLI 直跑面)抛 ``tokens_missing`` 结构化错误,规格
  §2 同款;品类源走引擎面时由宿主引擎落 ``credential_missing`` 空态);
- **掩码-only 输出**(Q9):payload/items 里密钥只有前 8 后 4 掩码形态,
  全文永不进 stdout;全文只落**本机密钥库**(keystore,``$MYIA_HOME``
  或开发态 cwd 下 ``credhunter-keystore.json``,600 权限,数据域不出
  本机、不入 git);
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
    "credcheck",
    "exposure",
    "fetch",
    "fetch_exposure",
    "fetch_hunt",
    "findings",
    "fingerprints",
    "fofa_query_pool",
    "ghhunt",
    "keystore",
    "packs",
    "run",
    "run_credcheck",
    "run_credcheck_keystore",
    "run_credhunt",
    "run_exposure",
    "scan_documents",
    "shodan_query_pool",
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

    与宿主 ``shishi.cli._import_plugin_adapter`` 同一手法与同一理由:不走
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
ghhunt = _load_module("ghhunt")
credcheck = _load_module("credcheck")
keystore = _load_module("keystore")
exposure = _load_module("exposure")


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
    """三条出网 lane 的状态面:有 key=ready,无 key=显式空态。

    credcheck 不看 lane 凭据(要验的密钥来自 records 参数/猎取产物),
    状态恒 ``ready``。
    """
    states: dict[str, str] = {}
    for lane, key in (("credhunt", github_token), ("exposure_fofa", fofa_key), ("exposure_shodan", shodan_key)):
        states[lane] = "ready" if key else "credential_missing"
    states["credcheck"] = "ready"
    return states


# ---------------------------------------------------------------------------
# 查询池(发现层数据文件 → 三 lane 缺省查询)
# ---------------------------------------------------------------------------


def _query_pool(field: str, loaded_packs: Sequence[Any]) -> list[str]:
    """按声明顺序拼接去重各包的某类查询(fofa/shodan 同 github 口径)。"""
    pool: list[str] = []
    seen: set[str] = set()
    for pack in loaded_packs:
        for query in getattr(pack, field, ()):
            if query not in seen:
                seen.add(query)
                pool.append(query)
    return pool


def fofa_query_pool(loaded_packs: Sequence[Any]) -> list[str]:
    """发现层全部包的 FOFA 查询池(拼接去重,保声明序)。"""
    return _query_pool("fofa_queries", loaded_packs)


def shodan_query_pool(loaded_packs: Sequence[Any]) -> list[str]:
    """发现层全部包的 Shodan 查询池(拼接去重,保声明序)。"""
    return _query_pool("shodan_queries", loaded_packs)


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
    """引擎面入口(本地扫描 lane):消费 documents,返回管线 items。

    下游 classify/dedup/store/push 全链复用;出网 lane 走 :func:`fetch_hunt`
    / :func:`fetch_exposure`。
    """
    await asyncio.sleep(0)  # 引擎上下文让出事件循环(与 async 契约对齐)
    items, _counts = scan_documents(documents)
    return items


# ---------------------------------------------------------------------------
# 引擎面(异步):credhunt / exposure 出网 lane
# ---------------------------------------------------------------------------


def _default_keystore_factory() -> Any:
    """缺省密钥库工厂:``MYIA_HOME``(优先)或 cwd 下 credhunter-keystore.json。"""
    return keystore.Keystore(keystore.default_keystore_path())


class _LazyKeystore:
    """首次命中才落盘的惰性密钥库代理(零命中/凭据缺失不建空文件)。

    引擎面与 CLI 面缺省注入本代理:``credhunter-keystore.json`` 只在真有
    全文命中要入库时创建(构造期即建档会让每次零命中 run 在数据域留空库,
    也让 tokens_missing 一类先失败路径无谓写盘)。
    """

    def __init__(self, factory: Any) -> None:
        self._factory = factory
        self._store: Any = None

    def record(self, fingerprint: str, apikey: str, provider: str, source_url: str = "") -> None:
        """ghhunt 的 duck-typed 入库口(首调构造真库,后续直通)。"""
        if self._store is None:
            self._store = self._factory()
        self._store.record(fingerprint, apikey, provider, source_url)


async def fetch_hunt(
    *,
    github_tokens: Sequence[str] | None = None,
    queries: Sequence[str] | None = None,
    checkpoint: Mapping[str, Any] | None = None,
    client: Any = None,
    keystore_store: Any = None,
) -> dict[str, Any]:
    """引擎面入口(credhunt lane):跑一轮 GitHub 工件猎取。

    Args:
        github_tokens: GitHub token 池(密钥由引擎宿主解析 ``keychain:``/
            ``env:`` 引用后注入;空池抛 ``tokens_missing``,该源不启用)。
        queries: 查询池;缺省 = 发现层数据文件全部包 github_terms 去重。
        checkpoint: 跨 run 状态(引擎宿主持久化后下轮传入即接续)。
        client: 注入的 ``httpx.AsyncClient``(引擎传共享 context client,
        让代理 transport/测试 MockTransport 对本 lane 生效)。
        keystore_store: 密钥库(duck-typed ``record``);缺省注入惰性代理
        (首命中落 ``$MYIA_HOME``/cwd 下 ``credhunter-keystore.json``,
        600 权限)—— 全文只进库,items 仍掩码-only(Q9)。

    Returns:
        ``ghhunt.hunt`` 原样产物:``{"items", "errors", "checkpoint",
        "counts"}``(items 掩码-only 管线形状)。
    """
    kwargs: dict[str, Any] = {"tokens": list(github_tokens or [])}
    if queries is not None:
        kwargs["queries"] = list(queries)
    if checkpoint is not None:
        kwargs["checkpoint"] = dict(checkpoint)
    if client is not None:
        kwargs["client"] = client
    kwargs["keystore"] = keystore_store if keystore_store is not None else _LazyKeystore(_default_keystore_factory)
    try:
        return await ghhunt.hunt(**kwargs)
    except ValueError as exc:  # GithubHuntError/KeystoreError 同为 ValueError 族,code 透传
        raise CredhunterError(str(getattr(exc, "code", "credhunt_failed")), str(exc)) from exc


async def fetch_exposure(
    *,
    fofa_key: str | None = None,
    fofa_base: str | None = None,
    shodan_key: str | None = None,
    queries: Mapping[str, Sequence[str]] | None = None,
    probe: bool = True,
) -> dict[str, Any]:
    """引擎面入口(exposure lane):FOFA/Shodan 搜页 + L0 被动探测。

    exposure 实现是同步的(自身带页间 sleep 节奏),引擎面经
    ``asyncio.to_thread`` 包进 async 上下文,不让页间等待阻塞事件循环。
    查询缺省 = 发现层数据文件的 fofa/shodan 查询池;``queries`` 形如
    ``{"fofa": [...], "shodan": [...]}`` 可覆写。

    Returns:
        ``exposure.run_exposure`` 原样产物:``{"status", "lanes", "counts",
        "findings", "duration_seconds"}``;无 key 的 lane 报
        ``credential_missing`` 显式空态(不抛错)。
    """
    loaded_packs = packs.load_packs()
    fofa_queries = (queries or {}).get("fofa")
    shodan_queries = (queries or {}).get("shodan")
    return await asyncio.to_thread(
        exposure.run_exposure,
        fofa_key=fofa_key,
        fofa_base=fofa_base,
        shodan_key=shodan_key,
        fofa_queries=fofa_query_pool(loaded_packs) if fofa_queries is None else list(fofa_queries),
        shodan_queries=shodan_query_pool(loaded_packs) if shodan_queries is None else list(shodan_queries),
        probe=probe,
    )


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
            本地扫描面不消费,只进 lane 状态面;真出网走
            :func:`run_credhunt`)。
        fofa_key / shodan_key: 曝面 lane 凭据(同上;真出网走
            :func:`run_exposure`)。
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


# ---------------------------------------------------------------------------
# CLI 面(同步):三 lane 子命令 payload(shishi credhunt/credcheck/exposure)
# ---------------------------------------------------------------------------


def run_credhunt(
    *,
    github_tokens: Sequence[str] | None = None,
    queries: Sequence[str] | None = None,
    checkpoint: Mapping[str, Any] | None = None,
    clock: Any = time.monotonic,
) -> dict[str, Any]:
    """CLI 面入口(``shishi credhunt``):单轮 GitHub 猎取 stdout payload。

    正式产出走引擎进管线;本面是调试/冒烟口。tokens 为空抛
    ``tokens_missing``(GitHub 无 token 该源不启用,规格 §2);查询页失败
    只进 errors 不打断整轮(规格 §7)。``status``:success(≥1 item)/
    empty(0 item 0 error)/ degraded(0 item 有 error)/ partial(有 item
    有 error)——退出码映射归 CLI。

    Returns:
        结构化 payload:plugin/status/library/findings(掩码-only)/errors/
        counts/checkpoint(调用方可持久化后下轮传入)/duration_seconds。
    """
    started = clock()
    loaded_packs = packs.load_packs()
    outcome = asyncio.run(
        fetch_hunt(
            github_tokens=github_tokens,
            queries=queries if queries is not None else packs.github_query_pool(loaded_packs),
            checkpoint=checkpoint,
        )
    )
    items, errors = outcome["items"], outcome["errors"]
    status = "success" if items and not errors else ("partial" if items else ("degraded" if errors else "empty"))
    return {
        "plugin": PLUGIN_ID,
        "capability": "credhunt",
        "status": status,
        "library": {
            "packs": len(loaded_packs),
            "specs": len(specs.load_specs()),
            "github_query_pool": len(packs.github_query_pool(loaded_packs)),
        },
        "findings": items,
        "errors": errors,
        "counts": outcome["counts"],
        "checkpoint": outcome["checkpoint"],
        "duration_seconds": round(clock() - started, 3),
    }


def run_credcheck(
    *,
    records: Sequence[Mapping[str, Any]],
    probe_balance: bool = False,
    clock: Any = time.monotonic,
) -> dict[str, Any]:
    """CLI 面入口(``shishi credcheck``):凭证验证(+可选余额)payload。

    Args:
        records: 待验凭证批,每项 ``{"apikey": str, "apiurl": str(可省)}``
            (全文密钥只在 credcheck 模块作用域内用作探测头,永不落 payload;
            掩码-only 输出,Q9)。
        probe_balance: Q7 余额/身份探测开关(默认**关**,显式开)。

    Returns:
        结构化 payload:plugin/capability/status/results(result_to_dict 列
        表)/counts(按 validation_state 计数)/duration_seconds。探测结论
        (rejected/final_verified)是**数据**不是错误:status 恒 success,
        transient 占比只进 counts(退出码映射归 CLI)。

    Raises:
        CredhunterError: records 形状坏(``invalid_record``,结构化降级)。
    """
    started = clock()
    registry = specs.ProviderResolver(specs.load_specs())
    try:
        results = credcheck.check_credentials(list(records), registry=registry, probe_balance=probe_balance)
    except ValueError as exc:
        raise CredhunterError("invalid_record", str(exc)) from exc
    state_counts: dict[str, int] = {}
    for result in results:
        state_counts[result.validation_state] = state_counts.get(result.validation_state, 0) + 1
    return {
        "plugin": PLUGIN_ID,
        "capability": "credcheck",
        "status": "success",
        "probe_balance": probe_balance,
        "results": [credcheck.result_to_dict(result) for result in results],
        "counts": {
            "records": len(results),
            **state_counts,
        },
        "duration_seconds": round(clock() - started, 3),
    }


def run_credcheck_keystore(
    *,
    keystore_path: str | Path | None = None,
    probe_balance: bool = False,
    limit: int | None = None,
    rpm: int = credcheck.PER_PROVIDER_RPM_LIMIT,
    clock: Any = time.monotonic,
) -> dict[str, Any]:
    """CLI 面入口(``shishi credcheck --from-keystore``):读密钥库验证 payload。

    与 :func:`run_credcheck`(``--apikey`` 显式传键)构成 credcheck 双入口:
    本面读猎手落盘的本地密钥库(缺省 ``$MYIA_HOME``/cwd 下
    ``credhunter-keystore.json``,600 权限),逐条验证并把 ``check_state``/
    ``last_check`` 回填入库 ——「猎→存→验」闭环。探测结论(rejected/
    final_verified)是**数据**不是错误:status 恒 success,transient 占比
    只进 counts(退出码映射归 CLI)。

    Args:
        keystore_path: 密钥库路径(缺省走 ``keystore.default_keystore_path``)。
        probe_balance: Q7 余额/身份探测开关(默认**关**,显式开)。
        limit: 最多验证条数(缺省全量,按入库顺序)。
        rpm: Q8 每供应商 RPM 上限(60/rpm = 同供应商最小间隔;缺省 30)。
        clock: 单调时钟注入口(测试 mock 用)。

    Returns:
        结构化 payload:plugin/capability/status/source(keystore)/
        keystore_path/keystore_records(库内指纹总数)/probe_balance/
        results(掩码-only,逐条含 fingerprint + masked_apikey)/counts/
        duration_seconds。

    Raises:
        CredhunterError: 库文件坏/记录形状坏(``keystore_invalid``/
        ``keystore_unreadable``/``invalid_record``,结构化降级)。
    """
    started = clock()
    try:
        outcome = credcheck.run_from_keystore(
            keystore_path if keystore_path is not None else keystore.default_keystore_path(),
            balance=probe_balance,
            limit=limit,
            rpm=rpm,
        )
    except ValueError as exc:  # KeystoreError/记录形状坏统一结构化
        code = getattr(exc, "code", "invalid_record")
        raise CredhunterError(str(code), str(exc)) from exc
    return {
        "plugin": PLUGIN_ID,
        "capability": "credcheck",
        "status": "success",
        "source": "keystore",
        "keystore_path": outcome["keystore_path"],
        "keystore_records": outcome["records"],
        "probe_balance": probe_balance,
        "results": outcome["results"],
        "counts": outcome["counts"],
        "duration_seconds": round(clock() - started, 3),
    }


def run_exposure(
    *,
    fofa_key: str | None = None,
    fofa_base: str | None = None,
    shodan_key: str | None = None,
    probe: bool = True,
    clock: Any = time.monotonic,
) -> dict[str, Any]:
    """CLI 面入口(``shishi exposure``):FOFA/Shodan 曝面 + L0 探测 payload。

    无 key 的 lane 报 ``credential_missing`` 显式空态(AC6,不抛错);查询
    缺省 = 发现层数据文件查询池。``status`` 沿 exposure 模块口径
    (success/empty),退出码映射(含 degraded 判定)归 CLI。

    Returns:
        结构化 payload:plugin/capability + ``exposure.run_exposure`` 产物
        (status/lanes/counts/findings/duration_seconds)。
    """
    started = clock()
    payload = asyncio.run(
        fetch_exposure(fofa_key=fofa_key, fofa_base=fofa_base, shodan_key=shodan_key, probe=probe)
    )
    payload.update(
        {
            "plugin": PLUGIN_ID,
            "capability": "exposure",
            "duration_seconds": round(clock() - started, 3),
        }
    )
    return payload
