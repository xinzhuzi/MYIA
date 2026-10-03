"""微信桥接通道:出站经本机 Hermes-Agent CLI(可选桥接,零 vendor)。

蓝本归属(10-03-messaging-weixin-bridge):调用形态移植自 Hermes
``hermes send`` 一次性路径(NousResearch/Hermes-Agent,MIT;上游路径
``~/.hermes/hermes-agent/``,CLI 契约见 ``hermes_cli/send_cmd.py`` 的
``cmd_send``/``_emit_result``——exit 0/1/2 三段 + ``--json`` 机器可读
应答,微信终态见 ``gateway/platforms/weixin.py`` 的
``send_weixin_direct``/``_session_not_ready_error``/``_send_text_chunk``)。
MYIA 只做子进程调用,不 vendor 任何上游代码(prd R5 红线):

- **桥接而非自实现**:个人微信唯一出站路径 = QR 登录的 iLink Bot API,
  每条出站须回显对端最新 ``context_token``,而该 token 只由常驻
  long-poll 处理入站时落盘——MYIA 自实现等于半个常驻服务,已否决。
  本机 Hermes gateway 常驻且已持 token,桥接它(prd 事实依据)。
- **调用形状**(design D0 定案①):argv =
  ``[hermes_bin, "send", "--to", "weixin:<peer>", "--json", "--file", "-"]``,
  正文经 stdin(argv 不携带情报内容,日志可安全打 argv;无长度/引号
  边界问题);环境变量原样继承,MYIA 不注入任何凭据。
- **显式可选**(prd R2):无 Hermes 环境 = 配置照常加载、发送返回
  ``bridge_unavailable``(code + 修复指引),UI 灰态如实披露,不装可用。
  探测(:func:`probe_bridge`)只看文件**存在性**,永不读内容。
- **零死信设计**(design D3):iLink 蓝本不存在 peer 级 forbidden/
  not_found 信号,按 prd R4 本通道任何错误都不标 dead——「对方须先发
  条消息」的 ``session_not_ready`` 是可修复瞬态,不是死信。
- **超时**::data:`DEFAULT_BRIDGE_TIMEOUT_SECONDS` = 120s 模块常量,
  不设配置口(上游内部重试上界 ≈ 105s:4 次重试 × (API 15s + 退避)
  + 分块间隔 1.5s,``weixin.py`` 的 ``send_chunk_retries``/delay 缺省);
  配置口只会诱导调出比上游重试更小的值产生伪超时。超时 → kill 兜底
  + ``bridge_timeout`` 瞬态。

寻址(design D1):``supports_targeting=True``;``context.target.chat_id``
优先,退回 legacy ``target`` 引用(缺省 :data:`DEFAULT_TARGET_ENV_REF`,
发送期解析)。对象 = 微信会话 peer id(``xxx@im.wechat`` DM /
``xxx@chatroom`` 群,蓝本 ``get_chat_info`` 形态依据);中文别名不匹配
直达正则 → 落目录四路径(别名层手工登记,prd R3)。目录无自动发现
(:class:`~shishi.push.directory.DirectoryDiscoverUnsupported`)。

正文:用户模板在场走 :class:`TemplateRenderer`,缺省内置纯文本版式
(与 ntfy ``build_message`` 同款);长文不预切——Hermes 侧 1800/2000
分块是它的本职。错误消息经 :func:`normalize_error_text` 归一后再抛,
防上游 HTTP 层文本误撞 core 死信分类器的 ASCII marker 表。

纯 stdlib(asyncio 子进程);测试经 ``runner`` 注入点 mock,不触真
Hermes(真实子进程冒烟仅两条:成功回显脚本 + sleep 超时 kill)。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from shishi.push.base import PushSendError, SendContext, TrendAwareChannel
from shishi.push.directory import DirectoryDiscoverUnsupported
from shishi.push.ntfy import build_message
from shishi.push.targets import RESOLVED_DIRECT, ChannelTarget
from shishi.push.templates import TemplateRenderError, TemplateRenderer
from shishi.schema import CredentialResolveError, resolve_credential

__all__ = [
    "DEFAULT_BRIDGE_TIMEOUT_SECONDS",
    "DEFAULT_HERMES_BIN",
    "DEFAULT_TARGET_ENV_REF",
    "WEIXIN_PEER_RE",
    "BridgeRunner",
    "BridgeStatus",
    "BridgeTimeoutError",
    "WeixinChannel",
    "normalize_error_text",
    "probe_bridge",
    "run_bridge_command",
]

logger = logging.getLogger(__name__)

#: 桥接子进程超时上限(秒;design D0:覆盖 Hermes 内部重试上界 ≈105s,
#: 不设配置口——只会诱导伪超时)。超时 → kill + ``bridge_timeout`` 瞬态。
DEFAULT_BRIDGE_TIMEOUT_SECONDS = 120.0
#: 本机 Hermes CLI 缺省路径(蓝本 launcher 实存形态;可用 push 节
#: ``weixin_hermes_bin`` 覆写)。
DEFAULT_HERMES_BIN = "~/.hermes/hermes-agent/.hermes/bin/hermes"
#: legacy peer id 引用缺省(发送期解析;与 ntfy/wecom 先例同构)。
DEFAULT_TARGET_ENV_REF = "env:WEIXIN_PEER_ID"
#: 直达 peer id 形态:``xxx@im.wechat``(DM)/ ``xxx@chatroom``(群),
#: 蓝本 ``get_chat_info`` 的 chat_id 判据;非命中(如中文别名)落目录。
WEIXIN_PEER_RE = re.compile(r"^[A-Za-z0-9_-]+@[A-Za-z0-9.-]+$")

#: runner 契约:async ``(argv, input_text, timeout) -> (exit_code, stdout, stderr)``。
#: 测试注入点;缺省 :func:`run_bridge_command`(asyncio 子进程)。
BridgeRunner = Callable[[Sequence[str], str, float], Awaitable["tuple[int, str, str]"]]

_HTTP_STATUS_RE = re.compile(r"HTTP[ \t]+(\d{3})", re.IGNORECASE)
#: ASCII 数字 → 全角(design D3 归一化的闭口:状态码数字也不留 ASCII 形,
#: 否则 ``HTTP 403`` 归一后残留的 ``403`` 仍撞 core 分类器裸 ``"403"``
#: forbidden marker,破坏零死信设计)。
_FULLWIDTH_DIGITS = str.maketrans("0123456789", "０１２３４５６７８９")


class BridgeTimeoutError(RuntimeError):
    """缺省 runner 的超时终态(子进程已 kill);通道层映射 ``bridge_timeout``。"""


@dataclass(frozen=True)
class BridgeStatus:
    """``probe_bridge`` 探测结果(sidecar ``bridge.status`` 原样外发)。

    纯文件系统探测,**永不读文件内容**(他人应用的私有数据边界):
    ``bin_found``/``weixin_configured``/``gateway_alive`` 都是存在性判据;
    ``gateway_alive`` 仅咨询信号(发送不依赖 gateway 在跑,但 token 续期
    依赖其轮询——不在场时冷发大概率吃 session 类错误)。
    """

    available: bool
    reason: str | None
    fix_hint: str | None
    bin_found: bool
    weixin_configured: bool
    gateway_alive: bool
    bin_path: str

    def to_payload(self) -> dict[str, Any]:
        """sidecar 应答形态(键序即字段序,协议测试对账用)。"""
        return asdict(self)


def normalize_error_text(text: str) -> str:
    """上游错误文本归一:``HTTP <ddd>`` → ``HTTP状态码<全角ddd>``。

    Hermes HTTP 层错误文本形如 ``iLink POST … HTTP 404: …``(蓝本
    ``_api_request``),会撞 core 分类器 ``"http 404"``(not_found→dead)
    与裸 ``"403"``(forbidden→dead)marker 误判死信;归一后该 span 内
    无 ASCII 数字/空格组合,不可能撞 ASCII marker 表(design D3 零死信
    设计的防误伤层)。上游文本的其余部分原样保留(诊断信息不丢)。
    """
    return _HTTP_STATUS_RE.sub(
        lambda m: "HTTP状态码" + m.group(1).translate(_FULLWIDTH_DIGITS), text
    )


def probe_bridge(hermes_bin: str | None = None) -> BridgeStatus:
    """探测本机 Hermes 桥接可用性(prd R2;纯存在性检查,零读取)。

    Args:
        hermes_bin: 覆写 bin 路径(配置 ``weixin_hermes_bin`` 的值);
            缺省 :data:`DEFAULT_HERMES_BIN`。

    判据(design D2):``bin_found`` = bin 是文件且可执行;
    ``weixin_configured`` = ``<hermes_home>/weixin/accounts/`` 下任一
    ``*.json``(QR 登录必写);``gateway_alive`` = gateway.sock 存在
    (仅咨询)。hermes_home = ``HERMES_HOME`` env(在场时)否则
    ``~/.hermes``(蓝本 ``get_hermes_home`` 同一序,MYIA 不带 profile
    机制)。``available = bin_found and weixin_configured``。
    """
    bin_path = Path(hermes_bin or DEFAULT_HERMES_BIN).expanduser()
    bin_found = bin_path.is_file() and os.access(bin_path, os.X_OK)
    home = Path(
        (os.environ.get("HERMES_HOME") or "").strip() or "~/.hermes"
    ).expanduser()
    weixin_configured = any((home / "weixin" / "accounts").glob("*.json"))
    gateway_alive = (home / "gateway.sock").exists()
    if not bin_found:
        reason, fix_hint = (
            "hermes_missing",
            (
                "安装 Hermes-Agent,或在本品类 push[].weixin_hermes_bin 配置其"
                " hermes bin 的路径(缺省 ~/.hermes/hermes-agent/.hermes/bin/hermes)"
            ),
        )
    elif not weixin_configured:
        reason, fix_hint = (
            "weixin_not_configured",
            (
                "在 Hermes 侧执行 hermes gateway setup 扫码登录微信"
                "(MYIA 不持有微信凭据,登录态只存在 Hermes 侧)"
            ),
        )
    else:
        reason = fix_hint = None
    return BridgeStatus(
        available=bin_found and weixin_configured,
        reason=reason,
        fix_hint=fix_hint,
        bin_found=bin_found,
        weixin_configured=weixin_configured,
        gateway_alive=gateway_alive,
        bin_path=str(bin_path),
    )


async def run_bridge_command(
    argv: Sequence[str], input_text: str, timeout: float
) -> tuple[int, str, str]:
    """缺省 runner:``asyncio.create_subprocess_exec`` + 超时 kill 兜底。

    正文经 stdin(UTF-8);环境原样继承(MYIA 不注入凭据)。超时 =
    :class:`BridgeTimeoutError`(先 kill 再 reap,不留孤儿进程);
    argv[0] 不存在 = ``FileNotFoundError``(通道层归 ``bridge_unavailable``)。
    """
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input_text.encode("utf-8")), timeout=timeout
        )
    except asyncio.TimeoutError as exc:
        with contextlib.suppress(ProcessLookupError):  # 已退出的竞态:kill 落空无害
            proc.kill()
        await proc.wait()
        raise BridgeTimeoutError(
            f"hermes send 超过 {timeout:.0f}s 未退出,子进程已终止"
        ) from exc
    return (
        proc.returncode if proc.returncode is not None else 0,
        stdout.decode("utf-8", "replace"),
        stderr.decode("utf-8", "replace"),
    )


class WeixinChannel(TrendAwareChannel):
    """``weixin`` channel:出站桥接本机 Hermes(每条消息一次 hermes send)。

    Args:
        target: peer id 的凭据引用(``env:WEIXIN_PEER_ID`` style,发送期
            解析);定向(``targets``)在场时可省(schema 允许),peer 由
            ``context.target`` 给出。
        template: 可选用户模板(Jinja2);在场时渲染输出为整条消息,
            省略 → 内置纯文本版式(ntfy ``build_message`` 同款)。
        renderer: 模板渲染器;缺省共享沙箱实例。
        hermes_bin: Hermes CLI 路径覆写(本地路径,非凭据);缺省
            :data:`DEFAULT_HERMES_BIN`。构造期只存字符串,零文件系统
            检查(prd R2:无 Hermes 环境配置加载零报错)。
        timeout: 子进程超时秒数(缺省 :data:`DEFAULT_BRIDGE_TIMEOUT_SECONDS`)。
        runner: 测试注入点(:data:`BridgeRunner` 契约);缺省真子进程。

    Raises:
        PushSendError: 桥接不可用(``bridge_unavailable`` + 修复指引)、
            会话未就绪(``session_not_ready``,可修复瞬态)、发送失败
            (``weixin_send_error``)、用法契约漂移(``bridge_usage_error``)、
            超时(``bridge_timeout``)或模板渲染失败。**全部不标 dead**
            (design D3 零死信:本通道无 forbidden/not_found 错误族)。
    """

    name = "weixin"
    #: 目录寻址已开(context.target 优先,legacy target 兜底)。
    supports_targeting = True

    def __init__(
        self,
        *,
        target: str | None = None,
        template: str | None = None,
        renderer: TemplateRenderer | None = None,
        hermes_bin: str | None = None,
        timeout: float = DEFAULT_BRIDGE_TIMEOUT_SECONDS,
        runner: BridgeRunner | None = None,
    ) -> None:
        self._target = target
        self._template = template
        self._renderer = renderer or TemplateRenderer()
        self._hermes_bin = hermes_bin
        self._timeout = timeout
        self._runner = runner

    async def send(self, items: Sequence[Any], context: SendContext) -> None:
        """Compose + one-shot ``hermes send`` 子进程;定向优先。

        Raises:
            PushSendError: on any bridge failure (callers isolate per
                channel; nothing is raised on success).
        """
        peer_id = self._resolve_peer(context)
        body = self._compose(items, context)
        bin_path = self._bin_path()
        # 发送期预检(prd R2:构造期零检查,探测只在发送期/UI 探测发生):
        # bin 缺失先于子进程失败,给结构化修复指引而非裸 FileNotFoundError。
        if not (bin_path.is_file() and os.access(bin_path, os.X_OK)):
            raise PushSendError(
                "bridge_unavailable",
                f"微信桥接不可用:Hermes CLI 不存在或不可执行: {bin_path}。"
                "修复指引:安装 Hermes-Agent,或在 push[].weixin_hermes_bin "
                "配置其 hermes bin 的路径",
            )
        argv = [
            str(bin_path),
            "send",
            "--to",
            f"weixin:{peer_id}",
            "--json",
            "--file",
            "-",
        ]
        runner = self._runner or run_bridge_command
        try:
            exit_code, stdout, stderr = await runner(argv, body, self._timeout)
        except BridgeTimeoutError as exc:
            raise PushSendError(
                "bridge_timeout",
                f"微信桥接超时:{exc}(Hermes 内部重试上界约 105s,超时即上游"
                "挂起;瞬态可重试)",
            ) from exc
        except FileNotFoundError as exc:
            raise PushSendError(
                "bridge_unavailable",
                f"微信桥接不可用:Hermes CLI 无法执行: {bin_path}"
                f"({type(exc).__name__}: {exc})。修复指引:安装 Hermes-Agent,"
                "或在 push[].weixin_hermes_bin 配置其 hermes bin 的路径",
            ) from exc
        self._raise_for_result(exit_code, stdout, stderr)
        logger.debug(
            "weixin 已桥接发送: slot=%s kind=%s count=%d peer=%s",
            context.slot,
            context.kind,
            len(items),
            peer_id,
        )

    # ------------------------------------------------------------- compose

    def _compose(self, items: Sequence[Any], context: SendContext) -> str:
        """消息文本:用户模板在场走渲染器,缺省内置纯文本版式。

        长文不预切——Hermes 侧 1800/2000 分块是它的本职(design D1)。
        """
        if self._template is not None:
            try:
                return self._renderer.render(
                    self._template, items, context, **self.trend_render_kwargs()
                )
            except TemplateRenderError as exc:
                raise PushSendError(
                    "template_render_error", f"push[].template 渲染失败: {exc}"
                ) from exc
        return build_message(items, context)

    def _bin_path(self) -> Path:
        return Path(self._hermes_bin or DEFAULT_HERMES_BIN).expanduser()

    def _resolve_peer(self, context: SendContext) -> str:
        """定向(``context.target.chat_id``)优先,退回 legacy target 引用。

        两条寻址路径均缺席 → ``missing_target``;解析值形态非法 →
        ``invalid_credential_ref``(peer 形态校验同时保证 argv 的
        ``--to weixin:<peer>`` 段不含 ``:``/空白等会破坏
        ``PLATFORM[:channel[:thread]]`` 解析的字符)。
        """
        if context.target is not None:
            value = context.target.chat_id.strip()
        elif self._target is not None:
            try:
                value = resolve_credential(self._target).strip()
            except CredentialResolveError as exc:
                raise PushSendError(exc.code, f"weixin target 解析失败: {exc}") from exc
        else:
            raise PushSendError(
                "missing_target",
                "weixin 两条寻址路径均缺席:未配置 legacy target(peer id 引用),"
                "本次发送也未携带 context.target",
            )
        if not WEIXIN_PEER_RE.fullmatch(value):
            raise PushSendError(
                "invalid_credential_ref",
                f"weixin peer id 形态非法(须为 xxx@im.wechat / xxx@chatroom): {value[:80]!r}",
            )
        return value

    # ------------------------------------------------ 错误映射(design D3 全表)

    def _raise_for_result(self, exit_code: int, stdout: str, stderr: str) -> None:
        """(exit_code, stdout JSON) → 成功放行 / 结构化 PushSendError。

        映射表见 design D3;上游文本一律先 :func:`normalize_error_text`
        归一再入消息与匹配(零死信的防误伤层)。任何分支都不产生
        forbidden/not_found 语义 → ``classify_dead_error`` 恒 None。
        """
        payload: Any = None
        try:
            payload = json.loads(stdout) if stdout.strip() else None
        except ValueError:
            payload = None
        upstream = normalize_error_text(
            str(payload.get("error"))
            if isinstance(payload, dict) and payload.get("error")
            else ""
        )
        if exit_code == 2:
            # 用法错 = MYIA argv 契约与 Hermes CLI 漂移,自家 bug,需介入。
            hint = normalize_error_text(stderr.strip())
            logger.error(
                "微信桥接用法错误(MYIA 与 Hermes CLI 契约漂移,需要介入):"
                " exit=2 stderr=%r",
                hint[:300],
            )
            raise PushSendError(
                "bridge_usage_error",
                f"微信桥接用法错误(argv 契约漂移,需要介入): {hint[:300]!r}",
            )
        if exit_code == 0 and not (isinstance(payload, dict) and payload.get("error")):
            # 上游契约:exit 0 ⟺ 无 error(skipped/success 均算送达路径)。
            return
        blob = upstream.lower()
        if not blob:
            raise PushSendError(
                "weixin_send_error",
                f"微信发送失败且无结构化错误文本: exit={exit_code}"
                f" stdout={stdout[:120]!r} stderr={normalize_error_text(stderr)[:160]!r}",
            )
        if "weixin token missing" in blob or "weixin account id missing" in blob:
            raise PushSendError(
                "bridge_unavailable",
                f"微信桥接不可用:Hermes 在场但微信未配置(上游错误: {upstream})。"
                "修复指引:在 Hermes 侧执行 hermes gateway setup 扫码登录微信",
            )
        if (
            "requirements not met" in blob
            or "need aiohttp" in blob
            or "adapter not available" in blob
        ):
            raise PushSendError(
                "bridge_unavailable",
                f"微信桥接不可用:Hermes 侧 weixin 适配器依赖未就绪(上游错误: {upstream})。"
                "修复指引:在 Hermes 侧执行 hermes pm repair",
            )
        if "session not ready" in blob or "ret=-14" in blob or "errcode=-14" in blob:
            raise PushSendError(
                "session_not_ready",
                f"微信会话未就绪:对端尚未给 bot 发过消息(或登录态已过期),iLink 要求"
                "先有入站才持有出站 context_token(上游错误: {upstream})。"
                "修复指引:先让对方给 bot 发一条消息,或在 Hermes 侧重新扫码配对",
            )
        if "rate limited" in blob:
            raise PushSendError(
                "weixin_send_error",
                f"微信发送失败(iLink 限流,Hermes 侧重试退避已尽;瞬态可重试): {upstream}",
            )
        raise PushSendError(
            "weixin_send_error",
            f"微信发送失败: {upstream}",
        )

    # --------------------------------------------- 目录(无自动发现)+ 直达

    async def discover_directory(self) -> list[Any]:
        """R3 定案:MYIA 不做自动发现,结构化说明而非假装刷新。

        peer id 的自助查询在 Hermes 侧(``hermes send --list weixin``);
        MYIA 侧别名可手工登记,直达写 ``weixin:<peer>``。
        """
        raise DirectoryDiscoverUnsupported(
            "weixin 无自动发现(R3 定案):peer id 可在 Hermes 侧执行"
            " `hermes send --list weixin` 自查;MYIA 侧别名手工登记,"
            "直达写 weixin:<peer>"
        )

    @classmethod
    def parse_direct_ref(cls, ref: str) -> ChannelTarget | None:
        """直达:``weixin:<peer>``(``xxx@im.wechat``/``xxx@chatroom``)不经目录。

        非命中形态(如中文别名)返回 None,调用方回落目录四路径——别名
        命名不受 peer 字符集限制。
        """
        value = ref.strip()
        if WEIXIN_PEER_RE.fullmatch(value):
            return ChannelTarget(
                platform="weixin", chat_id=value, resolved_from=RESOLVED_DIRECT
            )
        return None
