"""企业微信通道:自建应用 ``gettoken`` + ``message/send``(text 私聊)。

蓝本归属(10-03-messaging-w2-platforms):出站形态移植自 Hermes
``plugins/platforms/wecom/callback_adapter.py`` 的 ``_send_text`` /
``_get_access_token`` / ``_refresh_access_token``(NousResearch/Hermes-Agent,
MIT;上游路径 ``~/.hermes/hermes-agent/plugins/platforms/wecom/callback_adapter.py``,
端点常量见其 ``_TOKEN_URL``/``_SEND_URL``,40001/42001 重试见其两轮循环)。
MYIA 按本档语义重写,不整块复制:

- **token 生命周期**(:func:`WecomChannel._get_access_token`):
  ``GET gettoken?corpid=&corpsecret=`` → ``access_token`` + ``expires_in``
  (缺省 7200s,蓝本 ``ACCESS_TOKEN_TTL_SECONDS``);缓存判据沿用蓝本
  「剩余 > 60s 才算有效」。MYIA 的缓存落点从蓝本的进程内 dict 改为数据根
  ``wecom_token_cache.json``(design D2:通道实例随派发重建,跨 run 复用
  7200s 窗口;原子写 tmp+rename,损坏/不可写退化重取,绝不阻塞发送)。
  **重试**:``message/send`` 报 ``40001``(secret 不合法)/``42001``
  (access_token 已过期,官方全局错误码)→ 逐出缓存重取一次再发(蓝本
  两轮循环同款);重试后仍失败才浮为 ``wecom_api_error``。
- **text 私聊**:`msgtype=text`、``touser`` 寻址(蓝本 ``chat_id.split(":",
  1)[-1]`` 同款兼容 ``corpid:userid`` 形态);**2048 字节**分块
  (:data:`MAX_CONTENT_BYTES`,蓝本 ``MAX_MESSAGE_LENGTH``/``_utf8_len``
  同一判据)按行边界切、逐块顺序发送;群聊/markdown 是蓝本外能力,
  不做(prd 非目标)。

寻址(design D1/D4):``supports_targeting=True``;``context.target.chat_id``
(userid)优先,退回 legacy ``target`` 引用(缺省 ``env:WECOM_TUSER``)。
直达解析 ``wecom:<userid>``(保守字符集:字母/数字/``-``/``_``;WeCom
UserID 不含中文,非命中形态回落目录四路径)。目录无自动发现(蓝本事实,
应用消息无「列出收件人」API),别名手工登记。

错误消息携带 ``errcode=<n>`` 与 ``HTTP <status>`` 供死信分类
(:func:`shishi.push.delivery.classify_dead_error`):40001/42001(重取后仍
失败)、60020(不安全的访问 IP)、60021(userid 不在可见范围)、81013
(touser 全部非法)→ forbidden;40003/60111/46004(userid 无效/不存在)
→ not_found;45009(接口调用超过限制)/``-1``(系统繁忙)/超时 → 瞬态。

凭据安全基线同其余通道:corpid/corpsecret/agentid 全为 ``env:``/
``keychain:`` 引用,发送期才解析,错误只带引用名。token 缓存文件是数据根
私有数据(与 channel_directory.json 同盘同隐私面),内含短时效(≤7200s)
access_token;不可写时退化为每次发送现取,绝不阻塞。

All HTTP I/O goes through an injectable ``httpx.AsyncClient`` — tests use
``httpx.MockTransport`` and never touch the real API.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable, Sequence

import httpx

from shishi.push.base import (
    DEFAULT_SEND_TIMEOUT_SECONDS,
    PushSendError,
    SendContext,
    TrendAwareChannel,
)
from shishi.push.directory import DirectoryDiscoverUnsupported
from shishi.push.ntfy import build_message
from shishi.push.targets import RESOLVED_DIRECT, ChannelTarget
from shishi.push.templates import TemplateRenderError, TemplateRenderer
from shishi.schema import CredentialResolveError, resolve_credential

__all__ = [
    "DEFAULT_AGENTID_REF",
    "DEFAULT_CORPID_REF",
    "DEFAULT_CORPSECRET_REF",
    "DEFAULT_TARGET_ENV_REF",
    "MAX_CONTENT_BYTES",
    "SEND_URL",
    "TOKEN_EXPIRY_MARGIN_SECONDS",
    "TOKEN_RETRY_ERRCODES",
    "TOKEN_TTL_SECONDS",
    "TOKEN_URL",
    "TOKEN_CACHE_FILENAME",
    "USERID_RE",
    "WecomChannel",
    "split_utf8_chunks",
]

logger = logging.getLogger(__name__)

#: 官方端点(蓝本同款常量)。
TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
SEND_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"
#: access_token 有效期缺省(秒,官方 gettoken 应答的 expires_in,蓝本同款)。
TOKEN_TTL_SECONDS = 7200
#: 缓存提前失效余量(秒):剩余寿命 > 60s 才复用(蓝本 ``now + 60`` 同判据)。
TOKEN_EXPIRY_MARGIN_SECONDS = 60.0
#: message/send 报这些 errcode → 逐出缓存重取一次再发(蓝本两轮循环)。
TOKEN_RETRY_ERRCODES = frozenset({40001, 42001})
#: text.content 上限(字节,蓝本 MAX_MESSAGE_LENGTH/_utf8_len 同判据)。
MAX_CONTENT_BYTES = 2048
#: token 缓存文件名(数据根下;design D2)。
TOKEN_CACHE_FILENAME = "wecom_token_cache.json"
#: legacy touser 引用缺省(私聊 userid;corpid:userid 复合形态兼容)。
DEFAULT_TARGET_ENV_REF = "env:WECOM_TUSER"
#: 应用凭据引用缺省(schema 字段可覆写;蓝本无 env 名,MYIA 按 telegram 先例)。
DEFAULT_CORPID_REF = "env:WECOM_CORPID"
DEFAULT_CORPSECRET_REF = "env:WECOM_CORPSECRET"
DEFAULT_AGENTID_REF = "env:WECOM_AGENTID"
#: 直达 userid 形态(保守字符集:字母/数字/-/_;非命中回落目录四路径)。
USERID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def split_utf8_chunks(text: str, *, limit: int = MAX_CONTENT_BYTES) -> list[str]:
    """按 **UTF-8 字节** 上限切块,行边界优先(企微 2048 字节判据)。

    蓝本经 ``truncate_message(content, 2048, len_fn=_utf8_len)`` 分块
    (fence 续开/序号标记是 Hermes 版式花活,MYIA 通知版式用不到,取其
    字节判据与「逐块顺序、不丢内容」的核心语义重写):优先在换行处切;
    单行超限硬切;空串返回 ``[]``;块序即发送序。
    """
    if not text:
        return []
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining.encode("utf-8")) <= limit:
            chunks.append(remaining)
            break
        # 行边界优先:在字节预算内找最后一个换行(按字符扫描换行位置)。
        cut = 0
        used = 0
        for index, line in enumerate(remaining.split("\n")):
            line_bytes = len(line.encode("utf-8")) + (1 if index else 0)
            if used + line_bytes > limit:
                break
            cut = (cut + 1 + len(line)) if cut else len(line)
            used += line_bytes
        if cut <= 0:
            # 首行即超限:按字符硬切到字节预算(多字节字符不跨块)。
            width = 0
            for position in range(len(remaining)):
                width += len(remaining[position].encode("utf-8"))
                if width > limit:
                    break
            cut = max(position, 1)
        chunks.append(remaining[:cut].rstrip("\n"))
        remaining = remaining[cut:].lstrip("\n")
    return [chunk for chunk in chunks if chunk]


class WecomChannel(TrendAwareChannel):
    """``wecom`` channel:corpid+corpsecret 换 token → text 私聊(touser)。

    Args:
        target: touser 的凭据引用(userid 或 ``corpid:userid``;发送期
            解析);省略 → :data:`DEFAULT_TARGET_ENV_REF`;定向
            (``targets``)在场时可省(schema 允许),touser 由
            ``context.target`` 给出。
        corpid_ref: 企业 ID 引用;省略 → :data:`DEFAULT_CORPID_REF`。
        corpsecret_ref: 应用 Secret 引用;省略 → :data:`DEFAULT_CORPSECRET_REF`。
        agentid_ref: 应用 AgentId 引用(数值串);省略 →
            :data:`DEFAULT_AGENTID_REF`。
        token_cache_path: token 缓存文件路径(pipeline 接数据根);``None``
            = 不落盘(进程内缓存,测试/隔离形态)。
        template: 可选用户模板(Jinja2);在场时渲染输出为消息文本;
            省略 → 内置纯文本版式(ntfy 同款)。
        renderer: 模板渲染器;缺省共享沙箱实例。
        client: 注入用 ``httpx.AsyncClient``(测试 mock 点);省略则每次
            发送自建带 ``timeout`` 的 client。
        timeout: 自管 client 的发送超时秒数。
        clock: 时间源注入(秒;缓存寿命判据,测试可钉死)。

    Raises:
        PushSendError: 凭据解析失败、agentid 非数值、HTTP 传输失败、
            非 JSON 响应、``errcode != 0``(含 40001/42001 重取后仍失败),
            或模板渲染失败。
    """

    name = "wecom"
    #: 目录寻址已开(context.target = userid 优先,legacy target 兜底);
    #: 协议判定见 base.Channel docstring。
    supports_targeting = True

    def __init__(
        self,
        *,
        target: str | None = None,
        corpid_ref: str | None = None,
        corpsecret_ref: str | None = None,
        agentid_ref: str | None = None,
        token_cache_path: str | Path | None = None,
        template: str | None = None,
        renderer: TemplateRenderer | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_SEND_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._target = target
        self._corpid_ref = corpid_ref or DEFAULT_CORPID_REF
        self._corpsecret_ref = corpsecret_ref or DEFAULT_CORPSECRET_REF
        self._agentid_ref = agentid_ref or DEFAULT_AGENTID_REF
        self._token_cache_path = Path(token_cache_path) if token_cache_path else None
        self._template = template
        self._renderer = renderer or TemplateRenderer()
        self._client = client
        self._timeout = timeout
        self._clock = clock
        self._cached_token: str | None = None
        self._cached_until: float = 0.0

    async def send(self, items: Sequence[Any], context: SendContext) -> None:
        """Render + 逐块 POST;定向优先(``context.target.chat_id``)。

        Raises:
            PushSendError: on any credential/transport/API failure (callers
                isolate per channel; nothing is raised on success). 块序发送,
                中途失败整通道报错(已落块的重复投递由摘要留池语义吸收,
                telegram 同款取舍)。
        """
        corpid, corpsecret, agentid = self._resolve_credentials()
        touser = (
            context.target.chat_id if context.target is not None else self._resolve_touser()
        )
        # 蓝本同款:chat_id 允许 corpid:userid 复合形态,touser 取 userid 段。
        touser = touser.split(":", 1)[-1].strip()
        if not touser:
            raise PushSendError(
                "missing_target",
                "wecom 两条寻址路径均缺席:未配置 legacy target(touser 引用),"
                "本次发送也未携带 context.target",
            )
        for chunk in split_utf8_chunks(self._compose(items, context)):
            await self._send_text(corpid, corpsecret, agentid, touser, chunk)
        logger.debug(
            "wecom 已提交: slot=%s kind=%s count=%d touser_ref=%s",
            context.slot,
            context.kind,
            len(items),
            context.target or self._target,
        )

    # ------------------------------------------------------------- compose

    def _compose(self, items: Sequence[Any], context: SendContext) -> str:
        if self._template is None:
            return build_message(items, context)
        # 契约:send 只抛 PushSendError —— 渲染失败包装为结构化的
        # template_render_error(语法错误已在加载期被 schema 拒绝)。
        try:
            return self._renderer.render(
                self._template, items, context, **self.trend_render_kwargs()
            )
        except TemplateRenderError as exc:
            raise PushSendError(
                "template_render_error", f"push[].template 渲染失败: {exc}"
            ) from exc

    # ---------------------------------------------------------- credentials

    def _resolve_credentials(self) -> tuple[str, str, int]:
        """三凭据引用 → ``(corpid, corpsecret, agentid:int)``。"""
        corpid = self._resolve_ref(self._corpid_ref, "wecom corpid")
        corpsecret = self._resolve_ref(self._corpsecret_ref, "wecom corpsecret")
        agentid_raw = self._resolve_ref(self._agentid_ref, "wecom agentid").strip()
        try:
            agentid = int(agentid_raw)
        except ValueError as exc:
            raise PushSendError(
                "invalid_credential_ref",
                f"wecom agentid 引用 {self._agentid_ref!r} 解析结果不是数值: {agentid_raw[:32]!r}",
            ) from exc
        return corpid, corpsecret, agentid

    def _resolve_touser(self) -> str:
        if self._target is None:
            return ""
        return self._resolve_ref(self._target, "wecom touser")

    def _resolve_ref(self, reference: str, label: str) -> str:
        try:
            return resolve_credential(reference)
        except CredentialResolveError as exc:
            raise PushSendError(exc.code, f"{label} 解析失败: {exc}") from exc

    # -------------------------------------------------------- token 生命周期

    async def _get_access_token(self, corpid: str, corpsecret: str) -> str:
        """有效缓存(剩余 > 60s)直接复用;否则 gettoken 现取并落缓存。"""
        now = self._clock()
        if self._cached_token and now < self._cached_until - TOKEN_EXPIRY_MARGIN_SECONDS:
            return self._cached_token
        cached = self._read_token_cache()
        if cached is not None:
            token, fetched_at, expires_in = cached
            if now < fetched_at + expires_in - TOKEN_EXPIRY_MARGIN_SECONDS:
                self._cached_token, self._cached_until = token, fetched_at + expires_in
                return token
        token, expires_in = await self._fetch_token(corpid, corpsecret)
        fetched_at = self._clock()
        self._cached_token, self._cached_until = token, fetched_at + expires_in
        self._write_token_cache(token, fetched_at, expires_in)
        return token

    async def _fetch_token(self, corpid: str, corpsecret: str) -> tuple[str, float]:
        """``GET gettoken`` → ``(access_token, expires_in)``;errcode≠0 报错。"""
        try:
            response = await self._request(
                "GET", TOKEN_URL, params={"corpid": corpid, "corpsecret": corpsecret}
            )
        except httpx.HTTPError as exc:
            raise PushSendError(
                "http_error", f"企微 gettoken 请求失败: {type(exc).__name__}: {exc}"
            ) from exc
        data = self._parse_json(response, "gettoken")
        if data.get("errcode"):
            raise PushSendError(
                "wecom_api_error",
                f"企微 gettoken 返回错误: errcode={data.get('errcode')}"
                f" errmsg={data.get('errmsg')}",
            )
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise PushSendError(
                "invalid_response",
                f"企微 gettoken 应答缺 access_token: {str(data)[:200]!r}",
            )
        try:
            expires_in = float(data.get("expires_in") or TOKEN_TTL_SECONDS)
        except (TypeError, ValueError):
            expires_in = float(TOKEN_TTL_SECONDS)
        return token, expires_in

    def _read_token_cache(self) -> tuple[str, float, float] | None:
        """读缓存文件;缺失/损坏/形态坏 → None(best-effort,绝不抛)。"""
        if self._token_cache_path is None:
            return None
        try:
            raw = json.loads(self._token_cache_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None
        token = raw.get("token")
        fetched_at, expires_in = raw.get("fetched_at"), raw.get("expires_in")
        if (
            not isinstance(token, str)
            or not token
            or not isinstance(fetched_at, (int, float))
            or not isinstance(expires_in, (int, float))
        ):
            return None
        return token, float(fetched_at), float(expires_in)

    def _write_token_cache(self, token: str, fetched_at: float, expires_in: float) -> None:
        """原子写缓存(tmp+rename);失败仅告警(蓝本 best-effort 同款)。"""
        if self._token_cache_path is None:
            return
        payload = {"token": token, "fetched_at": fetched_at, "expires_in": expires_in}
        try:
            path = self._token_cache_path
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            logger.warning(
                "企微 token 缓存写入失败(退化为每次现取): path=%s error=%s",
                self._token_cache_path,
                exc,
            )

    def _evict_token_cache(self) -> None:
        """40001/42001 逐出:内存态清空 + 缓存文件删除(下轮现取)。"""
        self._cached_token, self._cached_until = None, 0.0
        if self._token_cache_path is not None:
            try:
                self._token_cache_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("企微 token 缓存删除失败: path=%s error=%s", self._token_cache_path, exc)

    # ------------------------------------------------------------- send text

    async def _send_text(
        self, corpid: str, corpsecret: str, agentid: int, touser: str, content: str
    ) -> None:
        """一块 text:蓝本两轮循环——40001/42001 逐出缓存重取一次再发。"""
        payload = {
            "touser": touser,
            "msgtype": "text",
            "agentid": agentid,
            "text": {"content": content},
            "safe": 0,
        }
        for attempt in (1, 2):
            token = await self._get_access_token(corpid, corpsecret)
            try:
                response = await self._request(
                    "POST", SEND_URL, params={"access_token": token}, json_payload=payload
                )
            except httpx.HTTPError as exc:
                raise PushSendError(
                    "http_error", f"企微 message/send 请求失败: {type(exc).__name__}: {exc}"
                ) from exc
            data = self._parse_json(response, "message/send")
            errcode = data.get("errcode") or 0
            if not errcode:
                return
            if errcode in TOKEN_RETRY_ERRCODES and attempt == 1:
                logger.warning(
                    "企微 token 被拒(errcode=%s),逐出缓存重取一次", errcode
                )
                self._evict_token_cache()
                continue
            # errcode=N 进文案 → 死信分类(40001/42001 重取后仍失败按
            # 凭据级 forbidden;45009/-1 等无 marker = 瞬态不标)。
            raise PushSendError(
                "wecom_api_error",
                f"企微 API 返回错误: errcode={errcode} errmsg={data.get('errmsg')}",
            )
        raise AssertionError("unreachable: retry loop must return or raise")  # pragma: no cover

    # -------------------------------------------------------------- http base

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
    ) -> httpx.Response:
        if self._client is not None:
            if method == "GET":
                return await self._client.get(url, params=params)
            return await self._client.post(url, params=params, json=json_payload)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            if method == "GET":
                return await client.get(url, params=params)
            return await client.post(url, params=params, json=json_payload)

    @staticmethod
    def _parse_json(response: httpx.Response, label: str) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise PushSendError(
                "invalid_response",
                f"企微 {label} 响应不是 JSON(HTTP {response.status_code}):"
                f" {response.text[:200]!r}",
            ) from exc
        if not isinstance(data, dict):
            raise PushSendError(
                "invalid_response",
                f"企微 {label} 响应不是 JSON 对象: {str(data)[:200]!r}",
            )
        return data

    # --------------------------------------------- 目录(无自动发现)+ 直达

    async def discover_directory(self) -> list[Any]:
        """企微应用消息无目录发现(蓝本事实;群聊/markdown 为蓝本外不做)。"""
        raise DirectoryDiscoverUnsupported(
            "企微无自动发现(蓝本事实):应用消息按 touser 私聊寻址,"
            "无「列出收件人」API;直达写 wecom:<userid>,常用对象用别名登记"
        )

    @classmethod
    def parse_direct_ref(cls, ref: str) -> ChannelTarget | None:
        """直达:``wecom:<userid>``(字母/数字/``-``/``_``)不经目录。

        非命中形态(中文别名等)返回 None(调用方回落目录四路径)。
        ``corpid:userid`` 复合形态发送期拆分(:meth:`send`),解析层原样保留。
        """
        value = ref.strip()
        if USERID_RE.fullmatch(value):
            return ChannelTarget(
                platform="wecom", chat_id=value, resolved_from=RESOLVED_DIRECT
            )
        return None
