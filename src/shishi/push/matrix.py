"""Matrix 通道:任意 homeserver 的 Client-Server API(``m.room.message`` 纯文本)。

蓝本归属(10-03-messaging-w3-longtail 组二):出站形态对照 Hermes
``plugins/platforms/matrix/adapter.py``(NousResearch/Hermes-Agent,MIT)的
send 路径(``m.room.message`` / msgtype=text / 消息长度上限)——上游经
mautrix 客户端库并含 E2EE/入站整套,**MYIA 出站-only 走纯 CS API REST**
(``httpx``,零新依赖,蓝本依赖路线不移植);``MATRIX_MAX_MESSAGE_LENGTH``
缺省 16000 的量级亦取自蓝本 env 文档。

- **发送**:`PUT {homeserver}/_matrix/client/v3/rooms/{roomId}/send/
  m.room.message/{txnId}`(Bearer access token,body ``{"msgtype": "m.text",
  "body": …}``);``txnId`` 每次发送新生成(uuid,幂等键),超长文本按
  :data:`MESSAGE_LIMIT` 自动分段(telegram ``split_message`` 同款换行边界
  优先,逐段顺序 PUT)。
- **房间别名**:`#alias:server` 形态先经 ``GET /_matrix/client/v3/directory/
  room/{alias}`` 解析成 ``!roomId:server`` 再发送(CS API 发送端点只收
  room id);``!…`` 原生 id 直发。
- **错误**:非 2xx 时 Matrix 以 ``{"errcode": "M_FORBIDDEN", "error": …}``
  应答;错误文案保留 ``HTTP <status>`` + 原厂 errcode/error 片段,供死信
  分类(:func:`shishi.push.delivery.classify_dead_error`:403 → forbidden、
  404 → not_found、429/M_LIMIT_EXCEEDED → 瞬态)。

寻址(design D1/D4):``supports_targeting=True``;``context.target.chat_id``
优先(房间 id 或别名皆可),退回 legacy ``target`` 引用;两路全缺报
``missing_target``。直达解析 ``!room:server`` / ``#alias:server``。目录无
自动发现(CS API 的 joined_rooms 无名称、逐房取 name 不成目录;蓝本走
mautrix 常驻会话,MYIA 出站-only 不含),抛
:class:`~shishi.push.directory.DirectoryDiscoverUnsupported`。

凭据安全基线同其余通道:YAML 只写 ``env:``/``keychain:`` 引用,发送期才
解析;错误文案只带引用名,绝不带解析值。All HTTP I/O goes through an
injectable ``httpx.AsyncClient`` — tests use ``httpx.MockTransport``.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Mapping, Sequence
from urllib.parse import quote

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
from shishi.push.telegram import split_message
from shishi.push.templates import TemplateRenderError, TemplateRenderer
from shishi.schema import CredentialResolveError, resolve_credential

__all__ = [
    "DEFAULT_SERVER_ENV_REF",
    "DEFAULT_TARGET_ENV_REF",
    "DEFAULT_TOKEN_ENV_REF",
    "MatrixChannel",
    "MESSAGE_LIMIT",
    "ROOM_ALIAS_RE",
    "ROOM_ID_RE",
    "room_send_url",
    "resolve_alias_url",
]

logger = logging.getLogger(__name__)

#: Access-token credential reference (resolved at send time; value never logged).
DEFAULT_TOKEN_ENV_REF = "env:MATRIX_ACCESS_TOKEN"
#: Homeserver base-URL credential reference(自建 matrix.example.com 或 matrix.org)。
DEFAULT_SERVER_ENV_REF = "env:MATRIX_HOMESERVER"
#: Legacy 单房 target 引用(constructor ``target`` 覆写;定向 targets 在场可省)。
DEFAULT_TARGET_ENV_REF = "env:MATRIX_ROOM_ID"
#: 单条 ``m.room.message`` body 上限(蓝本 MATRIX_MAX_MESSAGE_LENGTH 缺省量级;
#: homeserver 事件上限普遍 65536,16000 留足事件 JSON 包裹余量)。
MESSAGE_LIMIT = 16000
#: 原生房间 id 形态 ``!opaque:server.tld``(sigil + 非空白非冒号 + 冒号 + server)。
ROOM_ID_RE = re.compile(r"^![^\s:]+:[^\s:]+$")
#: 房间别名形态 ``#name:server.tld``(发送前经 directory 端点解析成房间 id)。
ROOM_ALIAS_RE = re.compile(r"^#[^\s:]+:[^\s:]+$")
#: CS API 路径(拼在 homeserver base URL 之后)。
_ROOM_SEND_PATH = "/_matrix/client/v3/rooms/{room_id}/send/m.room.message/{txn_id}"
_DIRECTORY_ROOM_PATH = "/_matrix/client/v3/directory/room/{alias}"


def room_send_url(server: str, room_id: str, txn_id: str) -> str:
    """拼装发送端点 URL;room_id/txn_id 均 URL 编码(id 含 ``!``/``$`` 等保留字符)。"""
    path = _ROOM_SEND_PATH.format(
        room_id=quote(room_id, safe=""), txn_id=quote(txn_id, safe="")
    )
    return f"{server.rstrip('/')}{path}"


def resolve_alias_url(server: str, alias: str) -> str:
    """拼装房间别名解析端点 URL(``#`` sigil 亦编码)。"""
    return f"{server.rstrip('/')}{_DIRECTORY_ROOM_PATH.format(alias=quote(alias, safe=''))}"


class MatrixChannel(TrendAwareChannel):
    """``matrix`` channel:每条消息一至多次 ``PUT …/send/m.room.message/{txn}``。

    Args:
        target: 房间 id 或别名的凭据引用(``env:MATRIX_ROOM_ID`` style,发送期
            解析);定向(``targets``)在场时可省——两条寻址路径至少一条,
            否则发送期报 ``missing_target``。
        server_ref: homeserver base URL 引用;省略 → :data:`DEFAULT_SERVER_ENV_REF`。
        token_ref: access token 引用;省略 → :data:`DEFAULT_TOKEN_ENV_REF`。
        template: 可选用户模板(Jinja2);省略 → 内置纯文本版式(ntfy 同款,
            跨通道标题一致)。
        renderer: 模板渲染器;缺省共享沙箱实例。
        client: 注入用 ``httpx.AsyncClient``(测试 mock 点);省略则每次发送
            自建带 ``timeout`` 的 client。
        timeout: 自管 client 的发送超时秒数。

    Raises:
        PushSendError: 凭据解析失败、别名解析失败、HTTP 传输失败、非 JSON
            响应或 Matrix 返回非 2xx(errcode/error 原样入文案)。分段按序
            发送,中途失败整次失败(digest.py 留池重试契约,telegram 同款
            取舍)。
    """

    name = "matrix"
    #: 目录寻址已开:context.target 优先,legacy target 兜底;协议判定见
    #: base.Channel docstring。
    supports_targeting = True

    def __init__(
        self,
        *,
        target: str | None = None,
        server_ref: str | None = None,
        token_ref: str | None = None,
        template: str | None = None,
        renderer: TemplateRenderer | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_SEND_TIMEOUT_SECONDS,
    ) -> None:
        self._target = target
        self._server_ref = server_ref
        self._token_ref = token_ref
        self._template = template
        self._renderer = renderer or TemplateRenderer()
        self._client = client
        self._timeout = timeout

    async def send(self, items: Sequence[Any], context: SendContext) -> None:
        """Render + 分段 PUT;定向优先(``context.target.chat_id``)。

        Raises:
            PushSendError: on any credential/transport/API failure (callers
                isolate per channel; nothing is raised on success).
        """
        token = self._resolve_credential(self._token_ref or DEFAULT_TOKEN_ENV_REF, "matrix access token")
        server = self._resolve_credential(self._server_ref or DEFAULT_SERVER_ENV_REF, "matrix homeserver")
        chat_id = (
            context.target.chat_id
            if context.target is not None
            else self._resolve_target()
        )
        room_id = await self._ensure_room_id(server, token, chat_id)
        parts = self._compose(items, context)
        for text in parts:
            txn_id = f"myia-{uuid.uuid4().hex}"  # 幂等键:每次发送全新
            await self._put_message(token, room_send_url(server, room_id, txn_id), text)
        logger.debug(
            "matrix 发送完成: slot=%s kind=%s count=%d parts=%d",
            context.slot,
            context.kind,
            len(items),
            len(parts),
        )

    # ------------------------------------------------------------- compose

    def _compose(self, items: Sequence[Any], context: SendContext) -> list[str]:
        """消息分段:模板渲染输出或内置纯文本版式,均按 :data:`MESSAGE_LIMIT` 切。"""
        if self._template is not None:
            # 契约:send 只抛 PushSendError —— 渲染失败包装为结构化的
            # template_render_error(语法错误已在加载期被 schema 拒绝)。
            try:
                text = self._renderer.render(
                    self._template, items, context, **self.trend_render_kwargs()
                )
            except TemplateRenderError as exc:
                raise PushSendError(
                    "template_render_error", f"push[].template 渲染失败: {exc}"
                ) from exc
            return split_message(text, limit=MESSAGE_LIMIT)
        return split_message(build_message(items, context), limit=MESSAGE_LIMIT)

    # ------------------------------------------------------------- resolve

    def _resolve_credential(self, reference: str, label: str) -> str:
        try:
            return resolve_credential(reference).strip()
        except CredentialResolveError as exc:
            raise PushSendError(exc.code, f"matrix {label} 凭据解析失败: {exc}") from exc

    def _resolve_target(self) -> str:
        if self._target is None:
            raise PushSendError(
                "missing_target",
                "matrix 未配置 legacy target(env:/keychain: 引用,房间 id 或 #别名),"
                "本次发送也未携带 context.target——两条寻址路径至少一条在场",
            )
        try:
            value = resolve_credential(self._target).strip()
        except CredentialResolveError as exc:
            raise PushSendError(exc.code, f"matrix target 解析失败: {exc}") from exc
        if not value:
            raise PushSendError(
                "invalid_credential_ref",
                f"matrix target 引用 {self._target!r} 解析结果为空",
            )
        return value

    async def _ensure_room_id(self, server: str, token: str, chat_id: str) -> str:
        """``!…`` 直用;``#alias:server`` 先解析;其余形态结构化拒绝(绝不猜房)。"""
        if ROOM_ID_RE.fullmatch(chat_id):
            return chat_id
        if not ROOM_ALIAS_RE.fullmatch(chat_id):
            # 解析值不回显(模块自宣基线「错误文案只带引用名」;且回显串若含
            # HTTP 状态样文本会污染死信分类的判读面)。
            raise PushSendError(
                "invalid_credential_ref",
                f"matrix target 形态非法(须为 !room:server 或 #alias:server):"
                f"得到 {len(chat_id)} 字符的值,不匹配任一形态(解析值不回显)",
            )
        url = resolve_alias_url(server, chat_id)
        headers = {"Authorization": f"Bearer {token}"}
        try:
            if self._client is not None:
                response = await self._client.get(url, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise PushSendError(
                "http_error", f"matrix 房间别名解析请求失败: {type(exc).__name__}: {exc}"
            ) from exc
        # CS API directory 端点应答 ``{"room_id": "!…", "servers": […]}``(顶层裸字段)。
        envelope = self._parse_response(response, context="房间别名解析")
        room_id = envelope.get("room_id")
        if not isinstance(room_id, str) or not ROOM_ID_RE.fullmatch(room_id.strip()):
            raise PushSendError(
                "invalid_response",
                f"matrix 房间别名解析响应缺 room_id: {str(envelope)[:200]!r}",
            )
        return room_id.strip()

    # ---------------------------------------------------------------- post

    async def _put_message(self, token: str, url: str, text: str) -> dict[str, Any]:
        body = {"msgtype": "m.text", "body": text}
        headers = {"Authorization": f"Bearer {token}"}
        try:
            if self._client is not None:
                response = await self._client.put(url, headers=headers, json=body)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.put(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise PushSendError(
                "http_error", f"matrix 请求失败: {type(exc).__name__}: {exc}"
            ) from exc
        return self._parse_response(response)

    @staticmethod
    def _parse_response(response: httpx.Response, *, context: str = "发送") -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise PushSendError(
                "invalid_response",
                f"matrix 响应不是 JSON(HTTP {response.status_code}): {response.text[:200]!r}",
            ) from exc
        if response.is_success and isinstance(data, Mapping):
            return dict(data)
        errcode = data.get("errcode") if isinstance(data, Mapping) else None
        error = data.get("error") if isinstance(data, Mapping) else response.text[:200]
        # 文案保留 HTTP 状态与原厂 errcode/error:死信分类按「HTTP 403/404」
        # 命中(M_FORBIDDEN+403 → forbidden、M_NOT_FOUND+404 → not_found、
        # M_LIMIT_EXCEEDED/429 → 瞬态不标)。
        raise PushSendError(
            "matrix_api_error",
            f"matrix API {context}返回错误: HTTP {response.status_code} "
            f"errcode={errcode} error={error}",
        )

    # --------------------------------------------- 目录(无自动发现)+ 直达

    async def discover_directory(self) -> list[Any]:
        """matrix 无出站目录发现:结构化说明,而非假装刷新。

        CS API 的 ``joined_rooms`` 只回 id 列表(无名称,逐房取 name 是 N+1
        而非目录);蓝本的房间枚举建立在 mautrix 常驻会话上,MYIA 出站-only
        不含。条目唯一来源 = 别名文件手工登记 + 直达 ``!room``/``#alias`` id。
        """
        raise DirectoryDiscoverUnsupported(
            "matrix 无出站目录发现(蓝本走 mautrix 常驻会话,MYIA 出站-only 不含):"
            "直达写 matrix:!room:server 或 matrix:#alias:server,"
            "常用地名可用别名文件登记"
        )

    @classmethod
    def parse_direct_ref(cls, ref: str) -> ChannelTarget | None:
        """直达:``!room:server``(原生 id)与 ``#alias:server``(别名)不经目录。

        其余(中文名等)返回 None,调用方回落目录四路径解析。
        """
        value = ref.strip()
        if ROOM_ID_RE.fullmatch(value) or ROOM_ALIAS_RE.fullmatch(value):
            return ChannelTarget(
                platform="matrix", chat_id=value, resolved_from=RESOLVED_DIRECT
            )
        return None
