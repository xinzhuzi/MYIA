"""Source engine: in-process scenario plugin ``myia-credhunter`` (链外引擎).

``engine: credhunter`` does not fetch the source URL — it loads the scenario
plugin adapter (``plugins/myia-credhunter/adapter.py``) in-process and lets it
assemble pipeline items from one of three lanes (task 10-03-aipocket-fusion,
通路 A of ``research/myia-integration-facts.md`` §2):

- ``scan``(缺省):本地文本扫描 —— ``engine_options.credhunter.documents``
  是命中位置文本批(text/url/source_type/file_path),跑供应商指纹库产出
  掩码-only items(手工分诊通道:把可疑日志/配置片段钉进品类即可入链);
- ``credhunt``:GitHub 工件猎取 —— token 池逐请求轮转 + 403/429 退避
  (行为规格 ghhunt.md);请求骑共享 context client(代理 transport/测试
  MockTransport 对本 lane 生效);跨 run checkpoint 经 ``checkpoint_file``
  落盘续传;
- ``exposure``:FOFA/Shodan 曝面 + L0 被动探测(行为规格 exposure.md);
  同步实现经 ``asyncio.to_thread`` 进 async 上下文。

Credential references (``github_tokens`` / ``fofa_apikey`` / ``shodan_apikey``)
are ``env:``/``keychain:`` refs resolved at fetch time — the adapter only ever
receives concrete values (插件纪律:密钥经参数注入). Missing credentials are
an **explicit empty state**, not an error (AC6): the lane reports
``credential_missing`` via :attr:`last_skip_reason` and yields zero items. A
tokenless credhunt lane is no exception — 上游无 token 时该源根本不注册
(ghhunt.md §2),引擎面同语义 = 本轮不启用(空态,写好引用即恢复产出);
「显式直跑」的 CLI ``shishi credhunt`` 面才报 ``tokens_missing`` 配置错误退 1
(adapter 层合同不变)。

Degradation contract: this engine is registered but never in :data:`AUTO_CHAIN`
(:func:`shishi.engines.registry.auto_degrade` gives it a single-rung chain); an
adapter that is missing/broken raises a structured ``FetchError`` which the
registry records as an :class:`EngineFailure` — the category's other sources
are unaffected (铁律).

Raises:
    FetchError: unknown lane, malformed options, adapter missing/broken, or
        a lane's own fetch failure. Credential resolution failures (tokenless
        credhunt included) degrade to the explicit empty state instead
        (AC6 + PRD P3 口径); the CLI ``shishi credhunt`` direct-run face
        still reports ``tokens_missing`` as a config error (exit 1).
"""

from __future__ import annotations

import json
import logging
import types
from pathlib import Path
from typing import Any

from shishi.engines.fetch_base import BaseEngine, FetchError
from shishi.schema import CredentialResolveError, resolve_credential

logger = logging.getLogger(__name__)

#: 引擎名(schema ``EngineName`` 词表一员;链外源引擎,不进 AUTO_CHAIN)。
LAYER = "SOURCE"

#: 场景插件目录内定位(与 CLI ``--plugins-dir`` 同一约定:cwd 相对)。
DEFAULT_PLUGINS_DIR = "plugins"

#: ``engine_options.credhunter.lane`` 的封闭词表。
LANES = ("scan", "credhunt", "exposure")

__all__ = ["DEFAULT_PLUGINS_DIR", "LANES", "LAYER", "CredhunterEngine", "import_credhunter_adapter"]


def import_credhunter_adapter(plugins_dir: str | Path = DEFAULT_PLUGINS_DIR) -> types.ModuleType:
    """动态加载 ``<plugins_dir>/myia-credhunter/adapter.py``(compile+exec).

    与 ``shishi.cli._import_plugin_adapter`` 同一手法与同一理由(引擎不 import
    CLI,保持 engines 层依赖方向干净):插件目录不是 Python 包,不走
    importlib 的 SourceFileLoader(会在插件目录写 ``__pycache__`` 垃圾);
    核心仓库与插件零静态耦合。加载失败抛 OSError/SyntaxError,由引擎统一
    包成结构化 ``credhunter_adapter_missing`` FetchError。
    """
    adapter_file = Path(plugins_dir) / "myia-credhunter" / "adapter.py"
    if not adapter_file.is_file():
        raise FileNotFoundError(f"credhunter 适配器不存在:{adapter_file}(场景件应随仓库 plugins/ 分发)")
    module = types.ModuleType("myia_myia_credhunter_adapter")
    module.__file__ = str(adapter_file)
    executable = compile(adapter_file.read_text(encoding="utf-8"), str(adapter_file), "exec")
    exec(executable, module.__dict__)  # noqa: S102 - 仓库内受控插件代码,非任意输入
    return module


class CredhunterEngine(BaseEngine):
    """In-process source engine backed by the myia-credhunter scenario plugin.

    Items come from the plugin's fingerprints (masked, Q9); ``extract`` is not
    consulted — a configured extract node is rejected structurally so the
    misconfiguration is visible instead of silently ignored.
    """

    LAYER = "SOURCE"
    ENGINE_NAME = "credhunter"
    REQUIRES_EXTRACT = False
    SUPPORTED_EXTRACT_TYPES = ()

    async def _fetch_impl(self) -> list[dict]:
        options = self.engine_options()
        lane = self._lane(options)
        if lane == "scan":
            return await self._fetch_scan(options)
        if lane == "credhunt":
            return await self._fetch_credhunt(options)
        return await self._fetch_exposure(options)

    # ------------------------------------------------------------- options

    def _lane(self, options: dict[str, Any]) -> str:
        lane = options.get("lane", "scan")
        if lane not in LANES:
            raise FetchError(
                f"engine_options.credhunter.lane 取值 {lane!r} 无效,应为 {'/'.join(LANES)}",
                error_type="invalid_lane",
            )
        return str(lane)

    def _adapter(self) -> Any:
        plugins_dir = self.engine_options().get("plugins_dir", DEFAULT_PLUGINS_DIR)
        if not isinstance(plugins_dir, str) or not plugins_dir.strip():
            raise FetchError(
                f"engine_options.credhunter.plugins_dir 应为非空字符串,当前为 {plugins_dir!r}",
                error_type="invalid_engine_options",
            )
        try:
            return import_credhunter_adapter(plugins_dir)
        except (OSError, ImportError, SyntaxError) as exc:
            raise FetchError(
                f"credhunter 适配器加载失败(plugins_dir={plugins_dir}):{exc}",
                error_type="credhunter_adapter_missing",
            ) from exc

    def _resolve_token_refs(self, refs: Any) -> list[str]:
        """Resolve the GitHub token pool, skipping unresolvable refs loudly.

        token 池语义(ghhunt.md §2):坏一个引用不废整池——解析失败的引用
        留 warning 后跳过,其余照用;全部不可用/未配置 = 无 token,由
        :meth:`_fetch_credhunt` 落显式空态(该源本轮不启用)。
        """
        if refs is None:
            return []
        if not isinstance(refs, list) or not all(isinstance(ref, str) for ref in refs):
            raise FetchError(
                "engine_options.credhunter.github_tokens 应为凭据引用字符串列表"
                '(如 ["keychain:myia/credhunter/github-token"])',
                error_type="invalid_engine_options",
            )
        tokens: list[str] = []
        for ref in refs:
            try:
                tokens.append(resolve_credential(ref, backend=self.context.keychain_backend))
            except CredentialResolveError as exc:
                logger.warning(
                    "credhunt lane token 解析失败(跳过该引用,全池不可用则空态)"
                    " source=%s ref=%s: %s",
                    self.source.name, ref, exc,
                )
        return tokens

    def _resolve_ref(self, ref: Any, *, field: str) -> str | None:
        """Resolve one optional credential ref (``None`` when absent)."""
        if ref is None:
            return None
        if not isinstance(ref, str) or not ref.strip():
            raise FetchError(
                f"engine_options.credhunter.{field} 应为凭据引用字符串或省略,当前为 {ref!r}",
                error_type="invalid_engine_options",
            )
        return resolve_credential(ref, backend=self.context.keychain_backend)

    def _resolve_lane_key(self, options: dict[str, Any], field: str) -> str | None:
        """Exposure lane 键解析:引用缺失/解析失败 → None(降级为显式空态).

        与 credhunt 的 token 池同口径(引用解析失败 → 空态 + warning 留痕),
        FOFA/Shodan 键缺失按 AC6 走源级降级——但解析失败必须留痕:warning
        日志 + 部分降级可见,绝不静默吞掉配置错误。
        """
        ref = options.get(field)
        if ref is None:
            return None
        try:
            return self._resolve_ref(ref, field=field)
        except CredentialResolveError as exc:
            logger.warning(
                "exposure lane 凭据解析失败,该 lane 降级为空态 source=%s field=%s: %s",
                self.source.name, field, exc,
            )
            return None

    # --------------------------------------------------------------- lanes

    async def _fetch_scan(self, options: dict[str, Any]) -> list[dict]:
        documents = options.get("documents")
        if documents is None:
            documents = []
        if not isinstance(documents, list) or not all(isinstance(doc, dict) for doc in documents):
            raise FetchError(
                "engine_options.credhunter.documents 应为映射列表"
                "(text/url 必填,source_type/file_path 可选)",
                error_type="invalid_engine_options",
            )
        adapter = self._adapter()
        try:
            items = await adapter.fetch(documents=documents)
        except Exception as exc:  # noqa: BLE001 — 插件失败结构化降级,不拦品类
            raise FetchError(f"credhunter 本地扫描失败:{exc}", error_type="credhunter_scan_failed") from exc
        if not documents:
            self.last_skip_reason = "documents_empty"
        return items

    async def _fetch_credhunt(self, options: dict[str, Any]) -> list[dict]:
        tokens = self._resolve_token_refs(options.get("github_tokens"))
        if not tokens:
            # ghhunt.md §2:上游无 token 时该源根本不注册(不报错、不启用)——
            # 引擎面同语义 = 显式空态:本轮不启用,写好 keychain 引用即恢复
            # 产出(「显式直跑」的 CLI shishi credhunt 面才报 tokens_missing
            # 配置错误退 1,见 CREDHUNT_CONFIG_FAILURE_CODES)。
            self.last_skip_reason = "credential_missing"
            logger.info(
                "credhunt lane 无可用 GitHub token,显式空态 source=%s"
                "(myia secret set myia/credhunter/github-token 后引用)",
                self.source.name,
            )
            return []
        checkpoint = self._load_checkpoint(options.get("checkpoint_file"))
        adapter = self._adapter()
        try:
            outcome = await adapter.fetch_hunt(
                github_tokens=tokens,
                queries=options.get("queries") if isinstance(options.get("queries"), list) else None,
                checkpoint=checkpoint,
                client=self.context.client,
            )
        except FetchError:
            raise
        except Exception as exc:  # noqa: BLE001 — 适配器结构化错误(to_dict 面向 CLI)
            raise FetchError(f"credhunter GitHub 猎取失败:{exc}", error_type="credhunt_failed") from exc
        errors = outcome.get("errors") or []
        if errors:
            logger.warning(
                "credhunt 泳道部分查询失败 source=%s errors=%s",
                self.source.name, errors[:3],
            )
        self._save_checkpoint(options.get("checkpoint_file"), outcome.get("checkpoint"))
        items = outcome.get("items") or []
        return [item for item in items if isinstance(item, dict)]

    async def _fetch_exposure(self, options: dict[str, Any]) -> list[dict]:
        # AC6 + PRD P3 口径:keychain 引用缺失(没配置/钥匙串没写)= 源级
        # 结构化降级(显式空态 + 可见 skip,不报错);配置了别的 lane 键则
        # 只降级缺的那条 lane。credhunt 的 token 池同口径:无 token = 该源
        # 本轮不启用(ghhunt.md §2 上游「根本不注册」语义)。
        fofa_key = self._resolve_lane_key(options, "fofa_apikey")
        shodan_key = self._resolve_lane_key(options, "shodan_apikey")
        if fofa_key is None and shodan_key is None:
            self.last_skip_reason = "credential_missing"
            logger.info(
                "exposure 双 lane 无凭据,显式空态 source=%s(myia secret set myia/credhunter/*)",
                self.source.name,
            )
            return []
        adapter = self._adapter()
        fofa_base = options.get("fofa_base")
        probe = bool(options.get("probe", True))
        try:
            payload = await adapter.fetch_exposure(
                fofa_key=fofa_key,
                fofa_base=fofa_base if isinstance(fofa_base, str) and fofa_base.strip() else None,
                shodan_key=shodan_key,
                probe=probe,
            )
        except Exception as exc:  # noqa: BLE001 — 插件失败结构化降级,不拦品类
            raise FetchError(f"credhunter 曝面探测失败:{exc}", error_type="exposure_failed") from exc
        lanes = payload.get("lanes") or {}
        for lane_name, lane in lanes.items():
            if isinstance(lane, dict) and lane.get("errors"):
                logger.warning("exposure lane=%s 查询部分失败:%s", lane_name, lane.get("per_query"))
        items = payload.get("findings") or []
        return [item for item in items if isinstance(item, dict)]

    # ---------------------------------------------------------- checkpoint

    def _load_checkpoint(self, path: Any) -> dict[str, Any] | None:
        """读跨 run checkpoint(``checkpoint_file`` 配置时;坏文件=从头再来)."""
        if path is None:
            return None
        if not isinstance(path, str) or not path.strip():
            raise FetchError(
                f"engine_options.credhunter.checkpoint_file 应为文件路径字符串,当前为 {path!r}",
                error_type="invalid_engine_options",
            )
        file = Path(path)
        if not file.is_file():
            return None
        try:
            loaded = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("credhunt checkpoint 不可读,从头续跑 path=%s: %s", path, exc)
            return None
        return loaded if isinstance(loaded, dict) else None

    def _save_checkpoint(self, path: Any, checkpoint: Any) -> None:
        """写回 checkpoint(读失败可容忍:跨 run 接续退化为从头,不拦本轮)."""
        if path is None or not isinstance(checkpoint, dict):
            return
        try:
            Path(path).write_text(json.dumps(checkpoint, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            logger.warning("credhunt checkpoint 写回失败 path=%s: %s", path, exc)
