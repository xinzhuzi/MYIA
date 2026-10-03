"""Feishu card callback endpoint (服务端/compose 形态, grill Q7 定案).

**默认关闭** (security baseline: 回调 API 默认关闭);开启必须同时满足:

- **强制 token 鉴权**: every request must carry the configured credential —
  either our own header (``X-Myia-Token``, e.g. injected by the reverse
  proxy in front of the endpoint) or Feishu's verification ``token`` inside
  the callback payload. Mismatch → 401, structured body.
- **仅内网**: the listener refuses public bind addresses (only loopback /
  private-range IPs, same policy as the ingest API); requests from public
  client IPs are rejected 403 when the peer address is known.

The core is transport-agnostic: :class:`FeishuCallbackHandler.handle` turns
``(headers, body, client_ip)`` into a structured :class:`CallbackResponse`
(status + JSON payload + parsed feedback callbacks) — unit-testable with
zero sockets. The stdlib ``http.server`` runner (:func:`build_server`,
``python -m shishi.push.feishu_callback``) is the compose-form delivery: the
core dependency set stays at six packages, no web framework.

Payload contract (button ``value``, both Feishu schema generations):
``{"feedback": "good"|"bad", "item": "<dedup_key>"}`` (``verdict`` /
``dedup_key`` accepted as aliases; a stringified value is parsed). Feishu's
``url_verification`` handshake is answered with the challenge echo — but
only after the token check (开启即强制鉴权,握手也不例外).

Parsed callbacks go to :func:`shishi.feedback.ingest_callbacks` (dangling
dedup keys — retention-pruned items — are tolerated like every channel path).
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import sys
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping, Sequence

from shishi.feedback import ingest_callbacks, normalize_verdict
from shishi.schema import parse_secret_value, resolve_credential
from shishi.store import FEEDBACK_CHANNEL_FEISHU, Store

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "DEFAULT_TOKEN_ENV_REF",
    "TOKEN_HEADER",
    "CallbackResponse",
    "FeishuCallbackConfig",
    "FeishuCallbackConfigError",
    "FeishuCallbackHandler",
    "build_server",
]

logger = logging.getLogger(__name__)

#: Verification-token credential reference (与推送 bot token 无关;服务端专配).
DEFAULT_TOKEN_ENV_REF = "env:MYIA_FEISHU_CALLBACK_TOKEN"
#: Our own auth header (reverse-proxy 注入或直连方携带).
TOKEN_HEADER = "X-Myia-Token"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8730

_VALUE_KEYS = ("feedback", "verdict")
_ITEM_KEYS = ("item", "dedup_key")


def _extract_event_id(data: Mapping[str, Any]) -> str | None:
    """Feishu 事件幂等身份:``header.event_id``(v2 事件)或顶层 ``event_id``."""
    header = data.get("header")
    if isinstance(header, Mapping):
        event_id = header.get("event_id")
        if isinstance(event_id, str) and event_id.strip():
            return event_id
    event_id = data.get("event_id")
    return event_id if isinstance(event_id, str) and event_id.strip() else None


class FeishuCallbackConfigError(RuntimeError):
    """The endpoint configuration cannot be used (structured, fail fast).

    Attributes:
        code: ``disabled_by_default`` / ``missing_token_ref`` /
            ``invalid_credential_ref`` / ``public_bind_refused`` /
            ``invalid_host`` / ``credential_unresolved``.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _validate_bind_host(host: str) -> None:
    """Refuse public bind addresses (仅内网;loopback/私网放行)."""
    if host == "localhost":
        return
    try:
        ip = ipaddress.ip_address(host)
    except ValueError as exc:
        raise FeishuCallbackConfigError(
            "invalid_host",
            f"回调端点绑定地址必须是 IP 或 'localhost',得到 {host!r}",
        ) from exc
    if not (ip.is_loopback or ip.is_private) or ip.is_unspecified:
        # is_unspecified(0.0.0.0/::)=监听所有网卡,等于公网暴露,同样拒绝。
        raise FeishuCallbackConfigError(
            "public_bind_refused",
            f"回调端点拒绝绑定公网/全网卡地址 {host}(安全基线:仅绑本机/内网,"
            "与 ingest API 同策略)",
        )


def _is_internal_ip(client_ip: str) -> bool:
    """Whether a peer address is loopback/private (unknown → 不判定)."""
    try:
        ip = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


@dataclass(frozen=True)
class FeishuCallbackConfig:
    """Endpoint configuration (default OFF at every layer).

    Args:
        enabled: master switch; ``False`` (default) makes the handler 404
            everything and the runner refuse to start.
        token_ref: ``env:``/``keychain:`` reference of the verification
            token; required when enabled, plaintext refused.
        host: bind address; loopback/private only (安全基线).
        port: bind port.

    Raises:
        FeishuCallbackConfigError: enabled without a token reference, a
            non-reference token value, or a public bind address.
    """

    enabled: bool = False
    token_ref: str | None = None
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    def __post_init__(self) -> None:
        if not self.enabled:
            return  # 默认关:未启用时不要求其余配置(但启动仍被 runner 拒绝)
        if not self.token_ref:
            raise FeishuCallbackConfigError(
                "missing_token_ref",
                "回调端点开启必须配置 token 引用(env:/keychain:),"
                f"如 {DEFAULT_TOKEN_ENV_REF}(开启即强制鉴权)",
            )
        try:
            parse_secret_value(self.token_ref, label="feishu_callback.token", allow_scheme=False)
        except Exception as exc:
            code = getattr(exc, "code", "invalid_credential_ref")
            raise FeishuCallbackConfigError(
                code, f"回调 token 引用非法(必须是纯 env:/keychain: 引用): {exc}"
            ) from exc
        _validate_bind_host(self.host)
        if not 0 <= self.port < 65536:
            raise FeishuCallbackConfigError(
                "invalid_port", f"回调端点端口必须在 0-65535(0 = 系统分配),得到 {self.port}"
            )

    def resolve_token(self) -> str:
        """Resolve the verification token (values never logged).

        Raises:
            FeishuCallbackConfigError: ``credential_unresolved``.
        """
        assert self.token_ref is not None  # __post_init__ 已保证(enabled 时)
        try:
            return resolve_credential(self.token_ref)
        except Exception as exc:
            raise FeishuCallbackConfigError(
                "credential_unresolved", f"回调 token 引用无法解析: {exc}"
            ) from exc


@dataclass(frozen=True)
class CallbackResponse:
    """One handled request: HTTP status + JSON payload + parsed callbacks."""

    status: int
    payload: dict[str, Any]
    callbacks: list[dict[str, str]] = field(default_factory=list)


class FeishuCallbackHandler:
    """Turns one ``(headers, body, client_ip)`` request into a response.

    Pure — no sockets, no store. The runner (``build_server``) owns the
    transport and feeds :attr:`~CallbackResponse.callbacks` into the
    feedback store; tests call :meth:`handle` directly (零真实网络).

    Args:
        config: endpoint configuration (default-off honored here: 404).
        token: pre-resolved verification token (tests inject); resolved from
            ``config`` at construction when omitted — an unresolvable
            reference fails here, before any traffic.
    """

    def __init__(self, config: FeishuCallbackConfig, *, token: str | None = None) -> None:
        self.config = config
        if token is not None:
            self._token = token
        elif config.enabled:
            self._token = config.resolve_token()
        else:
            self._token = ""  # 未启用:handle 首行即 404,鉴权路径不可达

    def handle(
        self,
        *,
        headers: Mapping[str, str],
        body: bytes,
        client_ip: str | None = None,
    ) -> CallbackResponse:
        """Handle one POST (auth → challenge → button value → callbacks).

        Status codes: 404 disabled / 403 public source / 401 auth missing or
        mismatch / 400 invalid JSON or callback value / 200 ok-or-ignored.
        """
        if not self.config.enabled:
            return CallbackResponse(
                404,
                {"error": "callback_disabled", "message": "飞书回调端点未开启(默认关闭)"},
            )
        if client_ip is not None and not _is_internal_ip(client_ip):
            logger.warning("回调来源非内网,已拒绝 client_ip=%s", client_ip)
            return CallbackResponse(
                403,
                {"error": "forbidden_source", "message": "回调仅接受内网来源(安全基线)"},
            )
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return CallbackResponse(
                400, {"error": "invalid_json", "message": f"请求体不是合法 JSON: {exc}"}
            )
        if not isinstance(data, Mapping):
            return CallbackResponse(
                400, {"error": "invalid_json", "message": "请求体必须是 JSON 对象"}
            )
        if not self._authorized(headers, data):
            logger.warning("回调鉴权失败(拒绝)")
            return CallbackResponse(
                401, {"error": "unauthorized", "message": "缺少或错误的回调 token"}
            )
        if data.get("type") == "url_verification":
            challenge = data.get("challenge")
            return CallbackResponse(
                200,
                {
                    "code": "url_verification",
                    "challenge": challenge if isinstance(challenge, str) else "",
                },
            )
        value = _extract_action_value(data)
        verdict_raw = (
            next((value[key] for key in _VALUE_KEYS if key in value), None)
            if value is not None
            else None
        )
        if value is None or verdict_raw is None:
            # 非反馈按钮回调(其它卡片事件/无判定键):ack 但不产反馈(Feishu
            # 语义要求 200,4xx 会触发平台重试风暴;reason 落在响应与日志里)。
            return CallbackResponse(
                200, {"code": "ignored", "reason": "非反馈按钮回调或缺少 value"}
            )
        dedup_key = next((value[key] for key in _ITEM_KEYS if key in value), None)
        try:
            verdict = normalize_verdict(verdict_raw)
        except ValueError as exc:
            return CallbackResponse(
                400, {"error": "invalid_callback", "message": str(exc)}
            )
        if not isinstance(dedup_key, str) or not dedup_key.strip():
            return CallbackResponse(
                400,
                {
                    "error": "invalid_callback",
                    "message": f"按钮 value 缺少条目身份(item/dedup_key),当前为 {dedup_key!r}",
                },
            )
        callback = {
            "channel": FEEDBACK_CHANNEL_FEISHU,
            "verdict": verdict,
            "dedup_key": dedup_key,
            # 幂等身份:飞书事件 id(重试推同事件只记一次;缺失时降级为 NULL)
            "external_id": _extract_event_id(data),
        }
        logger.info("飞书回调解析成功 verdict=%s dedup_key=%s", verdict, dedup_key)
        return CallbackResponse(200, {"code": "ok", "received": 1}, [callback])

    def _authorized(self, headers: Mapping[str, str], data: Mapping[str, Any]) -> bool:
        """Token check: our header OR Feishu's payload verification token."""
        provided = _header_value(headers, TOKEN_HEADER)
        if provided is None:
            payload_header = data.get("header")
            if isinstance(payload_header, Mapping) and isinstance(
                payload_header.get("token"), str
            ):
                provided = payload_header["token"]
            elif isinstance(data.get("token"), str):
                provided = data["token"]
        if not isinstance(provided, str) or not provided:
            return False
        # hmac.compare_digest 只收 bytes 或双方均为 ASCII 的 str:异常客户端
        # 携带非 ASCII token 时,str 直接比较会抛 TypeError(素材 11:未处理
        # 异常 → 500)。先统一 encode 成 bytes 再常数时间比较——任何编码侧
        # 失败都归入「未授权」,走既有结构化 401 路径,绝不裸抛。
        try:
            return hmac.compare_digest(provided.encode("utf-8"), self._token.encode("utf-8"))
        except (AttributeError, UnicodeEncodeError):
            return False


def _header_value(headers: Mapping[str, str], name: str) -> str | None:
    """Case-insensitive header lookup."""
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return value
    return None


def _extract_action_value(data: Mapping[str, Any]) -> dict[str, Any] | None:
    """Pull the button ``value`` dict out of either Feishu schema generation.

    Schema 2.0: ``event.action.form.value`` / ``event.action.value``; v1:
    ``action.value``. A stringified value is parsed (平台两种形态都出现过);
    anything that is not an object afterwards yields None.
    """
    event = data.get("event")
    action: Any = None
    if isinstance(event, Mapping):
        event_action = event.get("action")
        if isinstance(event_action, Mapping):
            form = event_action.get("form")
            if isinstance(form, Mapping):
                action = form.get("value")
            if action is None:
                action = event_action.get("value")
    if action is None:
        v1_action = data.get("action")
        if isinstance(v1_action, Mapping):
            action = v1_action.get("value")
    if isinstance(action, str) and action.strip():
        try:
            action = json.loads(action)
        except json.JSONDecodeError:
            return None
    return action if isinstance(action, Mapping) else None


# ---------------------------------------------------------------------------
# stdlib HTTP runner (服务端/compose 形态;核心依赖零新增)
# ---------------------------------------------------------------------------


def build_server(
    config: FeishuCallbackConfig,
    store: Store,
    *,
    handler: FeishuCallbackHandler | None = None,
) -> ThreadingHTTPServer:
    """Build (not start) the callback HTTP server on ``config.host:port``.

    Parsed callbacks are ingested into ``store`` per request (单条失败不拖垮
    端点:ingest 的失败语义见 :func:`shishi.feedback.ingest_callbacks`).

    Raises:
        FeishuCallbackConfigError: config invalid (public bind host etc.) or
            the token reference unresolvable.
    """
    handler = handler or FeishuCallbackHandler(config)

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server 命名约定
            length_raw = self.headers.get("Content-Length", "0")
            try:
                length = int(length_raw)
            except ValueError:
                length = 0
            body = self.rfile.read(length) if length > 0 else b""
            response = handler.handle(
                headers=dict(self.headers.items()), body=body, client_ip=self.client_address[0]
            )
            if response.callbacks:
                ingest_callbacks(store, response.callbacks)
            payload = json.dumps(response.payload, ensure_ascii=False).encode("utf-8")
            self.send_response(response.status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            logger.debug("feishu_callback http: " + format, *args)

    return ThreadingHTTPServer((config.host, config.port), _Handler)


def serve(
    config: FeishuCallbackConfig,
    store: Store,
    *,
    handler: FeishuCallbackHandler | None = None,
) -> None:
    """Run the endpoint until interrupted (compose 形态入口;先校验后监听)."""
    if not config.enabled:
        raise FeishuCallbackConfigError(
            "disabled_by_default",
            "飞书回调端点默认关闭;启动必须显式 --enable 并配置 token 引用",
        )
    server = build_server(config, store, handler=handler)
    host, port = server.server_address[:2]
    logger.info("飞书回调端点已监听 http://%s:%s(仅内网,token 鉴权)", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("收到中断信号,回调端点已停止")
    finally:
        server.server_close()


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m shishi.push.feishu_callback`` — 服务端/compose 回调入口."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m shishi.push.feishu_callback",
        description="飞书卡片回调端点(默认关闭;开启强制 token 鉴权 + 仅内网)",
    )
    parser.add_argument("--enable", action="store_true", help="显式开启端点(默认关闭)")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"绑定地址(默认 {DEFAULT_HOST},仅内网)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"绑定端口(默认 {DEFAULT_PORT})")
    parser.add_argument("--db", default="myia.db", help="SQLite 存储路径(默认 ./myia.db)")
    parser.add_argument(
        "--token-ref",
        default=DEFAULT_TOKEN_ENV_REF,
        help=f"验证 token 凭据引用(默认 {DEFAULT_TOKEN_ENV_REF})",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = FeishuCallbackConfig(
            enabled=args.enable, token_ref=args.token_ref, host=args.host, port=args.port
        )
        from shishi.store import SQLiteStore

        store = SQLiteStore(args.db)
    except FeishuCallbackConfigError as exc:
        print(
            json.dumps({"error": "feishu_callback", "code": exc.code, "message": str(exc)},
                       ensure_ascii=False),
        )
        return 1
    serve(config, store)
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
